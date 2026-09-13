from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from rolegraph.drift.__main__ import main
from rolegraph.drift.engine import IncompleteScan, compare
from rolegraph.drift.files import load_target, markdown, parse_json
from rolegraph.drift.schema import Observation, TargetState

TENANT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"
PERSON = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROLE = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
SCOPE = f"/subscriptions/{SUB}"
MG = f"/providers/Microsoft.Management/managementGroups/{TENANT}"
NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
ASSIGNMENT = {"principalId": PERSON, "roleDefinitionId": ROLE, "scope": SCOPE}


def target_document():
    return {"schemaVersion": 1, "tenantId": TENANT, "scopes": [{"id": SCOPE}], "assignments": [copy.deepcopy(ASSIGNMENT)]}


def observation_document():
    return {"schemaVersion": 1, "tenantId": TENANT, "collectedAt": NOW.isoformat(),
            "scopeResults": [{"scope": {"id": SCOPE}, "assignments": [
                {"id": SCOPE + "/providers/Microsoft.Authorization/roleAssignments/cccccccc-cccc-cccc-cccc-cccccccccccc", **ASSIGNMENT}]}]}


def report(target=None, observed=None):
    return compare(TargetState.model_validate(target or target_document()),
                   Observation.model_validate(observed or observation_document()), now=NOW)


def test_assignment_ids_display_names_casing_and_role_prefixes_do_not_create_drift():
    target = target_document()
    target["assignments"][0].update(principalName="Approved human-readable name", roleName="Reader")
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0].update(
        principalId=PERSON.upper(), scope=SCOPE.upper() + "/",
        roleDefinitionId=f"{SCOPE}/providers/Microsoft.Authorization/roleDefinitions/{ROLE.upper()}")
    assert report(target, observed)["status"] == "in-sync"


def test_added_unknown_identity_and_removed_approved_grant_are_both_reported():
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0]["principalId"] = OTHER
    result = report(observed=observed)
    assert {f["kind"] for f in result["findings"]} == {"missing", "unexpected"}


def test_a_scope_change_is_not_treated_as_the_same_approved_access():
    observed = observation_document()
    row = observed["scopeResults"][0]["assignments"][0]
    row["scope"] += "/resourceGroups/payments"
    row["id"] = row["id"].replace(SCOPE, row["scope"])
    assert {f["kind"] for f in report(observed=observed)["findings"]} == {"missing", "unexpected"}


@pytest.mark.parametrize("condition", [None, "@Resource[name] StringEquals 'Prod'", "@Resource[name]  StringEquals 'prod'"])
def test_changed_or_removed_conditions_are_detected_without_normalizing_quoted_values(condition):
    target = target_document()
    target["assignments"][0].update(condition="@Resource[name] StringEquals 'prod'", conditionVersion="2.0")
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0].update(condition=condition, conditionVersion="2.0" if condition else None)
    assert [f["kind"] for f in report(target, observed)["findings"]] == ["changed"]


def test_delegated_managed_identity_changes_are_detected():
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0]["delegatedManagedIdentityResourceId"] = (
        SCOPE + "/resourceGroups/platform/providers/Microsoft.ManagedIdentity/userAssignedIdentities/deploy")
    assert report(observed=observed)["summary"]["changed"] == 1


def test_duplicate_ids_from_overlapping_queries_are_collapsed_but_distinct_assignments_are_reported():
    observed = observation_document()
    rows = observed["scopeResults"][0]["assignments"]
    rows.append(copy.deepcopy(rows[0]))
    assert report(observed=observed)["status"] == "in-sync"
    rows.append({**rows[0], "id": SCOPE + "/providers/Microsoft.Authorization/roleAssignments/dddddddd-dddd-dddd-dddd-dddddddddddd"})
    assert report(observed=observed)["summary"]["duplicate"] == 1


def test_conflicting_reads_for_the_same_assignment_id_are_an_incomplete_scan():
    observed = observation_document()
    rows = observed["scopeResults"][0]["assignments"]
    rows.append({**rows[0], "principalId": OTHER})
    with pytest.raises(IncompleteScan, match="Conflicting"):
        report(observed=observed)


