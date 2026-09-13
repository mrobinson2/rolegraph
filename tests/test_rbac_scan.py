from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from rolegraph.drift.engine import compare
from rolegraph.drift.schema import TargetState
from tests.test_drift import ASSIGNMENT, SCOPE, SUB, TENANT, target_document

spec = importlib.util.spec_from_file_location("scan_rbac", Path(__file__).resolve().parents[1] / "scripts/scan_rbac.py")
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def fixture_reads():
    account = {"id": SUB, "tenantId": TENANT, "state": "Enabled", "environmentName": "AzureCloud"}
    base = f"{scanner.ARM}{SCOPE}/providers/Microsoft.Authorization/roleAssignments?api-version={scanner.API_VERSION}"
    raw = {"id": SCOPE + "/providers/Microsoft.Authorization/roleAssignments/cccccccc-cccc-cccc-cccc-cccccccccccc", "properties": ASSIGNMENT}
    return {("account", "show"): account, ("account", "show", "--subscription", SUB): account,
            ("rest", "--method", "get", "--url", base): {"value": [raw], "nextLink": base + "&$skipToken=two"},
            ("rest", "--method", "get", "--url", base + "&$skipToken=two"): {"value": []},
            ("rest", "--method", "get", "--url", base + "&$filter=atScope()"): {"value": [raw]}}


def test_collector_reads_every_page_and_ancestors_without_mutating_context():
    responses = fixture_reads()
    called = []

    def read(args):
        called.append(tuple(args))
        return copy.deepcopy(responses[tuple(args)])

    target = TargetState.model_validate(target_document())
    observed = scanner.collect(target, read)
    assert len(called) == 5 and set(called) == set(responses)
    assert compare(target, observed)["status"] == "in-sync"


@pytest.mark.parametrize("next_link", ["https://evil.invalid/collect", "http://management.azure.com/test", 42,
    "https://management.azure.com@evil.invalid/test", "https://management.azure.com/other/path", ""])
def test_bad_pagination_cannot_produce_a_complete_scan(next_link):
    responses = fixture_reads()
    first = next(key for key in responses if key[0] == "rest")
    responses[first]["nextLink"] = next_link
    with pytest.raises(scanner.CollectionError):
        scanner.collect(TargetState.model_validate(target_document()), lambda args: responses[tuple(args)])


def test_pagination_cycle_is_an_error():
    responses = fixture_reads()
    first = next(key for key in responses if key[0] == "rest")
    responses[first]["nextLink"] = first[-1]
    with pytest.raises(scanner.CollectionError, match="repeated"):
        scanner.collect(TargetState.model_validate(target_document()), lambda args: responses[tuple(args)])


@pytest.mark.parametrize("field,value", [("tenantId", SUB), ("state", "Disabled"), ("id", TENANT), ("environmentName", "AzureUSGovernment")])
def test_every_explicit_subscription_is_verified(field, value):
    responses = fixture_reads()
    responses[("account", "show", "--subscription", SUB)] = {**responses[("account", "show")], field: value}
    with pytest.raises(scanner.CollectionError):
        scanner.collect(TargetState.model_validate(target_document()), lambda args: responses[tuple(args)])


def test_failed_live_scan_writes_an_error_report_and_no_observation(tmp_path, monkeypatch):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(target_document()))

    def fail(_target):
        raise scanner.CollectionError("A required scope could not be read.")

    monkeypatch.setattr(scanner, "collect", fail)
    assert scanner.main(["--target", str(target), "--output-dir", str(tmp_path / "result")]) == 2
    report = json.loads((tmp_path / "result/report.json").read_text())
    assert report["status"] == "error"
    assert not (tmp_path / "result/observed.json").exists()


def test_live_entrypoint_collects_compares_and_writes_complete_evidence(tmp_path, monkeypatch):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(target_document()))
    responses = fixture_reads()
    original = scanner.collect
    monkeypatch.setattr(scanner, "collect", lambda target: original(target, lambda args: responses[tuple(args)]))
    assert scanner.main(["--target", str(target), "--output-dir", str(tmp_path / "result")]) == 0
    assert json.loads((tmp_path / "result/report.json").read_text())["status"] == "in-sync"
    assert (tmp_path / "result/observed.json").exists()


def test_azure_cli_transport_retries_throttling_and_captures_output_without_a_shell(monkeypatch):
    calls = []
    pauses = []
    responses = iter([
        subprocess.CompletedProcess([], 1, b"", b"429 TooManyRequests"),
        subprocess.CompletedProcess([], 0, b'{"value": []}', b""),
    ])

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(scanner.subprocess, "run", run)
    monkeypatch.setattr(scanner.time, "sleep", pauses.append)
    result = scanner.azure_read(["rest", "--method", "get", "--url", scanner.ARM + "/test"])
    assert result == {"value": []} and pauses == [1]
    assert len(calls) == 2 and calls[0][0][:4] == ["az", "rest", "--method", "get"]
    assert calls[0][1] == {"capture_output": True, "timeout": 90}
    assert calls[0][0][-3:] == ["--only-show-errors", "--output", "json"]


def test_failed_cli_transport_does_not_expose_raw_authentication_errors(monkeypatch):
    monkeypatch.setattr(scanner.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, b"", b"403 private-session-detail"))
    with pytest.raises(scanner.CollectionError) as failure:
        scanner.azure_read(["account", "show"])
    assert "private-session-detail" not in str(failure.value)


def test_a_cli_timeout_is_a_collection_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("az", 90)

    monkeypatch.setattr(scanner.subprocess, "run", timeout)
    with pytest.raises(scanner.CollectionError, match="timed out"):
        scanner.azure_read(["account", "show"])


def test_pinned_role_collection_retains_permission_exclusions():
    target = target_document()
    pin = {"id": SCOPE + "/providers/Microsoft.Authorization/roleDefinitions/" + ASSIGNMENT["roleDefinitionId"],
           "permissions": [{"actions": ["*"], "notActions": ["Microsoft.Authorization/*/write"], "dataActions": [], "notDataActions": []}]}
    target["roleDefinitions"] = [pin]
    target = TargetState.model_validate(target)
    responses = fixture_reads()
    url = scanner.ARM + target.roleDefinitions[0].id + "?api-version=" + scanner.API_VERSION
    responses[("rest", "--method", "get", "--url", url)] = {"id": pin["id"], "properties": {"permissions": pin["permissions"]}}
    observed = scanner.collect(target, lambda args: responses[tuple(args)])
    assert observed.roleDefinitions[0].permissions[0].notActions == ["Microsoft.Authorization/*/write"]
    assert compare(target, observed)["status"] == "in-sync"
