"""Business-rule errors. Deliberately HTTP-agnostic: the API layer decides which
status code each one maps to, so services can be reused from workers or CLIs."""

import uuid


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AccountNotFound(DomainError):
    code = "account_not_found"

    def __init__(self, account_id: uuid.UUID) -> None:
        super().__init__(f"Account {account_id} not found")


class TransferNotFound(DomainError):
    code = "transfer_not_found"

    def __init__(self, transfer_id: uuid.UUID) -> None:
        super().__init__(f"Transfer {transfer_id} not found")


class InsufficientFunds(DomainError):
    code = "insufficient_funds"

    def __init__(self, account_id: uuid.UUID) -> None:
        super().__init__(f"Account {account_id} has insufficient funds")


class CurrencyMismatch(DomainError):
    code = "currency_mismatch"

    def __init__(self, account_id: uuid.UUID, account_currency: str, currency: str) -> None:
        super().__init__(
            f"Account {account_id} holds {account_currency}, cannot transfer {currency}"
        )


class SameAccountTransfer(DomainError):
    code = "same_account_transfer"

    def __init__(self) -> None:
        super().__init__("Source and destination accounts must differ")
