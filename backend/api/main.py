"""FastAPI application: lifespan tasks, optional maritime data routes, the /api entry point, error responses.

Run: uvicorn api.main:app
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .errors import ApiError
from .web.backup_job import automatic_backup_loop
from .web.handler import router as api_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(automatic_backup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if maritime_engine is not None:
            maritime_engine.dispose()


app = FastAPI(title="SEA THE ANSWER API", version=config.VERSION, lifespan=lifespan)

# Maritime data tool (backend/maritime_data, ADR 0008). Mounted only when its server-only
# bearer token is configured. Registered before the /api/{path} catch-all so it wins routing.
maritime_engine = None
if os.getenv("MARITIME_DATA_API_TOKEN"):
    from maritime_data.api import create_engine_for, create_router

    maritime_engine = create_engine_for(os.getenv("MARITIME_DATA_DATABASE_URL") or config.database_url())
    app.include_router(create_router(maritime_engine, os.environ["MARITIME_DATA_API_TOKEN"]))

app.include_router(api_router)


@app.exception_handler(ApiError)
async def api_error(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, _exc: psycopg.Error):
    return JSONResponse(status_code=503, content={"error": {"code": "DATA_SOURCE_UNAVAILABLE", "message": "PostgreSQL 요청에 실패했습니다."}},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, _exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}},
                        headers={"Cache-Control": "no-store"})
