"""In-memory batch status store for polling / background / resume.

Single-process and lost on restart; the interface is small so it can be backed by
Redis/Postgres later without touching the orchestration layer.
"""
from __future__ import annotations

import threading
from uuid import UUID

from app.schemas import HospitalResult, HospitalRow


class BatchState:
    def __init__(self, batch_id: UUID, total: int):
        self.batch_id = batch_id
        self.total = total
        self.processed = 0
        self.failed = 0
        self.activated = False
        self.status = "running"  # running | completed | failed
        self.results: dict[int, HospitalResult] = {}  # keyed by row number
        self.rows: dict[int, HospitalRow] = {}  # original input, for resume


class BatchStore:
    def __init__(self) -> None:
        self._batches: dict[UUID, BatchState] = {}
        self._lock = threading.Lock()

    def create(self, batch_id: UUID, total: int) -> BatchState:
        with self._lock:
            state = BatchState(batch_id, total)
            # Resume reuses the batch_id, so carry over prior rows/results.
            existing = self._batches.get(batch_id)
            if existing is not None:
                state.rows = existing.rows
                state.results = existing.results
            self._batches[batch_id] = state
            return state

    def record_rows(self, batch_id: UUID, rows: list[HospitalRow]) -> None:
        with self._lock:
            state = self._batches.get(batch_id)
            if state is None:
                return
            for row in rows:
                state.rows.setdefault(row.row, row)

    def get(self, batch_id: UUID) -> BatchState | None:
        with self._lock:
            return self._batches.get(batch_id)

    def record_result(self, batch_id: UUID, result: HospitalResult) -> None:
        with self._lock:
            state = self._batches.get(batch_id)
            if state is None:
                return
            state.results[result.row] = result
            # Recompute from results so a resumed row updates counts in place.
            state.processed = len(state.results)
            state.failed = sum(1 for r in state.results.values() if r.status == "failed")

    def set_activated(self, batch_id: UUID, activated: bool) -> None:
        with self._lock:
            state = self._batches.get(batch_id)
            if state:
                state.activated = activated

    def set_status(self, batch_id: UUID, status: str) -> None:
        with self._lock:
            state = self._batches.get(batch_id)
            if state:
                state.status = status


store = BatchStore()
