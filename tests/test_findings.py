import json
from pathlib import Path

import pytest

from rolegraph.config import Settings
from rolegraph.findings import evaluate, group_by_rule, registered_rules, summarise
from rolegraph.importer import import_document

from .factories import GraphBuilder

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo" / "contoso.json"


def settings(privileged=("Owner", "Contributor", "User Access Administrator")):
    return Settings(
        database_url="sqlite://",
        data_dir=Path("."),
        demo_dataset=DEMO,
        privileged_roles_file=Path("."),
        max_upload_bytes=1024,
        privileged_roles=tuple(privileged),
    )


@pytest.fixture(scope="module")
def demo_findings():
    graph = import_document(json.loads(DEMO.read_text()), "contoso.json").graph
    return evaluate(graph, settings())


def rules_hit(findings):
    return set(group_by_rule(findings))


# --- engine behaviour ------------------------------------------------------


def test_every_registered_rule_has_a_unique_id():
    ids = registered_rules()
    assert len(ids) == len(set(ids))


def test_evaluation_is_deterministic(demo_findings):
    graph = import_document(json.loads(DEMO.read_text()), "contoso.json").graph
    again = evaluate(graph, settings())
    assert [f.key for f in again] == [f.key for f in demo_findings]


def test_findings_are_sorted_high_severity_first(demo_findings):
    ranks = [f.severity_rank for f in demo_findings]
    assert ranks == sorted(ranks)


def test_every_finding_explains_itself(demo_findings):
    for finding in demo_findings:
        assert finding.what.strip()
        assert finding.why.strip()
        assert finding.title.strip()
        assert finding.severity in {"high", "medium", "low"}


def test_summary_counts_add_up(demo_findings):
    counts = summarise(demo_findings)
    assert counts["high"] + counts["medium"] + counts["low"] == counts["total"]


def test_an_empty_graph_produces_no_findings():
    assert evaluate(GraphBuilder().build(), settings()) == []


# --- individual rules ------------------------------------------------------


def test_direct_owner_is_detected():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Olive Owner")
    b.role("rd-owner", "Owner")
    b.assign("u-1", "rd-owner", "/subscriptions/sub-1")
    hits = group_by_rule(evaluate(b.build(), settings()))["direct-owner"]
    assert hits[0].principal.display_name == "Olive Owner"


def test_owner_on_a_group_is_not_reported_as_a_direct_owner():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.group("g-1", "Admins")
    b.role("rd-owner", "Owner")
    b.assign("g-1", "rd-owner", "/subscriptions/sub-1")
    assert "direct-owner" not in rules_hit(evaluate(b.build(), settings()))


def test_user_access_administrator_is_detected_through_a_group():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Uma")
    b.group("g-1", "Access-Admins")
    b.member_of("u-1", "g-1")
    b.role("rd-uaa", "User Access Administrator", actions=("*/read", "Microsoft.Authorization/*"))
    b.assign("g-1", "rd-uaa", "/subscriptions/sub-1")
    holders = {f.principal.display_name for f in group_by_rule(evaluate(b.build(), settings()))["user-access-administrator"]}
    assert "Uma" in holders


def test_privileged_role_at_management_group_is_detected():
    b = GraphBuilder()
    prod = b.mg("Production")
    b.subscription("sub-1", parent=prod)
    b.group("g-1", "Admins")
    b.role("rd-contributor", "Contributor")
    b.assign("g-1", "rd-contributor", prod)
    hits = group_by_rule(evaluate(b.build(), settings()))["privileged-at-management-group"]
    assert "Production" in hits[0].scope_display


def test_privileged_roles_are_configurable():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Reader Rita")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1")
    graph = b.build()
    assert "privileged-at-subscription" not in rules_hit(evaluate(graph, settings()))
    custom = settings(privileged=("Reader",))
    assert "privileged-at-subscription" in rules_hit(evaluate(graph, custom))


def test_custom_role_wildcard_is_detected():
    b = GraphBuilder()
    b.role("rd-broad", "Broad", actions=("Microsoft.Compute/*",), custom=True)
    hits = group_by_rule(evaluate(b.build(), settings()))["custom-role-wildcard"]
    assert hits[0].role.name == "Broad"


def test_builtin_role_wildcards_are_not_reported():
    b = GraphBuilder()
    b.role("rd-owner", "Owner", actions=("*",))
    assert "custom-role-wildcard" not in rules_hit(evaluate(b.build(), settings()))


def test_custom_role_that_can_assign_roles_is_detected():
    b = GraphBuilder()
    b.role("rd-esc", "Escalator", actions=("Microsoft.Authorization/roleAssignments/write",), custom=True)
    assert "custom-role-can-assign-roles" in rules_hit(evaluate(b.build(), settings()))


