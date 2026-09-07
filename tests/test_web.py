"""End-to-end tests through the HTTP layer, including the MVP journey."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from rolegraph.storage import db, repository
from rolegraph.web import state
from rolegraph.web.app import create_app

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo" / "contoso.json"
CONTRIBUTOR = "/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"
PAYMENTS_SUB = "/subscriptions/00000000-0000-0000-0000-0000000000a1"
DAN = "u-2b3c4d5e-dan"
JANE = "u-1a2b3c4d-jane"


@pytest.fixture()
def client(tmp_path):
    db.configure(f"sqlite:///{tmp_path / 'web.db'}")
    repository.clear_cache()
    state.invalidate()
    with TestClient(create_app()) as test_client:
        yield test_client
    repository.clear_cache()
    state.invalidate()
    db.reset()


@pytest.fixture()
def loaded(client):
    response = client.post("/import/demo")
    assert response.status_code == 200
    return client


# --- empty state -----------------------------------------------------------


def test_empty_install_invites_an_import(client):
    body = client.get("/").text
    assert "Nothing imported yet" in body
    assert "Load the Contoso demo dataset" in body


def test_health_endpoint(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_security_headers_are_set(client):
    headers = client.get("/").headers
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"


# --- import ----------------------------------------------------------------


def test_demo_import_reports_what_it_found(loaded):
    body = loaded.get("/import").text
    assert "contoso.json" in body
    assert "Active" in body


def test_upload_rejects_non_json_filenames(client):
    response = client.post("/import/upload", files={"file": ("evil.exe", b"{}", "application/json")})
    assert "Only .json files" in response.text


def test_upload_rejects_invalid_json(client):
    response = client.post("/import/upload", files={"file": ("bad.json", b"{not json", "application/json")})
    assert "not valid JSON" in response.text


def test_upload_rejects_a_document_with_no_known_sections(client):
    payload = json.dumps({"somethingElse": [1]}).encode()
    response = client.post("/import/upload", files={"file": ("x.json", payload, "application/json")})
    assert "none of the expected sections" in response.text


def test_upload_accepts_a_valid_dataset(client):
    response = client.post(
        "/import/upload", files={"file": ("contoso.json", DEMO.read_bytes(), "application/json")}
    )
    assert "Imported" in response.text
    assert "Contoso" in client.get("/").text


def test_warnings_are_shown_and_nothing_is_dropped_silently(client):
    payload = (Path(__file__).parent / "fixtures" / "malformed.json").read_bytes()
    response = client.post("/import/upload", files={"file": ("malformed.json", payload, "application/json")})
    assert "Warnings" in response.text
    assert "membershipCycle" in response.text
    assert "skipped" in response.text


def test_snapshots_can_be_switched(loaded):
    loaded.post("/import/demo")
    snapshots = repository.list_snapshots()
    assert len(snapshots) == 2
    older = min(s.id for s in snapshots)
    response = loaded.post(f"/import/activate/{older}", follow_redirects=False)
    assert response.status_code == 303
    assert repository.active_snapshot().id == older


def test_snapshot_can_be_deleted(loaded):
    loaded.post("/import/demo")
    older = min(s.id for s in repository.list_snapshots())
    loaded.post(f"/import/delete/{older}", follow_redirects=False)
    assert older not in {s.id for s in repository.list_snapshots()}


# --- overview --------------------------------------------------------------


def test_overview_reports_the_estate(loaded):
    body = loaded.get("/").text
    assert "Contoso Ltd" in body
    assert "Role assignments" in body
    assert "Custom roles" in body
    assert "High findings" in body


# --- identity explorer -----------------------------------------------------


def test_identity_search_finds_jane(loaded):
    body = loaded.get("/identities?q=jane").text
    assert "Jane Smith" in body
    assert "Dan Okafor" not in body


def test_identity_search_matches_upn_and_department(loaded):
    assert "Jane Smith" in loaded.get("/identities?q=jane.smith@contoso.com").text
    assert "Priya Raman" in loaded.get("/identities?q=Security Engineering").text


def test_identity_search_partial_returns_a_fragment_for_htmx(loaded):
    response = loaded.get("/identities?q=dan", headers={"HX-Request": "true"})
    assert "<html" not in response.text
    assert "Dan Okafor" in response.text
    assert 'role="status"' in response.text
    assert "1 of 16 identities" in response.text


def test_role_search_partial_includes_updated_count(loaded):
    body = loaded.get("/roles?q=Legacy-Auditor", headers={"HX-Request": "true"}).text
    assert "<html" not in body
    assert 'role="status"' in body
    assert "1 of 9 roles" in body


def test_explorers_have_labeled_controls_and_keyboard_navigation(loaded):
    for route, label in [("identities", "identities"), ("roles", "roles")]:
        body = loaded.get(f"/{route}").text
        assert f'aria-label="Search {label}"' in body
        assert 'aria-label="Primary navigation"' in body
        assert 'href="#main-content"' in body
        assert 'aria-current="page"' in body


def test_identity_type_filter(loaded):
    body = loaded.get("/identities?type=managedIdentity").text
    assert "mi-payments-api" in body
    assert "Jane Smith" not in body


def test_missing_identity_is_a_404(loaded):
    assert loaded.get("/identities/nope").status_code == 404


def test_identity_page_shows_grants_groups_and_reach(loaded):
    body = loaded.get(f"/identities/{DAN}").text
    assert "Dan Okafor" in body
    assert "Contributor" in body
    assert "Azure-Platform-Admins" in body
    assert "Scopes reached" in body
    assert "relationship" in body  # the focused diagram


def test_identity_page_explains_inherited_access_in_plain_english(loaded):
    body = loaded.get(f"/identities/{DAN}").text
    assert "because they are a member of" in body


def test_group_page_lists_its_members(loaded):
    body = loaded.get("/identities/g-a1-platform-admins").text
    assert "Members of this group" in body
    assert "Dan Okafor" in body  # reaches the group through nesting


# --- access path -----------------------------------------------------------


def test_access_path_shows_every_step(loaded):
    chain = "g-a2-developers>g-a3-all-engineering>g-a1-platform-admins"
    body = loaded.get(f"/identities/{DAN}/path/ra-0001?chain={chain}").text
    assert "Access path" in body
    assert "Azure-Developers" in body
    assert "All-Engineering" in body
    assert "Azure-Platform-Admins" in body
    assert "Contributor" in body
    assert "Production" in body


def test_access_path_shows_inheritance_and_permissions(loaded):
    body = loaded.get(f"/identities/{DAN}/path/ra-0001?scope={quote(PAYMENTS_SUB, safe='')}").text
    assert "Inherited from Production" in body
    assert "Microsoft.Authorization" in body  # the role's notActions
    assert "Scopes this assignment applies to" in body


def test_access_path_for_a_direct_assignment(loaded):
    body = loaded.get(f"/identities/{JANE}/path/ra-0003").text
    assert "direct assignment" in body
    assert "User Access Administrator" in body


def test_unknown_access_path_is_a_404(loaded):
    assert loaded.get(f"/identities/{DAN}/path/ra-does-not-exist").status_code == 404


# --- roles -----------------------------------------------------------------


def test_role_explorer_lists_custom_roles_first(loaded):
    body = loaded.get("/roles").text
    assert body.index("Contoso-Platform-Operator") < body.index(">Contributor<")


def test_role_filter_shows_only_custom_roles(loaded):
    body = loaded.get("/roles?type=custom").text
    assert "Contoso-Legacy-Auditor" in body
    assert "Storage Blob Data Contributor" not in body


def test_role_detail_lists_permissions_and_holders(loaded):
    body = loaded.get(f"/roles/{quote(CONTRIBUTOR, safe='')}").text
    assert "Contributor" in body
    assert "Identities holding this role" in body
    assert "Jane Smith" in body
    assert "Microsoft.Authorization/*/Write" in body


def test_missing_role_is_a_404(loaded):
    assert loaded.get("/roles/does-not-exist").status_code == 404


# --- findings and privileged access ---------------------------------------


def test_findings_page_lists_observations_with_reasons(loaded):
    body = loaded.get("/findings").text
    assert "privileged-via-group" in body
    assert "Nobody granted this identity the role directly" in body
    assert "See the access path" in body


def test_findings_can_be_filtered_by_severity_and_rule(loaded):
    high = loaded.get("/findings?severity=high").text
    assert "direct-owner" in high
    only = loaded.get("/findings?rule=unused-custom-role").text
    assert "Contoso-Legacy-Auditor" in only
    # the rule chips still list every rule; the cards themselves must be filtered
    assert "holds Owner directly" not in only


def test_privileged_view_lists_holders_and_the_configured_roles(loaded):
    body = loaded.get("/privileged").text
    assert "sp-payments-deploy" in body
    assert "User Access Administrator" in body
    assert "privileged_roles.yaml" in body


# --- scopes ----------------------------------------------------------------


def test_scope_tree_renders(loaded):
    body = loaded.get("/scopes").text
    assert "Production" in body
    assert "Payments Production" in body


def test_scope_detail_shows_who_can_reach_it(loaded):
    body = loaded.get(f"/scopes?scope={quote(PAYMENTS_SUB, safe='')}").text
    assert "Who has access here" in body
    assert "Dan Okafor" in body
    assert "Inherited" in body


def test_unknown_scope_is_a_404(loaded):
    assert loaded.get("/scopes?scope=/subscriptions/nope").status_code == 404


def test_malformed_scope_is_a_400(loaded):
    assert loaded.get("/scopes?scope=garbage").status_code == 400


# --- the definition of done ------------------------------------------------


def test_mvp_journey_end_to_end(client):
    """Load the demo, search Jane, see her production access, the path, a finding, the role."""
    assert "Nothing imported yet" in client.get("/").text

    assert client.post("/import/demo").status_code == 200
    assert "Contoso Ltd" in client.get("/").text

    search = client.get("/identities?q=Jane").text
    assert "Jane Smith" in search

    identity = client.get(f"/identities/{JANE}").text
    assert "Contributor" in identity                      # her production access
    assert "Production" in identity
    assert "Via group" in identity                        # direct vs inherited is shown
    assert "Azure-Platform-Admins" in identity            # the group in the path
    assert "User Access Administrator" in identity        # her direct assignment

    path = client.get(f"/identities/{JANE}/path/ra-0001?chain=g-a1-platform-admins").text
    assert "Azure-Platform-Admins" in path                # the group step
    assert "Contributor" in path                          # the role step
    assert "Production" in path                           # the scope step
    assert "Microsoft.Authorization/*/Write" in path      # the role's permissions

    findings = client.get("/findings").text
    assert "Jane Smith" in findings                       # a finding tied to her path
    assert "privileged-via-group" in findings

    role = client.get(f"/roles/{quote(CONTRIBUTOR, safe='')}").text
    assert "Actions" in role
    assert "Jane Smith" in role
