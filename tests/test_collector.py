"""Run the PowerShell collector against recorded-shape cloud responses, never Azure.

The doubles replace only authenticated cloud reads. Serialization, pagination,
normalization, file safeguards and the real Python importer all execute.
Set ROLEGRAPH_PWSH to a PowerShell 7.4+ binary if pwsh is not on PATH.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rolegraph.importer import import_document
from rolegraph.resolver.access import access_paths

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/collect/Export-RoleGraphDataset.ps1"
DRIVER = ROOT / "tests/fixtures/collector_driver.ps1"
TENANT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"
MG = f"/providers/Microsoft.Management/managementGroups/{TENANT}"
ROLE = "33333333-3333-3333-3333-333333333333"
ROLE_ID = f"/providers/Microsoft.Authorization/roleDefinitions/{ROLE}"
GRAPH = "https://graph.microsoft.com/v1.0"
FLAGS = " --only-show-errors --output json"
ASSIGN_FLAGS = " --fill-principal-name false --fill-role-definition-name false"


@pytest.fixture
def pwsh():
    binary = os.environ.get("ROLEGRAPH_PWSH") or shutil.which("pwsh")
    if not binary:
        pytest.skip("PowerShell 7.4+ required: set ROLEGRAPH_PWSH (see docs/TESTING_AND_SCREENSHOTS.md)")
    return binary


@pytest.fixture
def export_fixture():
    role = {"id": f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{ROLE}",
            "roleName": "Contributor", "roleType": "BuiltInRole", "assignableScopes": ["/"],
            "permissions": [{"actions": ["*"], "notActions": ["Microsoft.Authorization/*/Write"],
                             "dataActions": [], "notDataActions": []}]}
    return {
        "context": {"TenantId": TENANT, "Environment": "Global", "Scopes": ["Directory.Read.All"]},
        "az": {
            "account show" + FLAGS: {"tenantId": TENANT, "environmentName": "AzureCloud"},
            "account list --all" + FLAGS: [{"id": SUB, "tenantId": TENANT, "name": "Production", "state": "Enabled"}],
            "account management-group entities list" + FLAGS: [
                {"id": MG, "name": TENANT, "type": "Microsoft.Management/managementGroups",
                 "properties": {"displayName": "Tenant root", "parent": {"id": "/"}}},
                {"id": f"/subscriptions/{SUB}", "name": SUB, "type": "/subscriptions",
                 "properties": {"displayName": "Production", "parent": {"id": MG}}}],
            f"group list --subscription {SUB}" + FLAGS: [{"name": "payments", "location": "eastus"}],
            f"resource list --subscription {SUB}" + FLAGS: [
                {"id": f"/subscriptions/{SUB}/resourceGroups/payments/providers/Microsoft.Storage/storageAccounts/payments",
                 "name": "payments", "type": "Microsoft.Storage/storageAccounts", "location": "eastus"}],
            f"role definition list --scope {MG}" + FLAGS: [copy.deepcopy(role)],
            f"role definition list --scope /subscriptions/{SUB} --subscription {SUB}" + FLAGS: [role],
            f"role assignment list --scope {MG}" + ASSIGN_FLAGS + FLAGS: [
                {"id": MG + "/providers/Microsoft.Authorization/roleAssignments/assignment-one",
                 "principalId": "outer", "principalType": "Group", "roleDefinitionId": role["id"], "scope": MG}],
            f"role assignment list --all --subscription {SUB}" + ASSIGN_FLAGS + FLAGS: [],
        },
        "graph": {
            GRAPH + "/organization?$select=id,displayName,verifiedDomains": {
                "value": [{"id": TENANT, "displayName": "Example Tenant", "verifiedDomains": [{"name": "example.test", "isDefault": True}]}]},
            GRAPH + "/users?$select=id,displayName,userPrincipalName,mail,department": {
                "value": [{"id": "jane", "displayName": "Jane Example", "userPrincipalName": "jane@example.test"}],
                "@odata.nextLink": GRAPH + "/users?$skiptoken=page2"},
            GRAPH + "/users?$skiptoken=page2": {"value": [{"id": "alex", "displayName": "Alex Example"}]},
            GRAPH + "/groups?$select=id,displayName,description,visibility": {
                "value": [{"id": "inner", "displayName": "Engineers"}, {"id": "outer", "displayName": "Platform"}]},
            GRAPH + "/servicePrincipals?$select=id,displayName,appId,description,servicePrincipalType": {
                "value": [{"id": "deploy", "displayName": "Deploy", "appId": "app-1", "servicePrincipalType": "Application"},
                          {"id": "mi", "displayName": "Payments identity", "servicePrincipalType": "ManagedIdentity"}]},
            GRAPH + "/groups/inner/members": {"value": [{"id": "jane", "@odata.type": "#microsoft.graph.user"}]},
            GRAPH + "/groups/outer/members": {"value": [{"id": "inner", "@odata.type": "#microsoft.graph.group"}]},
            GRAPH + "/servicePrincipals/deploy/memberOf": {"value": [{"id": "outer", "@odata.type": "#microsoft.graph.group"}]},
            GRAPH + "/servicePrincipals/mi/memberOf": {"value": []},
        },
    }


def run_collector(pwsh, tmp_path, fixture, *flags, output=None):
    source = tmp_path / "responses.json"
    source.write_text(json.dumps(fixture))
    output = output or tmp_path / "export.json"
    result = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(DRIVER),
         "-Collector", str(COLLECTOR), "-Fixture", str(source), "-OutputPath", str(output), *flags],
        capture_output=True, text=True, timeout=40, cwd=tmp_path,
    )
    return result, output


def test_dry_run_needs_no_cloud_sessions_and_creates_no_output(pwsh, tmp_path):
    result, output = run_collector(pwsh, tmp_path, {"az": {}, "graph": {}}, "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout and "Directory.Read.All" in result.stdout
    assert "groupMemberships" in result.stdout
    assert not output.exists()
    assert {p.name for p in tmp_path.iterdir()} == {"responses.json"}


def test_export_pages_users_preserves_nested_access_and_supplements_service_principals(pwsh, tmp_path, export_fixture):
    result, output = run_collector(pwsh, tmp_path, export_fixture, "-IncludeResources")
    assert result.returncode == 0, result.stderr
    doc = json.loads(output.read_text())
    assert {u["id"] for u in doc["users"]} == {"jane", "alex"}
    assert [p["id"] for p in doc["managedIdentities"]] == ["mi"]
    assert [p["id"] for p in doc["servicePrincipals"]] == ["deploy"]
    assert len(doc["roleDefinitions"]) == 1
    assert doc["roleDefinitions"][0]["id"] == ROLE_ID
    assert doc["roleAssignments"][0]["id"] == "assignment-one"
    assert len(doc["resources"]) == 1
    imported = import_document(doc)
    assert not imported.error_count
    paths = access_paths(imported.graph, "jane")
    assert len(paths) == 1
    assert paths[0].membership_chain == ("inner", "outer")
    assert access_paths(imported.graph, "deploy")[0].membership_chain == ("outer",)
    assert {p.name for p in tmp_path.iterdir()} == {"responses.json", "export.json"}
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0


def test_optional_resources_are_explicitly_reported_as_not_collected(pwsh, tmp_path, export_fixture):
    result, output = run_collector(pwsh, tmp_path, export_fixture)
    assert result.returncode == 0, result.stderr
    doc = json.loads(output.read_text())
    assert doc["resources"] == []
    assert not doc["$collection"]["includeResources"]
    assert "resources" in json.dumps(doc["$collection"]["warnings"]).lower()


@pytest.mark.parametrize("failure, expected", [
    ("tenant", "Tenant mismatch"), ("azure", "Azure read failed"),
    ("graph", "Graph read failed"), ("pagination", "pagination repeated"),
    ("off_host", "unexpected pagination URL"), ("bad_response", "invalid collection response"),
    ("hidden", "requires Member.Read.Hidden"),
])
def test_failed_or_incomplete_required_reads_leave_the_output_absent(pwsh, tmp_path, export_fixture, failure, expected):
    if failure == "tenant":
        export_fixture["context"]["TenantId"] = "different-tenant"
    elif failure == "azure":
        export_fixture["az"][f"group list --subscription {SUB}" + FLAGS] = "403 forbidden"
    elif failure == "graph":
        export_fixture["graph"][GRAPH + "/groups/inner/members"] = "403 forbidden"
    elif failure == "pagination":
        export_fixture["graph"][GRAPH + "/users?$skiptoken=page2"]["@odata.nextLink"] = GRAPH + "/users?$skiptoken=page2"
    elif failure == "off_host":
        export_fixture["graph"][GRAPH + "/users?$skiptoken=page2"]["@odata.nextLink"] = "https://example.invalid/collect"
    elif failure == "bad_response":
        export_fixture["graph"][GRAPH + "/users?$skiptoken=page2"] = {"unexpected": []}
    else:
        export_fixture["graph"][GRAPH + "/groups?$select=id,displayName,description,visibility"]["value"][0]["visibility"] = "HiddenMembership"
    result, output = run_collector(pwsh, tmp_path, export_fixture)
    assert result.returncode != 0
    assert expected in result.stderr
    assert not output.exists()


def test_assigned_custom_role_at_resource_group_scope_is_fetched(pwsh, tmp_path, export_fixture):
    scope = f"/subscriptions/{SUB}/resourceGroups/payments"
    role = export_fixture["az"][f"role definition list --scope {MG}" + FLAGS][0]
    export_fixture["az"][f"role definition list --scope {MG}" + FLAGS] = []
    export_fixture["az"][f"role definition list --scope /subscriptions/{SUB} --subscription {SUB}" + FLAGS] = []
    assignment = export_fixture["az"][f"role assignment list --scope {MG}" + ASSIGN_FLAGS + FLAGS].pop()
    assignment["scope"] = scope
    export_fixture["az"][f"role assignment list --all --subscription {SUB}" + ASSIGN_FLAGS + FLAGS] = [assignment]
    export_fixture["az"][f"role definition list --name {ROLE} --scope {scope} --subscription {SUB}" + FLAGS] = [role]
    result, output = run_collector(pwsh, tmp_path, export_fixture)
    assert result.returncode == 0, result.stderr
    assert not import_document(json.loads(output.read_text())).error_count


def test_hierarchy_subscriptions_missing_from_cli_cache_are_reported(pwsh, tmp_path, export_fixture):
    export_fixture["az"]["account list --all" + FLAGS] = []
    result, output = run_collector(pwsh, tmp_path, export_fixture)
    assert result.returncode == 0, result.stderr
    warnings = json.loads(output.read_text())["$collection"]["warnings"]
    assert any(w["category"] == "subscriptionNotCollected" and SUB in w["message"] for w in warnings)


def test_existing_output_is_preserved_until_explicit_successful_overwrite(pwsh, tmp_path, export_fixture):
    output = tmp_path / "export.json"
    output.write_text("original")
    result, _ = run_collector(pwsh, tmp_path, export_fixture)
    assert result.returncode != 0
    assert output.read_text() == "original"
    result, _ = run_collector(pwsh, tmp_path, export_fixture, "-Overwrite")
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["tenants"][0]["id"] == TENANT


def test_failed_overwrite_leaves_existing_output_intact(pwsh, tmp_path, export_fixture):
    output = tmp_path / "export.json"
    output.write_text("original")
    export_fixture["graph"][GRAPH + "/users?$skiptoken=page2"] = "timeout"
    result, _ = run_collector(pwsh, tmp_path, export_fixture, "-Overwrite")
    assert result.returncode != 0
    assert output.read_text() == "original"


def test_output_through_a_symlink_is_rejected(pwsh, tmp_path, export_fixture):
    target = tmp_path / "original.json"
    target.write_text("original")
    output = tmp_path / "export.json"
    output.symlink_to(target)
    result, _ = run_collector(pwsh, tmp_path, export_fixture, "-Overwrite", output=output)
    assert result.returncode != 0
    assert target.read_text() == "original"


def test_a_missing_output_directory_is_not_created(pwsh, tmp_path, export_fixture):
    result, output = run_collector(pwsh, tmp_path, export_fixture, output=tmp_path / "missing/export.json")
    assert result.returncode != 0
    assert not output.parent.exists()
