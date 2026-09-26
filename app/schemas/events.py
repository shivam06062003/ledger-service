from enum import StrEnum


class EventType(StrEnum):
    ACCOUNT_CREATED = "account.created"
    TRANSFER_CREATED = "transfer.created"
