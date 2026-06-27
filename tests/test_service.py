import httpx
import pytest
import respx

from app.config import Settings
from app.schemas import HospitalRow, RowStatus
from app.service import process_batch
from app.store import BatchStore

BASE = "https://upstream.test"


def settings(**kw) -> Settings:
    defaults = dict(
        upstream_base_url=BASE,
        max_concurrency=5,
        request_timeout_seconds=5,
        max_retries=2,
        backoff_base_seconds=0,
        max_rows=20,
        activate_on_partial_failure=False,
    )
    defaults.update(kw)
    return Settings(**defaults)


def _rows(n: int) -> list[HospitalRow]:
    return [HospitalRow(row=i + 1, name=f"H{i+1}", address="addr") for i in range(n)]


@pytest.mark.asyncio
@respx.mock
async def test_all_success_activates():
    respx.post(f"{BASE}/hospitals/").mock(
        side_effect=lambda req: httpx.Response(200, json={"id": 1})
    )
    activate = respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    report = await process_batch(_rows(3), settings(), BatchStore())
    assert report.processed_hospitals == 3
    assert report.failed_hospitals == 0
    assert report.batch_activated is True
    assert all(r.status == RowStatus.CREATED_AND_ACTIVATED for r in report.hospitals)
    assert activate.called


@pytest.mark.asyncio
@respx.mock
async def test_partial_failure_strict_skips_activation():
    def handler(request):
        body = request.content.decode()
        if "H2" in body:
            return httpx.Response(400, json={"detail": "bad"})
        return httpx.Response(200, json={"id": 9})

    respx.post(f"{BASE}/hospitals/").mock(side_effect=handler)
    activate = respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate")

    report = await process_batch(_rows(3), settings(), BatchStore())
    assert report.failed_hospitals == 1
    assert report.batch_activated is False
    assert not activate.called
    statuses = {r.row: r.status for r in report.hospitals}
    assert statuses[2] == RowStatus.FAILED
    assert statuses[1] == RowStatus.CREATED


@pytest.mark.asyncio
@respx.mock
async def test_partial_failure_lenient_activates():
    def handler(request):
        if "H2" in request.content.decode():
            return httpx.Response(400)
        return httpx.Response(200, json={"id": 9})

    respx.post(f"{BASE}/hospitals/").mock(side_effect=handler)
    respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    report = await process_batch(_rows(3), settings(activate_on_partial_failure=True), BatchStore())
    assert report.batch_activated is True
    assert report.failed_hospitals == 1


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_transient_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": 7})

    respx.post(f"{BASE}/hospitals/").mock(side_effect=handler)
    respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    report = await process_batch(_rows(1), settings(max_retries=3), BatchStore())
    assert report.failed_hospitals == 0
    assert calls["n"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_total_upstream_failure():
    respx.post(f"{BASE}/hospitals/").mock(return_value=httpx.Response(500))
    report = await process_batch(_rows(2), settings(), BatchStore())
    assert report.failed_hospitals == 2
    assert report.batch_activated is False
    assert all(r.status == RowStatus.FAILED for r in report.hospitals)
