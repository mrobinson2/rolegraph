import json
from datetime import datetime, timezone

import pytest

from tests.test_drift import OTHER, observation_document, target_document
from tests.test_web import client


def uploads(target=None, observed=None):
    observed = observed or observation_document()
    observed["collectedAt"] = datetime.now(timezone.utc).isoformat()
    return [("targets", ("approved.json", json.dumps(target or target_document()), "application/json")),
            ("observed", ("observed.json", json.dumps(observed), "application/json"))]


def test_drift_page_is_available_without_importing_a_snapshot(client):
    response = client.get("/drift")
    assert response.status_code == 200
    assert "Compare permissions" in response.text


def test_offline_comparison_displays_a_matching_target(client):
    response = client.post("/drift/compare", files=uploads())
    assert response.status_code == 200 and "Approved assignments match" in response.text


def test_unexpected_and_missing_assignments_are_shown_with_safe_evidence(client):
    target = target_document()
    target["assignments"][0]["principalName"] = "<script>alert(1)</script>"
    observed = observation_document()
    observed["scopeResults"][0]["assignments"][0]["principalId"] = OTHER
    response = client.post("/drift/compare", files=uploads(target, observed))
    assert "Approved assignment missing" in response.text and "Unapproved assignment" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_an_incomplete_observation_is_shown_as_a_failed_comparison(client):
    observed = observation_document()
    observed["scopeResults"] = []
    response = client.post("/drift/compare", files=uploads(observed=observed))
    assert "Comparison incomplete" in response.text and "No compliance conclusion" in response.text
    assert "Approved assignments match" not in response.text


@pytest.mark.parametrize("payload", [b"[]", b"{not-json", b'{"schemaVersion": 1, "schemaVersion": 1}', b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'])
def test_invalid_uploads_are_reported_without_a_server_error(client, payload):
    files = uploads()
    files[0] = ("targets", ("invalid.json", payload, "application/json"))
    response = client.post("/drift/compare", files=files)
    assert response.status_code == 200 and "Comparison incomplete" in response.text


def test_multiple_target_files_are_merged_in_the_web_comparison(client):
    second = target_document()
    second["assignments"] = []
    files = uploads()
    files.append(("targets", ("second.json", json.dumps(second), "application/json")))
    assert "Approved assignments match" in client.post("/drift/compare", files=files).text
