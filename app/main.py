"""FastAPI app + route wiring for the Hospital Bulk Processing System."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.config import get_settings
from app.csv_parser import CSVValidationError, parse_csv
from app.logging_config import configure_logging, get_logger
from app.schemas import (
    BatchReport,
    BatchStatus,
    HospitalResult,
    RowStatus,
    ValidationResponse,
)
from app.service import process_batch
from app.store import store

configure_logging(get_settings().log_level)
logger = get_logger("hospital_bulk.api")

app = FastAPI(
    title="Hospital Bulk Processing System",
    description="Bulk-ingest service fronting the Hospital Directory API: "
    "CSV upload, bounded concurrent fan-out, batch activation, per-row report.",
    version="1.0.0",
)


@app.get("/", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "hospital-bulk"}


async def read_and_parse(file: UploadFile):
    settings = get_settings()
    raw = await file.read()
    try:
        return parse_csv(raw, max_rows=settings.max_rows)
    except CSVValidationError as exc:
        logger.warning("Rejected upload '%s': %s", file.filename, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/hospitals/bulk", response_model=BatchReport, tags=["bulk"])
async def bulk_create(file: UploadFile = File(...)) -> BatchReport:
    """Parse the CSV, create every hospital concurrently, activate, return the report."""
    settings = get_settings()
    rows, row_errors = await read_and_parse(file)

    report = await process_batch(rows, settings, store)

    # Rows that failed CSV validation never reach the upstream; report them too.
    for err in row_errors:
        report.hospitals.append(
            HospitalResult(row=err.row, name="", status=RowStatus.FAILED, error=err.error)
        )
    report.hospitals.sort(key=lambda r: r.row)
    report.total_hospitals += len(row_errors)
    report.failed_hospitals += len(row_errors)
    return report


@app.post("/hospitals/bulk/async", status_code=202, tags=["bulk"])
async def bulk_create_async(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
) -> dict:
    """Start the batch in the background and return its id; track via polling or WS."""
    settings = get_settings()
    rows, _ = await read_and_parse(file)

    batch_id = uuid4()
    store.create(batch_id, total=len(rows))
    background_tasks.add_task(process_batch, rows, settings, store, batch_id=batch_id)
    return {"batch_id": str(batch_id), "status": "accepted", "total": len(rows)}


def status_of(batch_id: UUID) -> BatchStatus | None:
    state = store.get(batch_id)
    if state is None:
        return None
    return BatchStatus(
        batch_id=state.batch_id,
        status=state.status,
        total=state.total,
        processed=state.processed,
        failed=state.failed,
        activated=state.activated,
    )


@app.get("/hospitals/bulk/{batch_id}", response_model=BatchStatus, tags=["bulk"])
async def bulk_status(batch_id: UUID) -> BatchStatus:
    """Progress for a batch (polling)."""
    status = status_of(batch_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown batch_id.")
    return status


@app.websocket("/hospitals/bulk/{batch_id}/ws")
async def bulk_progress_ws(websocket: WebSocket, batch_id: UUID) -> None:
    """Stream progress frames until the batch finishes, then close."""
    await websocket.accept()
    status = status_of(batch_id)
    if status is None:
        await websocket.send_json({"error": "Unknown batch_id."})
        await websocket.close(code=1008)
        return

    last: str | None = None
    try:
        while True:
            status = status_of(batch_id)
            if status is None:
                break
            snapshot = status.model_dump_json()
            if snapshot != last:
                await websocket.send_text(snapshot)
                last = snapshot
            if status.status in ("completed", "failed"):
                break
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
    await websocket.close()


@app.post("/hospitals/bulk/validate", response_model=ValidationResponse, tags=["bulk"])
async def bulk_validate(file: UploadFile = File(...)) -> ValidationResponse:
    """Validate a CSV without creating anything upstream."""
    rows, row_errors = await read_and_parse(file)
    return ValidationResponse(
        valid=len(row_errors) == 0,
        total_rows=len(rows) + len(row_errors),
        valid_rows=len(rows),
        invalid_rows=len(row_errors),
        rows=rows,
        errors=row_errors,
    )


@app.post("/hospitals/bulk/{batch_id}/resume", response_model=BatchReport, tags=["bulk"])
async def bulk_resume(batch_id: UUID) -> BatchReport:
    """Retry just the failed rows of an earlier batch, reusing its stored input."""
    state = store.get(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown batch_id.")

    failed_rows = [
        state.rows[r.row]
        for r in state.results.values()
        if r.status == RowStatus.FAILED and r.row in state.rows
    ]
    if not failed_rows:
        raise HTTPException(status_code=400, detail="No failed rows to resume for this batch.")

    settings = get_settings()
    return await process_batch(failed_rows, settings, store, batch_id=batch_id)
