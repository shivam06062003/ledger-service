# Import every model module here so Alembic's autogenerate sees all tables
# when it loads Base.metadata.
from app.models.account import Account
from app.models.base import Base
from app.models.transfer import Entry, Transfer

__all__ = ["Account", "Base", "Entry", "Transfer"]
