# Import every model module here so Alembic's autogenerate sees all tables
# when it loads Base.metadata.
from app.models.base import Base

__all__ = ["Base"]
