"""Async client for the upstream Hospital Directory API.

One shared httpx.AsyncClient per batch (connection pooling / keep-alive), with
per-call timeout and exponential-backoff retry to absorb Render cold starts and
transient 502/503s.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import httpx

from app.config import Settings
from app.logging_config import get_logger
from app.schemas import HospitalRow

logger = get_logger("hospital_bulk.upstream")

# Transient upstream statuses worth retrying (cold start / gateway hiccups).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class UpstreamError(Exception):
    """Non-retryable or retries-exhausted failure for a single upstream call."""


class UpstreamClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def create_hospital(self, row: HospitalRow, batch_id: UUID) -> int:
        """Create one hospital under batch_id. Returns the new hospital id."""
        payload = {
            "name": row.name,
            "address": row.address,
            "phone": row.phone,
            "creation_batch_id": str(batch_id),
        }
        data = await self.request_with_retry("POST", "/hospitals/", json=payload)
        hospital_id = (data or {}).get("id")
        if hospital_id is None:
            raise UpstreamError("Upstream create returned no 'id'.")
        return int(hospital_id)

    async def activate_batch(self, batch_id: UUID) -> None:
        await self.request_with_retry("PATCH", f"/hospitals/batch/{batch_id}/activate")

    async def delete_batch(self, batch_id: UUID) -> None:
        await self.request_with_retry("DELETE", f"/hospitals/batch/{batch_id}")

    async def request_with_retry(self, method: str, path: str, *, json: dict | None = None):
        """Issue a request, retrying transient failures with exponential backoff.

        Returns parsed JSON (or None when the body is empty). Raises UpstreamError
        on a 4xx (non-retryable) or after exhausting retries.
        """
        last_exc: Exception | None = None

        for attempt in range(self.settings.max_retries):
            try:
                resp = await self.client.request(method, path, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = UpstreamError(f"{method} {path} network error: {exc}")
            else:
                if resp.status_code in RETRYABLE_STATUS:
                    last_exc = UpstreamError(
                        f"{method} {path} -> {resp.status_code} (transient)"
                    )
                elif resp.is_success:
                    if not resp.content:
                        return None
                    try:
                        return resp.json()
                    except ValueError:
                        return None
                else:
                    # 4xx (bad row, etc.) — not worth retrying.
                    raise UpstreamError(
                        f"{method} {path} -> {resp.status_code}: {resp.text[:200]}"
                    )

            # Backoff before the next attempt (skip after the final try).
            if attempt < self.settings.max_retries - 1:
                delay = self.settings.backoff_base_seconds * (2**attempt)
                logger.warning(
                    "%s %s attempt %d/%d failed (%s); retrying in %.2fs",
                    method, path, attempt + 1, self.settings.max_retries, last_exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_exc or UpstreamError(f"{method} {path} failed.")
