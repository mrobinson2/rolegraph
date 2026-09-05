"""SQLAlchemy models.

Every import is a snapshot. Entities carry a ``snapshot_id``, so history and
snapshot-to-snapshot diffing can be added later without a schema migration.

SQLite is the default engine; nothing here is SQLite-specific, so moving to
PostgreSQL is a connection-string change.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tenant_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    skipped: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    warnings: Mapped[list["ImportWarningRow"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    @property
    def total_records(self) -> int:
        return sum((self.counts or {}).values())

    @property
    def total_skipped(self) -> int:
        return sum((self.skipped or {}).values())


class ScopeRow(Base):
    __tablename__ = "scopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(1024))
    raw_scope: Mapped[str] = mapped_column(String(1024))
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    parent_scope: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PrincipalRow(Base):
    __tablename__ = "principals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    principal_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(512))
    type: Mapped[str] = mapped_column(String(32))
    upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RoleDefinitionRow(Base):
    __tablename__ = "role_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    role_id: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_type: Mapped[str] = mapped_column(String(32))
    assignable_scopes: Mapped[list] = mapped_column(JSON, default=list)
    permissions: Mapped[list] = mapped_column(JSON, default=list)


class RoleAssignmentRow(Base):
    __tablename__ = "role_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    assignment_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str] = mapped_column(String(255))
    role_definition_id: Mapped[str] = mapped_column(String(512))
    scope: Mapped[str] = mapped_column(String(1024))
    raw_scope: Mapped[str] = mapped_column(String(1024))
    principal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_on: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class GroupMembershipRow(Base):
    __tablename__ = "group_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    group_id: Mapped[str] = mapped_column(String(255))
    member_id: Mapped[str] = mapped_column(String(255))


class ImportWarningRow(Base):
    __tablename__ = "import_warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    record_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")

    snapshot: Mapped[Snapshot] = relationship(back_populates="warnings")


class AuditLogRow(Base):
    """Administrative actions. Imports and snapshot activation are recorded here."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


for _model, _column in (
    (ScopeRow, "scope"),
    (PrincipalRow, "principal_id"),
    (RoleDefinitionRow, "role_id"),
    (RoleAssignmentRow, "principal_id"),
    (GroupMembershipRow, "member_id"),
    (ImportWarningRow, "category"),
):
    Index(
        f"ix_{_model.__tablename__}_snapshot_{_column}",
        _model.snapshot_id,
        getattr(_model, _column),
    )
