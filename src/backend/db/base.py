"""
SQLAlchemy declarative base and shared mixins.

Design decisions:
- UUIDs as primary keys (uuid7-style via Python uuid4 for now; sortable UUIDs
  are a future optimization if needed).
- Every table inherits TimestampMixin so created_at / updated_at are guaranteed.
- server_default=func.now() means the DB sets the value even for raw SQL inserts.
- onupdate=func.now() keeps updated_at accurate even for partial ORM updates.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base. All models must inherit from this."""
    pass


class TimestampMixin:
    """
    Adds created_at and updated_at to any model.

    - server_default uses DB-side NOW() so raw SQL inserts are also covered.
    - onupdate is an ORM-level hook (good enough for 99% of our use cases).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """
    Adds a UUID primary key to any model.

    We generate UUIDs in Python (not the DB) so we can know the ID before
    inserting — essential for async batch inserts and idempotency checks.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