def test_inherited_management_group_grants_are_reported_and_require_explicit_approval_at_the_origin():
    observed = observation_document()
    observed["scopeResults"][0]["assignments"].append({**ASSIGNMENT, "id": MG + "/providers/Microsoft.Authorization/roleAssignments/dddddddd-dddd-dddd-dddd-dddddddddddd", "scope": MG})
    assert report(observed=observed)["summary"]["unexpected"] == 1
    target = target_document()
    target["scopes"].append({"id": MG, "includeDescendants": False})
    target["assignments"].append({**ASSIGNMENT, "scope": MG})
    observed["scopeResults"].append({"scope": target["scopes"][1], "assignments": [observed["scopeResults"][0]["assignments"][1]]})
    assert report(target, observed)["status"] == "in-sync"


@pytest.mark.parametrize("change", ["wrong-tenant", "missing-scope", "duplicate-scope", "wrong-descendants", "stale", "future"])
def test_incomplete_wrong_tenant_and_stale_scans_never_pass(change):
    observed = observation_document()
    if change == "wrong-tenant":
        observed["tenantId"] = OTHER
    elif change == "missing-scope":
        observed["scopeResults"] = []
    elif change == "duplicate-scope":
        observed["scopeResults"] *= 2
    elif change == "wrong-descendants":
        observed["scopeResults"][0]["scope"]["includeDescendants"] = False
    else:
        observed["collectedAt"] = (NOW + timedelta(hours=-37 if change == "stale" else 1)).isoformat()
    with pytest.raises(IncompleteScan):
        report(observed=observed)


@pytest.mark.parametrize("change", ["unknown-field", "duplicate", "outside-scope", "bad-guid", "condition-version", "mg-descendants", "string-bool", "path-injection"])
def test_malformed_and_ambiguous_targets_are_rejected(change):
    target = target_document()
    if change == "unknown-field":
        target["assignment"] = []
    elif change == "duplicate":
        target["assignments"] *= 2
    elif change == "outside-scope":
        target["assignments"][0]["scope"] = MG
    elif change == "bad-guid":
        target["assignments"][0]["principalId"] = "friendly-name"
    elif change == "condition-version":
        target["assignments"][0]["condition"] = "condition"
    elif change == "mg-descendants":
        target["scopes"] = [{"id": MG}]
    elif change == "string-bool":
        target["scopes"][0]["includeDescendants"] = "false"
    else:
        target["scopes"][0]["id"] = SCOPE + "?api-version=wrong"
    with pytest.raises(ValidationError):
        TargetState.model_validate(target)


def test_custom_role_permission_changes_and_missing_pins_are_detected():
    target = target_document()
    pin = {"id": SCOPE + "/providers/Microsoft.Authorization/roleDefinitions/" + ROLE,
           "permissions": [{"actions": ["Microsoft.Storage/*/read"], "notActions": [], "dataActions": [], "notDataActions": []}]}
    target["roleDefinitions"] = [pin]
    with pytest.raises(IncompleteScan, match="pinned"):
        report(target)
    observed = observation_document()
    observed["roleDefinitions"] = [copy.deepcopy(pin)]
    assert report(target, observed)["status"] == "in-sync"
    observed["roleDefinitions"][0]["permissions"][0]["actions"].append("*")
    assert report(target, observed)["summary"]["role-definition-changed"] == 1


def test_role_permission_order_and_action_casing_are_not_drift():
    target = target_document()
    target["roleDefinitions"] = [{"id": SCOPE + "/providers/Microsoft.Authorization/roleDefinitions/" + ROLE,
        "permissions": [{"actions": ["b/read", "a/read"], "notActions": [], "dataActions": [], "notDataActions": []}]}]
    observed = observation_document()
    observed["roleDefinitions"] = copy.deepcopy(target["roleDefinitions"])
    observed["roleDefinitions"][0]["permissions"][0]["actions"] = ["A/read", "B/read"]
    assert report(target, observed)["status"] == "in-sync"