def test_privileged_access_via_nested_groups_is_detected():
    b = GraphBuilder()
    prod = b.mg("Production")
    b.user("u-1", "Nested Nick")
    b.group("g-inner", "Inner")
    b.group("g-outer", "Outer")
    b.member_of("u-1", "g-inner")
    b.member_of("g-inner", "g-outer")
    b.role("rd-contributor", "Contributor")
    b.assign("g-outer", "rd-contributor", prod)
    hits = group_by_rule(evaluate(b.build(), settings()))["privileged-via-group"]
    assert hits[0].principal.display_name == "Nested Nick"
    assert "2 levels" in hits[0].what


def test_multiple_paths_to_the_same_access_is_detected():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Twice Tia")
    b.group("g-a", "A")
    b.group("g-b", "B")
    b.member_of("u-1", "g-a")
    b.member_of("u-1", "g-b")
    b.member_of("g-a", "g-b")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("g-b", "rd-reader", "/subscriptions/sub-1")
    hits = group_by_rule(evaluate(b.build(), settings()))["multiple-paths-to-same-access"]
    assert "2 different paths" in hits[0].what


def test_duplicate_assignment_is_detected():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Doubled Dee")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1", assignment_id="ra-a")
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1", assignment_id="ra-b")
    hits = group_by_rule(evaluate(b.build(), settings()))["duplicate-assignment"]
    assert "2 times" in hits[0].what


def test_redundant_narrow_assignment_is_detected():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.rg("sub-1", "rg-1")
    b.user("u-1", "Redundant Ray")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1")
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1/resourceGroups/rg-1")
    hits = group_by_rule(evaluate(b.build(), settings()))["redundant-narrow-assignment"]
    assert hits[0].scope.endswith("rg-1")


def test_unused_custom_role_is_detected():
    b = GraphBuilder()
    b.role("rd-ghost", "Ghost Role", actions=("*/read",), custom=True)
    assert "unused-custom-role" in rules_hit(evaluate(b.build(), settings()))


def test_assigned_custom_role_is_not_reported_as_unused():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "User")
    b.role("rd-used", "Used Role", actions=("*/read",), custom=True)
    b.assign("u-1", "rd-used", "/subscriptions/sub-1")
    assert "unused-custom-role" not in rules_hit(evaluate(b.build(), settings()))


def test_read_only_role_at_broad_scope_is_not_a_write_finding():
    b = GraphBuilder()
    root = b.mg("Root")
    b.subscription("sub-1", parent=root)
    b.subscription("sub-2", parent=root)
    b.user("u-1", "Auditor")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", root)
    assert "write-role-at-broad-scope" not in rules_hit(evaluate(b.build(), settings()))


def test_write_role_at_broad_scope_is_detected():
    b = GraphBuilder()
    root = b.mg("Root")
    b.subscription("sub-1", parent=root)
    b.subscription("sub-2", parent=root)
    b.service_principal("sp-1", "sp-ci")
    b.role("rd-ops", "Ops Role", actions=("Microsoft.Compute/*",), custom=True)
    b.assign("sp-1", "rd-ops", root)
    hits = group_by_rule(evaluate(b.build(), settings()))["write-role-at-broad-scope"]
    assert "2 subscriptions" in hits[0].what


def test_non_human_identity_with_a_privileged_role_is_detected():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.managed_identity("mi-1", "mi-backup")
    b.role("rd-owner", "Owner")
    b.assign("mi-1", "rd-owner", "/subscriptions/sub-1")
    hits = group_by_rule(evaluate(b.build(), settings()))["service-principal-privileged"]
    assert "Managed identity" in hits[0].what


# --- the demo dataset ------------------------------------------------------


def test_demo_surfaces_every_planted_issue(demo_findings):
    hit = rules_hit(demo_findings)
    for expected in (
        "privileged-via-group",            # developer inherits production Contributor
        "direct-owner",                    # service principal owns a subscription
        "service-principal-privileged",
        "custom-role-wildcard",            # overly broad custom role
        "custom-role-can-assign-roles",
        "duplicate-assignment",            # same grant assigned twice
        "multiple-paths-to-same-access",   # duplicate nesting routes
        "privileged-at-management-group",
    ):
        assert expected in hit, expected


def test_demo_finding_ties_dan_to_production(demo_findings):
    finding = next(
        f
        for f in demo_findings
        if f.rule_id == "privileged-via-group"
        and f.principal.display_name == "Dan Okafor"
        and "Production" in f.scope_display
    )
    assert finding.paths
    assert finding.paths[0].membership_chain[-1] == "g-a1-platform-admins"
    assert "Membership chain" in finding.evidence[0]
