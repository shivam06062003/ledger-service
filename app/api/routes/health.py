import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import SessionDep

router = APIRouter(prefix="/health", tags=["health"])
logger = structlog.get_logger()


@router.get("/live")
async def live() -> dict[str, str]:
    """Liveness: the process is up. Never checks dependencies, so a database
    outage doesn't make an orchestrator restart healthy API containers."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep) -> dict[str, str]:
    """Readiness: we can serve traffic, which requires a working database.
    Load balancers stop routing to an instance while this returns 503."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("readiness_check_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        ) from exc
    return {"status": "ok", "database": "ok"}
