from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func
from sqlalchemy.types import TypeDecorator

class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime stored as UTC.

    PostgreSQL keeps the offset itself, but SQLite silently drops it; normalising to UTC
    on write and re-attaching UTC on read keeps values identical on both backends.
    Naive datetimes are rejected so a missing timezone never slips through.
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime {value!r}; attach a timezone")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
