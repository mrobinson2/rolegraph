"""Persisting and reloading snapshots.

The resolver works on an in-memory :class:`AccessGraph`. This module is the only
place that translates between that and the database.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..domain.entities import (
    AccessGraph,
    GroupMembership,
    ImportWarning,
    Permission,
    Principal,
    PrincipalType,
    RoleAssignment,
    RoleDefinition,
    ScopeNode,
    Tenant,
)
from ..domain.ids import ScopeKind
from ..importer.importer import ImportResult
from .db import session_scope
from .models import (
    AuditLogRow,
    GroupMembershipRow,
    ImportWarningRow,
    PrincipalRow,
    RoleAssignmentRow,
    RoleDefinitionRow,
    ScopeRow,
    Snapshot,
)

#: Loaded graphs, keyed by snapshot id. Snapshots are immutable once written,
#: so a plain dict is a safe cache. Bounded because imports are operator-driven.
_GRAPH_CACHE: dict[int, AccessGraph] = {}
_CACHE_LIMIT = 8


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    id: int
    source_name: str
    created_at: str
    tenant_name: str | None
    counts: dict
    skipped: dict
    warning_count: int
    error_count: int
    is_active: bool

    @property
    def total_records(self) -> int:
        return sum(self.counts.values())


def audit(session: Session, action: str, detail: str = "", snapshot_id: int | None = None) -> None:
    session.add(AuditLogRow(action=action, detail=detail, snapshot_id=snapshot_id))


def save_import(result: ImportResult, activate: bool = True) -> int:
    """Write an import result as a new snapshot. Returns the snapshot id."""
    graph = result.graph
    with session_scope() as session:
        snapshot = Snapshot(
            source_name=result.source_name,
            checksum=result.checksum,
            tenant_id=graph.tenant.id if graph.tenant else None,
            tenant_name=graph.tenant.display_name if graph.tenant else None,
            tenant_domain=graph.tenant.domain if graph.tenant else None,
            counts=dict(result.counts),
            skipped=dict(result.skipped),
            is_active=False,
        )
        session.add(snapshot)
        session.flush()
        sid = snapshot.id

        session.add_all(
            ScopeRow(
                snapshot_id=sid,
                scope=node.scope,
                raw_scope=node.raw_scope,
                kind=node.kind.value,
                name=node.name,
                display_name=node.display_name,
                parent_scope=node.parent_scope,
                resource_type=node.resource_type,
                location=node.location,
            )
            for node in graph.scopes.values()
        )
        session.add_all(
            PrincipalRow(
                snapshot_id=sid,
                principal_id=p.id,
                display_name=p.display_name,
                type=p.type.value,
                upn=p.upn,
                email=p.email,
                department=p.department,
                app_id=p.app_id,
                description=p.description,
            )
            for p in graph.principals.values()
        )
        session.add_all(
            RoleDefinitionRow(
                snapshot_id=sid,
                role_id=r.id,
                name=r.name,
                description=r.description,
                role_type=r.role_type,
                assignable_scopes=list(r.assignable_scopes),
                permissions=[
                    {
                        "actions": list(p.actions),
                        "notActions": list(p.not_actions),
                        "dataActions": list(p.data_actions),
                        "notDataActions": list(p.not_data_actions),
                    }
                    for p in r.permissions
                ],
            )
            for r in graph.role_definitions.values()
        )
        session.add_all(
            RoleAssignmentRow(
                snapshot_id=sid,
                assignment_id=ra.id,
                principal_id=ra.principal_id,
                role_definition_id=ra.role_definition_id,
                scope=ra.scope,
                raw_scope=ra.raw_scope,
                principal_type=ra.principal_type.value if ra.principal_type else None,
                created_on=ra.created_on,
                created_by=ra.created_by,
                description=ra.description,
            )
            for ra in graph.role_assignments
        )
        session.add_all(
            GroupMembershipRow(snapshot_id=sid, group_id=m.group_id, member_id=m.member_id)
            for m in graph.memberships
        )
        session.add_all(
            ImportWarningRow(
                snapshot_id=sid,
                category=w.category,
                message=w.message,
                record_type=w.record_type,
                record_id=w.record_id,
                severity=w.severity,
            )
            for w in result.warnings
        )
        audit(
            session,
            "import",
            f"Imported {result.total_records} records from {result.source_name} "
            f"({result.total_skipped} skipped, {len(result.warnings)} warnings).",
            sid,
        )
        if activate:
            session.execute(
                Snapshot.__table__.update().values(is_active=False).where(Snapshot.is_active.is_(True))
            )
            snapshot.is_active = True
            audit(session, "activateSnapshot", f"Snapshot {sid} became the active dataset.", sid)
    _GRAPH_CACHE.pop(sid, None)
    return sid


def activate_snapshot(snapshot_id: int) -> None:
    with session_scope() as session:
        session.execute(
            Snapshot.__table__.update().values(is_active=False).where(Snapshot.is_active.is_(True))
        )
        snapshot = session.get(Snapshot, snapshot_id)
        if snapshot is None:
            raise LookupError(f"Snapshot {snapshot_id} does not exist.")
        snapshot.is_active = True
        audit(session, "activateSnapshot", f"Snapshot {snapshot_id} became the active dataset.", snapshot_id)


def delete_snapshot(snapshot_id: int) -> None:
    with session_scope() as session:
        for model in (
            ScopeRow,
            PrincipalRow,
            RoleDefinitionRow,
            RoleAssignmentRow,
            GroupMembershipRow,
            ImportWarningRow,
        ):
            session.execute(delete(model).where(model.snapshot_id == snapshot_id))
        snapshot = session.get(Snapshot, snapshot_id)
        if snapshot is not None:
            session.delete(snapshot)
        audit(session, "deleteSnapshot", f"Snapshot {snapshot_id} was deleted.", snapshot_id)
    _GRAPH_CACHE.pop(snapshot_id, None)


def _summarise(session: Session, snapshot: Snapshot) -> SnapshotSummary:
    counts = dict(
        session.execute(
            select(ImportWarningRow.severity, func.count())
            .where(ImportWarningRow.snapshot_id == snapshot.id)
            .group_by(ImportWarningRow.severity)
        ).all()
    )
    return SnapshotSummary(
        id=snapshot.id,
        source_name=snapshot.source_name,
        created_at=snapshot.created_at.isoformat(timespec="seconds"),
        tenant_name=snapshot.tenant_name,
        counts=snapshot.counts or {},
        skipped=snapshot.skipped or {},
        warning_count=counts.get("warning", 0),
        error_count=counts.get("error", 0),
        is_active=snapshot.is_active,
    )


def list_snapshots() -> list[SnapshotSummary]:
    with session_scope() as session:
        rows = session.execute(select(Snapshot).order_by(Snapshot.id.desc())).scalars().all()
        return [_summarise(session, row) for row in rows]


def active_snapshot() -> SnapshotSummary | None:
    with session_scope() as session:
        row = session.execute(
            select(Snapshot).where(Snapshot.is_active.is_(True)).order_by(Snapshot.id.desc())
        ).scalars().first()
        if row is None:
            row = session.execute(select(Snapshot).order_by(Snapshot.id.desc())).scalars().first()
        return _summarise(session, row) if row else None


def snapshot_warnings(snapshot_id: int) -> list[ImportWarning]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(ImportWarningRow)
                .where(ImportWarningRow.snapshot_id == snapshot_id)
                .order_by(ImportWarningRow.severity.desc(), ImportWarningRow.id)
            )
            .scalars()
            .all()
        )
        return [
            ImportWarning(
                category=r.category,
                message=r.message,
                record_type=r.record_type,
                record_id=r.record_id,
                severity=r.severity,
            )
            for r in rows
        ]


def load_graph(snapshot_id: int) -> AccessGraph:
    """Rebuild the in-memory graph for a snapshot. Cached: snapshots are immutable."""
    cached = _GRAPH_CACHE.get(snapshot_id)
    if cached is not None:
        return cached

    graph = AccessGraph()
    with session_scope() as session:
        snapshot = session.get(Snapshot, snapshot_id)
        if snapshot is None:
            raise LookupError(f"Snapshot {snapshot_id} does not exist.")
        graph.tenant = Tenant(
            id=snapshot.tenant_id or "tenant",
            display_name=snapshot.tenant_name or "Tenant",
            domain=snapshot.tenant_domain,
        )
        for row in session.execute(
            select(ScopeRow).where(ScopeRow.snapshot_id == snapshot_id)
        ).scalars():
            graph.scopes[row.scope] = ScopeNode(
                scope=row.scope,
                raw_scope=row.raw_scope,
                kind=ScopeKind(row.kind),
                name=row.name,
                display_name=row.display_name,
                parent_scope=row.parent_scope,
                resource_type=row.resource_type,
                location=row.location,
            )
        for row in session.execute(
            select(PrincipalRow).where(PrincipalRow.snapshot_id == snapshot_id)
        ).scalars():
            graph.principals[row.principal_id] = Principal(
                id=row.principal_id,
                display_name=row.display_name,
                type=PrincipalType(row.type),
                upn=row.upn,
                email=row.email,
                department=row.department,
                app_id=row.app_id,
                description=row.description,
            )
        for row in session.execute(
            select(RoleDefinitionRow).where(RoleDefinitionRow.snapshot_id == snapshot_id)
        ).scalars():
            graph.role_definitions[row.role_id] = RoleDefinition(
                id=row.role_id,
                name=row.name,
                description=row.description,
                role_type=row.role_type,
                assignable_scopes=tuple(row.assignable_scopes or ()),
                permissions=tuple(
                    Permission(
                        actions=tuple(block.get("actions") or ()),
                        not_actions=tuple(block.get("notActions") or ()),
                        data_actions=tuple(block.get("dataActions") or ()),
                        not_data_actions=tuple(block.get("notDataActions") or ()),
                    )
                    for block in (row.permissions or [])
                ),
            )
        for row in session.execute(
            select(RoleAssignmentRow).where(RoleAssignmentRow.snapshot_id == snapshot_id)
        ).scalars():
            graph.role_assignments.append(
                RoleAssignment(
                    id=row.assignment_id,
                    principal_id=row.principal_id,
                    role_definition_id=row.role_definition_id,
                    scope=row.scope,
                    raw_scope=row.raw_scope,
                    principal_type=PrincipalType(row.principal_type) if row.principal_type else None,
                    created_on=row.created_on,
                    created_by=row.created_by,
                    description=row.description,
                )
            )
        for row in session.execute(
            select(GroupMembershipRow).where(GroupMembershipRow.snapshot_id == snapshot_id)
        ).scalars():
            graph.memberships.append(GroupMembership(group_id=row.group_id, member_id=row.member_id))
        graph.warnings = snapshot_warnings(snapshot_id)

    graph.build_indexes()
    if len(_GRAPH_CACHE) >= _CACHE_LIMIT:
        _GRAPH_CACHE.pop(next(iter(_GRAPH_CACHE)))
    _GRAPH_CACHE[snapshot_id] = graph
    return graph


def active_graph() -> tuple[SnapshotSummary, AccessGraph] | tuple[None, None]:
    summary = active_snapshot()
    if summary is None:
        return None, None
    return summary, load_graph(summary.id)


def audit_log(limit: int = 100) -> list[AuditLogRow]:
    with session_scope() as session:
        return list(
            session.execute(select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit))
            .scalars()
            .all()
        )


def clear_cache() -> None:
    _GRAPH_CACHE.clear()
