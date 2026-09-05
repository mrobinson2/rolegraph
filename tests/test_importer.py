import json
from pathlib import Path

import pytest

from rolegraph.domain.ids import ScopeKind
from rolegraph.importer import SchemaError, import_document, import_json_bytes
from rolegraph.resolver.access import access_at_scope, access_paths

FIXTURES = Path(__file__).parent / "fixtures"
DEMO = Path(__file__).resolve().parent.parent / "data" / "demo" / "contoso.json"


@pytest.fixture(scope="module")
def malformed():
    return import_document(json.loads((FIXTURES / "malformed.json").read_text()), "malformed.json")


@pytest.fixture(scope="module")
def demo():
    return import_document(json.loads(DEMO.read_text()), "contoso.json")


def categories(result):
    return {w.category for w in result.warnings}


def messages(result, category):
    return [w.message for w in result.warnings if w.category == category]


# --- document-level validation -------------------------------------------


def test_non_object_document_is_rejected():
    with pytest.raises(SchemaError):
        import_document([1, 2, 3])


def test_section_that_is_not_a_list_is_rejected():
    with pytest.raises(SchemaError):
        import_document({"users": {"id": "u-1"}})


def test_document_with_no_known_sections_is_rejected():
    with pytest.raises(SchemaError):
        import_document({"somethingElse": [1]})


def test_invalid_json_bytes_produce_a_readable_error():
    with pytest.raises(SchemaError) as exc:
        import_json_bytes(b"{not json")
    assert "not valid JSON" in str(exc.value)


def test_oversized_upload_is_rejected_before_parsing():
    with pytest.raises(SchemaError) as exc:
        import_json_bytes(b"x" * 200, max_bytes=100)
    assert "over the" in str(exc.value)


def test_unknown_top_level_key_is_reported_not_silently_ignored(malformed):
    assert any("notAKnownSection" in m for m in messages(malformed, "unknownSection"))


def test_schema_marker_keys_are_not_flagged(demo):
    assert not messages(demo, "unknownSection")


# --- record-level handling ------------------------------------------------


def test_records_missing_required_fields_are_skipped_with_an_error(malformed):
    assert malformed.skipped["users"] >= 1
    assert any("missing required field" in m for m in messages(malformed, "malformedRecord"))


def test_non_object_record_is_skipped(malformed):
    assert any("not a JSON object" in m for m in messages(malformed, "malformedRecord"))


def test_duplicate_ids_keep_the_first_record(malformed):
    assert malformed.graph.role_definitions["rd-reader"].name == "Reader"
    assert any("rd-reader" in m for m in messages(malformed, "duplicate"))


def test_second_tenant_is_reported(malformed):
    assert malformed.graph.tenant.id == "tenant-a"
    assert any("tenant records" in m for m in messages(malformed, "duplicate"))


def test_missing_tenant_creates_a_placeholder_root():
    result = import_document({"users": [{"id": "u-1"}]})
    assert result.graph.scopes["/"].kind is ScopeKind.TENANT
    assert "missingSection" in categories(result)


def test_orphan_management_group_is_reported(malformed):
    assert any("Orphaned MG" in m for m in messages(malformed, "danglingReference"))


def test_subscription_without_a_management_group_attaches_to_root(malformed):
    assert malformed.graph.scopes["/subscriptions/sub-2"].parent_scope == "/"
    assert "missingParent" in categories(malformed)


def test_unparseable_resource_id_is_skipped(malformed):
    assert "not-a-scope" not in malformed.graph.scopes
    assert any("could not be parsed" in m for m in messages(malformed, "malformedRecord"))


def test_resource_record_that_is_really_a_subscription_is_skipped(malformed):
    assert malformed.graph.scopes["/subscriptions/sub-1"].kind is ScopeKind.SUBSCRIPTION


def test_role_with_non_list_permissions_is_imported_as_empty(malformed):
    assert malformed.graph.role_definitions["rd-bad-perms"].permissions == ()
    assert any("non-list" in m for m in messages(malformed, "malformedRecord"))


