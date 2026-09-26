import uuid

from fastapi import APIRouter, status

from app.core.db import SessionDep
from app.models import Transfer
from app.schemas.transfer import TransferCreate, TransferRead
from app.services import transfers as transfer_service

router = APIRouter(prefix="/v1/transfers", tags=["transfers"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TransferRead)
async def create_transfer(body: TransferCreate, session: SessionDep) -> Transfer:
    return await transfer_service.create_transfer(session, body)


@router.get("/{transfer_id}", response_model=TransferRead)
async def get_transfer(transfer_id: uuid.UUID, session: SessionDep) -> Transfer:
    return await transfer_service.get_transfer(session, transfer_id)
