from typing import Annotated

from pydantic import BaseModel, Field

Currency = Annotated[
    str, Field(pattern=r"^[A-Z]{3}$", examples=["INR"], description="ISO 4217 code")
]

# Amounts are integers in the currency's minor unit (e.g. paise). The upper bound
# keeps well inside BIGINT so summing many amounts can never overflow.
MAX_AMOUNT = 10**15
PositiveAmount = Annotated[
    int, Field(gt=0, le=MAX_AMOUNT, examples=[10_000], description="Minor units, e.g. paise")
]


class Page[T](BaseModel):
    data: list[T]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to fetch the next page; null on the last page."
    )