def test_an_explicit_empty_approval_set_detects_every_assignment():
    target = target_document()
    target["assignments"] = []
    assert report(target)["summary"]["unexpected"] == 1


def test_json_duplicates_and_nonstandard_constants_are_rejected():
    for payload in (b'{"assignments": [], "assignments": []}', b'{"value": NaN}'):
        with pytest.raises(ValueError):
            parse_json(payload)


def test_target_directory_merges_files_but_rejects_duplicate_grants_and_cross_tenant_files(tmp_path):
    first = target_document()
    second = target_document()
    second["assignments"][0]["principalId"] = OTHER
    (tmp_path / "one.json").write_text(json.dumps(first))
    (tmp_path / "two.json").write_text(json.dumps(second))
    merged, metadata = load_target(tmp_path)
    assert len(merged.scopes) == 1 and len(merged.assignments) == 2
    assert metadata["files"] == ["one.json", "two.json"]
    second["assignments"][0]["principalId"] = PERSON
    (tmp_path / "two.json").write_text(json.dumps(second))
    with pytest.raises(ValueError, match="Duplicate assignment"):
        load_target(tmp_path)
    second["tenantId"] = OTHER
    (tmp_path / "two.json").write_text(json.dumps(second))
    with pytest.raises(ValueError, match="same tenant"):
        load_target(tmp_path)


def test_empty_target_directory_cannot_validate(tmp_path):
    assert main(["validate", "--target", str(tmp_path)]) == 2


@pytest.mark.parametrize("state,code", [("in-sync", 0), ("drift", 1), ("error", 2)])
def test_cli_exit_codes_and_reports_distinguish_drift_from_failure(tmp_path, state, code):
    target = target_document()
    observed = observation_document()
    observed["collectedAt"] = datetime.now(timezone.utc).isoformat()
    if state == "drift":
        observed["scopeResults"][0]["assignments"] = []
    elif state == "error":
        observed["scopeResults"] = []
    (tmp_path / "target.json").write_text(json.dumps(target))
    (tmp_path / "observed.json").write_text(json.dumps(observed))
    args = ["check", "--target", str(tmp_path / "target.json"), "--observed", str(tmp_path / "observed.json"),
            "--output-dir", str(tmp_path / "report"), "--revision", "test-revision"]
    assert main(args) == code
    path = tmp_path / "report/report.json"
    data = json.loads(path.read_text())
    assert data["status"] == state and data["revision"] == "test-revision"
    original = path.read_bytes()
    assert main(args) == 2  # refusing to reuse evidence is distinct from drift
    assert path.read_bytes() == original
    assert "Status:" in (tmp_path / "report/report.md").read_text()


def test_untrusted_display_names_are_escaped_in_markdown_reports():
    target = target_document()
    target["assignments"][0]["principalName"] = "<script>bad</script> | [click](https://invalid.test)\nnext"
    observed = observation_document()
    observed["scopeResults"][0]["assignments"] = []
    text = markdown(report(target, observed))
    assert "<script>" not in text and "[click]" not in text


def test_comparison_is_deterministic_at_a_fixed_clock():
    assert report() == report()


def test_boolean_schema_versions_cannot_pass_as_version_one():
    target = target_document()
    target["schemaVersion"] = True
    with pytest.raises(ValidationError):
        TargetState.model_validate(target)


def test_an_observed_assignment_must_have_an_id_at_its_originating_scope():
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0]["scope"] += "/resourceGroups/other"
    with pytest.raises(ValidationError, match="disagree"):
        Observation.model_validate(observed)


def test_the_latest_daily_scan_can_be_reviewed_within_the_default_freshness_window():
    observed = observation_document()
    observed["collectedAt"] = (NOW - timedelta(hours=25)).isoformat()
    assert report(observed=observed)["status"] == "in-sync"


def test_an_unexpected_builtin_role_retains_the_known_identity_name():
    target = target_document()
    target["assignments"][0]["principalName"] = "Platform readers"
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0]["roleDefinitionId"] = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
    finding = next(f for f in report(target, observed)["findings"] if f["kind"] == "unexpected")
    assert finding["principalName"] == "Platform readers" and finding["roleName"] == "Owner"
