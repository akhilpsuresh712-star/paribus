"""Orchestration: bounded concurrent fan-out, activation, report aggregation."""
from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

import httpx

from app.config import Settings
from app.logging_config import get_logger
from app.schemas import BatchReport, HospitalResult, HospitalRow, RowStatus
from app.store import BatchStore
from app.upstream import UpstreamClient, UpstreamError

logger = get_logger("hospital_bulk.service")


async def create_one(
    client: UpstreamClient,
    semaphore: asyncio.Semaphore,
    row: HospitalRow,
    batch_id: UUID,
    store: BatchStore,
) -> HospitalResult:
    """Create a single hospital, gated by the concurrency semaphore."""
    async with semaphore:
        try:
            hospital_id = await client.create_hospital(row, batch_id)
            result = HospitalResult(
                row=row.row,
                hospital_id=hospital_id,
                name=row.name,
                status=RowStatus.CREATED,  # promoted to *_and_activated after activation
            )
        except UpstreamError as exc:
            logger.warning("batch=%s row=%s create failed: %s", batch_id, row.row, exc)
            result = HospitalResult(
                row=row.row, name=row.name, status=RowStatus.FAILED, error=str(exc)
            )
    store.record_result(batch_id, result)
    return result


async def process_batch(
    rows: list[HospitalRow],
    settings: Settings,
    store: BatchStore,
    *,
    batch_id: UUID | None = None,
) -> BatchReport:
    """Run a full batch: concurrent create -> activate (per policy) -> report."""
    batch_id = batch_id or uuid4()
    store.create(batch_id, total=len(rows))
    store.record_rows(batch_id, rows)
    started = time.perf_counter()
    logger.info("batch=%s starting: %d row(s), concurrency=%d", batch_id, len(rows), settings.max_concurrency)

    semaphore = asyncio.Semaphore(settings.max_concurrency)
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    limits = httpx.Limits(max_connections=settings.max_concurrency)

    async with httpx.AsyncClient(
        base_url=settings.upstream_base_url, timeout=timeout, limits=limits
    ) as http:
        client = UpstreamClient(http, settings)

        results = await asyncio.gather(
            *(create_one(client, semaphore, row, batch_id, store) for row in rows)
        )

        failed = sum(1 for r in results if r.status == RowStatus.FAILED)
        created = [r for r in results if r.status != RowStatus.FAILED]

        # Activation policy: see config.activate_on_partial_failure.
        activated = False
        if created and (failed == 0 or settings.activate_on_partial_failure):
            try:
                await client.activate_batch(batch_id)
                activated = True
                for r in created:
                    r.status = RowStatus.CREATED_AND_ACTIVATED
            except UpstreamError as exc:
                # creates stand; rows stay "created", batch_activated stays False
                logger.warning("batch=%s activation failed: %s", batch_id, exc)

    store.set_activated(batch_id, activated)
    store.set_status(batch_id, "completed" if failed == 0 else "failed")
    # Re-record so the activation-promoted statuses land in the store too.
    for r in results:
        store.record_result(batch_id, r)

    results.sort(key=lambda r: r.row)
    elapsed = round(time.perf_counter() - started, 3)
    logger.info(
        "batch=%s done: created=%d failed=%d activated=%s in %.3fs",
        batch_id, len(created), failed, activated, elapsed,
    )

    return BatchReport(
        batch_id=batch_id,
        total_hospitals=len(rows),
        processed_hospitals=len(results),
        failed_hospitals=failed,
        processing_time_seconds=elapsed,
        batch_activated=activated,
        hospitals=results,
    )
