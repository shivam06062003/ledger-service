import uuid

import structlog
from fastapi import APIRouter, status

from app.api.auth import Admin
from app.core.db import SessionDep
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated
from app.services import api_keys as api_key_service

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])
logger = structlog.get_logger()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(body: ApiKeyCreate, session: SessionDep, _: Admin) -> ApiKeyCreated:
    """Issue a key for a calling service. The secret is returned only in this response."""
    created = await api_key_service.create_api_key(session, body)
    logger.info("api_key_created", new_api_key_id=str(created.id), scopes=created.scopes)
    return created


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(api_key_id: uuid.UUID, session: SessionDep, _: Admin) -> None:
    await api_key_service.revoke_api_key(session, api_key_id)
    logger.info("api_key_revoked", revoked_api_key_id=str(api_key_id))
