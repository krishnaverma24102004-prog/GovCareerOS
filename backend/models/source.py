import uuid
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime
from datetime import datetime

REUSE_LEVELS = ("LIBERAL", "MODERATE", "CONSERVATIVE", "UNKNOWN")

class GovernmentSource(Base, TimestampMixin):
    __tablename__ = "government_sources"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    organization: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100))
    official_domain: Mapped[str] = mapped_column(String(255))
    recruitment_url: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    crawl_frequency: Mapped[int] = mapped_column(Integer, default=24)
    adapter_name: Mapped[Optional[str]] = mapped_column(String(255))
    # Expanded health status per requirements
    health_status: Mapped[str] = mapped_column(String(100), default="UNKNOWN") # HEALTHY, DEGRADED, ACCESS_RESTRICTED, etc.
    last_success: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    last_failure: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    last_verified: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    configuration: Mapped[Optional[str]] = mapped_column(Text)
    # "IN" for Union bodies, ISO 3166-2:IN code ("IN-HR") for state bodies; set from the registry by sync_registry
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    # Reuse terms of the source's content, set by a reviewer from the site's copyright policy page (never guessed)
    copyright_policy_url: Mapped[Optional[str]] = mapped_column(String(1000))
    reuse_level: Mapped[str] = mapped_column(String(20), default="UNKNOWN", server_default="UNKNOWN")  # see REUSE_LEVELS
    reuse_checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    
    endpoints: Mapped[List["SourceEndpoint"]] = relationship(back_populates="source")
    adapters: Mapped[List["SourceAdapterVersion"]] = relationship(back_populates="source")

class SourceEndpoint(Base, TimestampMixin):
    __tablename__ = "source_endpoints"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    method: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    fingerprint: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    source: Mapped["GovernmentSource"] = relationship(back_populates="endpoints")

class SourceAdapterVersion(Base, TimestampMixin):
    __tablename__ = "source_adapter_versions"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    discovery_method: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[Optional[str]] = mapped_column(String(1000))
    method: Mapped[Optional[str]] = mapped_column(String(10))
    schema_fingerprint: Mapped[Optional[str]] = mapped_column(String(255))
    endpoint_fingerprint: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(100))
    replacement_reason: Mapped[Optional[str]] = mapped_column(Text)
    
    source: Mapped["GovernmentSource"] = relationship(back_populates="adapters")
