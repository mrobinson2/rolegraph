import json
from pathlib import Path

import pytest

from rolegraph.importer import import_document
from rolegraph.storage import db, repository

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo" / "contoso.json"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.configure(f"sqlite:///{tmp_path / 'test.db'}")
    repository.clear_cache()
    yield
    repository.clear_cache()
    db.reset()


def demo_result():
    return import_document(json.loads(DEMO.read_text()), "contoso.json")


def test_snapshot_round_trips_every_entity():
    original = demo_result().graph
    sid = repository.save_import(demo_result())
    repository.clear_cache()
    loaded = repository.load_graph(sid)
    assert len(loaded.scopes) == len(original.scopes)
    assert len(loaded.principals) == len(original.principals)
    assert len(loaded.role_definitions) == len(original.role_definitions)
    assert len(loaded.role_assignments) == len(original.role_assignments)
    assert len(loaded.memberships) == len(original.memberships)


def test_loaded_graph_resolves_the_same_access():
    from rolegraph.resolver.access import access_paths

    result = demo_result()
    sid = repository.save_import(result)
    repository.clear_cache()
    before = {(p.role.name, p.scope) for p in access_paths(result.graph, "u-2b3c4d5e-dan")}
    after = {(p.role.name, p.scope) for p in access_paths(repository.load_graph(sid), "u-2b3c4d5e-dan")}
    assert before == after


def test_role_permissions_survive_the_round_trip():
    sid = repository.save_import(demo_result())
    repository.clear_cache()
    graph = repository.load_graph(sid)
    contributor = next(r for r in graph.role_definitions.values() if r.name == "Contributor")
    assert contributor.all_actions == ("*",)
    assert "Microsoft.Authorization/*/Write" in contributor.all_not_actions


def test_warnings_are_persisted():
    result = demo_result()
    sid = repository.save_import(result)
    stored = repository.snapshot_warnings(sid)
    assert len(stored) == len(result.warnings)


def test_importing_twice_creates_two_snapshots_and_activates_the_newest():
    first = repository.save_import(demo_result())
    second = repository.save_import(demo_result())
    assert first != second
    assert repository.active_snapshot().id == second
    assert len(repository.list_snapshots()) == 2


def test_an_older_snapshot_can_be_reactivated():
    first = repository.save_import(demo_result())
    repository.save_import(demo_result())
    repository.activate_snapshot(first)
    assert repository.active_snapshot().id == first


def test_activating_a_missing_snapshot_raises():
    with pytest.raises(LookupError):
        repository.activate_snapshot(999)


def test_snapshot_can_be_deleted():
    sid = repository.save_import(demo_result())
    repository.delete_snapshot(sid)
    assert repository.list_snapshots() == []
    with pytest.raises(LookupError):
        repository.load_graph(sid)


def test_import_without_activation_leaves_the_current_snapshot_active():
    first = repository.save_import(demo_result())
    repository.save_import(demo_result(), activate=False)
    assert repository.active_snapshot().id == first


def test_active_graph_returns_none_when_nothing_is_imported():
    assert repository.active_graph() == (None, None)


def test_summary_reports_counts_and_severities():
    sid = repository.save_import(demo_result())
    summary = next(s for s in repository.list_snapshots() if s.id == sid)
    assert summary.total_records > 0
    assert summary.error_count == 0
    assert summary.warning_count >= 1
    assert summary.is_active


def test_import_is_recorded_in_the_audit_log():
    repository.save_import(demo_result())
    actions = [row.action for row in repository.audit_log()]
    assert "import" in actions
    assert "activateSnapshot" in actions
