from rolegraph.domain.entities import ScopeNode
from rolegraph.domain.hierarchy import (
    ancestors,
    covers,
    descendants,
    is_ancestor_of,
    orphan_scopes,
    scope_breadcrumb,
    scope_chain,
)
from rolegraph.domain.ids import ScopeKind

from .factories import GraphBuilder


def build_tree():
    b = GraphBuilder()
    b.mg("Production")
    b.mg("Payments-Prod", parent="/providers/Microsoft.Management/managementGroups/Production")
    b.subscription("sub-1", parent="/providers/Microsoft.Management/managementGroups/Payments-Prod")
    b.rg("sub-1", "rg-api")
    b.resource("sub-1", "rg-api", "Microsoft.Storage/storageAccounts", "stpay")
    return b.build()


def test_ancestors_run_from_nearest_parent_to_root():
    g = build_tree()
    resource = "/subscriptions/sub-1/resourcegroups/rg-api/providers/microsoft.storage/storageaccounts/stpay"
    names = [n.name for n in ancestors(g, resource)]
    assert names == ["rg-api", "sub-1", "Payments-Prod", "Production", "Contoso"]


def test_scope_chain_is_root_down_and_inclusive():
    g = build_tree()
    chain = [n.name for n in scope_chain(g, "/subscriptions/sub-1")]
    assert chain == ["Contoso", "Production", "Payments-Prod", "sub-1"]


def test_descendants_include_everything_beneath():
    g = build_tree()
    below = descendants(g, "/providers/microsoft.management/managementgroups/production")
    assert "/subscriptions/sub-1" in below
    assert "/subscriptions/sub-1/resourcegroups/rg-api" in below
    assert len(below) == 4


def test_descendants_can_include_self():
    g = build_tree()
    scope = "/subscriptions/sub-1"
    assert scope in descendants(g, scope, include_self=True)
    assert scope not in descendants(g, scope)


def test_is_ancestor_of_is_strict():
    g = build_tree()
    mg = "/providers/microsoft.management/managementgroups/production"
    assert is_ancestor_of(g, mg, "/subscriptions/sub-1")
    assert not is_ancestor_of(g, "/subscriptions/sub-1", mg)
    assert not is_ancestor_of(g, mg, mg)


def test_covers_includes_the_scope_itself():
    g = build_tree()
    mg = "/providers/microsoft.management/managementgroups/production"
    assert covers(g, mg, mg)
    assert covers(g, mg, "/subscriptions/sub-1")
    assert not covers(g, "/subscriptions/sub-1", mg)


def test_sibling_management_groups_do_not_cover_each_other():
    b = GraphBuilder()
    b.mg("Production")
    b.mg("NonProduction")
    b.subscription("sub-prod", parent="/providers/Microsoft.Management/managementGroups/Production")
    b.subscription("sub-dev", parent="/providers/Microsoft.Management/managementGroups/NonProduction")
    g = b.build()
    prod = "/providers/microsoft.management/managementgroups/production"
    assert covers(g, prod, "/subscriptions/sub-prod")
    assert not covers(g, prod, "/subscriptions/sub-dev")


def test_breadcrumb_reads_top_down():
    g = build_tree()
    assert scope_breadcrumb(g, "/subscriptions/sub-1") == "Contoso > Production > Payments-Prod > sub-1"


def test_cycle_in_scope_parents_does_not_hang():
    b = GraphBuilder()
    g = b.build()
    g.scopes["/a"] = ScopeNode("/a", "/a", ScopeKind.MANAGEMENT_GROUP, "a", "A", parent_scope="/b")
    g.scopes["/b"] = ScopeNode("/b", "/b", ScopeKind.MANAGEMENT_GROUP, "b", "B", parent_scope="/a")
    g.build_indexes()
    assert len(ancestors(g, "/a")) <= 2


def test_orphan_scope_is_reported():
    b = GraphBuilder()
    b.subscription("sub-x", parent="/providers/Microsoft.Management/managementGroups/Missing")
    g = b.build()
    assert [n.name for n in orphan_scopes(g)] == ["sub-x"]


def test_unknown_scope_yields_empty_chain():
    g = build_tree()
    assert scope_chain(g, "/subscriptions/does-not-exist") == []