def test_role_with_no_permissions_is_flagged(malformed):
    assert any("rd-empty" in m for m in messages(malformed, "emptyPermissions"))


# --- reference integrity ---------------------------------------------------


def test_assignment_to_an_unknown_role_is_an_error(malformed):
    errors = [w for w in malformed.warnings if w.severity == "error"]
    assert any("rd-does-not-exist" in w.message for w in errors)


def test_assignment_to_an_unknown_principal_is_reported(malformed):
    assert any("u-missing" in m for m in messages(malformed, "danglingReference"))


def test_assignment_at_an_unimported_scope_is_reported(malformed):
    assert any("sub-unknown" in m for m in messages(malformed, "danglingReference"))


def test_assignment_with_a_garbage_scope_is_skipped(malformed):
    assert "ra-5" not in {ra.id for ra in malformed.graph.role_assignments}
    assert malformed.skipped["roleAssignments"] >= 1


def test_principal_type_mismatch_is_reported(malformed):
    assert any("principalType" in m for m in messages(malformed, "typeMismatch"))


def test_self_membership_is_skipped(malformed):
    assert not any(
        m.group_id == "g-1" and m.member_id == "g-1" for m in malformed.graph.memberships
    )


def test_duplicate_membership_is_deduplicated(malformed):
    pairs = [(m.group_id, m.member_id) for m in malformed.graph.memberships]
    assert pairs.count(("g-1", "u-1")) == 1


def test_membership_cycle_is_reported_as_an_error(malformed):
    cycle_warnings = [w for w in malformed.warnings if w.category == "membershipCycle"]
    assert cycle_warnings and cycle_warnings[0].severity == "error"


def test_import_is_total_despite_bad_records(malformed):
    """A document full of problems still yields a working graph."""
    assert malformed.graph.principals
    assert access_paths(malformed.graph, "u-1")


def test_result_reports_counts_and_checksum(malformed):
    assert malformed.total_records > 0
    assert malformed.total_skipped > 0
    assert len(malformed.checksum) == 16
    assert malformed.imported_at


# --- the demo dataset ------------------------------------------------------


def test_demo_dataset_imports_cleanly(demo):
    assert demo.total_skipped == 0
    assert not [w for w in demo.warnings if w.severity == "error"]


def test_demo_dataset_counts(demo):
    assert demo.counts["roleAssignments"] == 15
    assert demo.counts["groupMemberships"] == 11
    assert demo.counts["roleDefinitions"] == 9


def test_demo_planted_issue_developer_inherits_production_contributor(demo):
    paths = access_at_scope(demo.graph, "u-2b3c4d5e-dan", "/subscriptions/00000000-0000-0000-0000-0000000000a1")
    contributor = [p for p in paths if p.role.name == "Contributor"]
    assert contributor
    assert all(p.is_nested_group for p in contributor)


def test_demo_planted_issue_service_principal_owns_a_subscription(demo):
    paths = access_paths(demo.graph, "sp-b1-payments-deploy")
    assert any(p.role.name == "Owner" and p.is_direct for p in paths)


def test_demo_planted_issue_duplicate_assignment_is_detected(demo):
    assert any(w.category == "duplicateAssignment" for w in demo.warnings)


def test_demo_planted_issue_broad_custom_role_exists(demo):
    role = next(r for r in demo.graph.custom_roles if r.name == "Contoso-Platform-Operator")
    assert len(role.wildcard_actions) >= 3


def test_demo_planted_issue_custom_role_can_assign_roles(demo):
    role = next(r for r in demo.graph.custom_roles if r.name == "Contoso-Deployment-Automation")
    assert role.can_assign_roles


def test_demo_dan_reaches_developers_by_two_paths(demo):
    from rolegraph.resolver.membership import membership_paths

    routes = [p.chain for p in membership_paths(demo.graph, "u-2b3c4d5e-dan") if p.group_id == "g-a2-developers"]
    assert len(routes) == 2
