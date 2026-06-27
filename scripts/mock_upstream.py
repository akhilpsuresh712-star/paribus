"""A throwaway mock of the Hospital Directory API for the resume demo.

It has a global mode:
  - "fail" (default): every create returns 500  -> the whole bulk fails
  - "ok":             every create returns 200  -> resume succeeds

Flip the mode with:  POST /admin/mode/ok   (or /admin/mode/fail)
This is robust to the main app's retry count: in fail mode *every* attempt fails,
so retries can't accidentally turn a failure into a success.

Run it on port 9000:
    python -m uvicorn scripts.mock_upstream:app --port 9000
"""
from __future__ import annotations

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock Upstream")

state = {"mode": "fail"}
next_id = {"v": 1000}


@app.post("/admin/mode/{mode}")
async def set_mode(mode: str):
    state["mode"] = "ok" if mode == "ok" else "fail"
    return {"mode": state["mode"]}


@app.post("/hospitals/")
async def create(request: Request):
    body = await request.json()
    if state["mode"] == "fail":
        return Response(status_code=500, content="injected failure (mock in fail mode)")
    next_id["v"] += 1
    return {"id": next_id["v"], "name": body.get("name", ""), "active": False}


@app.patch("/hospitals/batch/{batch_id}/activate")
async def activate(batch_id: str):
    return {}


@app.delete("/hospitals/batch/{batch_id}")
async def delete_batch(batch_id: str):
    return {}
