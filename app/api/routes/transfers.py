import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.api.auth import TransfersReader, TransfersWriter
from app.api.idempotency import IdempotencyKey, idempotency_request, to_response
from app.core.db import SessionDep
from app.models import Transfer
from app.schemas.transfer import TransferCreate, TransferRead
from app.services import transfers as transfer_service

router = APIRouter(prefix="/v1/transfers", tags=["transfers"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TransferRead)
async def create_transfer(
    body: TransferCreate,
    request: Request,
    session: SessionDep,
    principal: TransfersWriter,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Move money. The Idempotency-Key header is **required**: moving money is
    exactly the operation a client must be able to retry safely."""
    idempotency = idempotency_request(principal, idempotency_key, request, body)
    assert idempotency is not None  # key is required, so always present
    result = await transfer_service.create_transfer(
        session, body, initiated_by=principal.api_key_id, idempotency_request=idempotency
    )
    return to_response(result)


@router.get("/{transfer_id}", response_model=TransferRead)
async def get_transfer(transfer_id: uuid.UUID, session: SessionDep, _: TransfersReader) -> Transfer:
    return await transfer_service.get_transfer(session, transfer_id)
