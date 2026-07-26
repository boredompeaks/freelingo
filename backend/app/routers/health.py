from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import get_redis, require_admin
from app.core.limiter import limiter
from app.models.user import User

router = APIRouter(tags=["health"])


@router.get("/health")
@limiter.limit("60/minute")
async def health(request: Request) -> JSONResponse:  # noqa: ARG001
    return JSONResponse({"status": "ok"})


@router.get("/health/ready")
@limiter.limit("60/minute")
async def health_ready(
    request: Request,  # noqa: ARG001
    redis: object = Depends(get_redis),
) -> JSONResponse:
    checks: dict[str, str] = {}
    ready = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        ready = False

    try:
        if hasattr(redis, "ping"):
            await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        ready = False

    status_code = 200 if ready else 503
    return JSONResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks},
        status_code=status_code,
    )


@router.get("/api/admin/health")
@limiter.limit("60/minute")
async def admin_health(
    request: Request,
    _admin: User = Depends(require_admin),
    redis: object = Depends(get_redis),
) -> JSONResponse:
    checks: dict[str, str] = {}
    ok = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        ok = False

    try:
        if hasattr(redis, "ping"):
            await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        ok = False

    try:
        tts = getattr(request.app.state, "tts_service", None)
        if tts is not None:
            await tts.health()
        checks["tts"] = "ok"
    except Exception as exc:
        checks["tts"] = f"error: {exc}"
        ok = False

    try:
        stt = getattr(request.app.state, "stt_service", None)
        if stt is not None:
            await stt.health()
        checks["stt"] = "ok"
    except Exception as exc:
        checks["stt"] = f"error: {exc}"
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status_code=status_code,
    )
