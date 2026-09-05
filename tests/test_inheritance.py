"""Inheritance: an assignment applies at its scope and everywhere below it."""

from rolegraph.resolver.access import (
    access_at_scope,
    access_paths,
    assignees_of_role,
    distinct_grants,
    explain,
    explain_inheritance,
    is_inherited_at,
    path_steps,
    principals_with_access_to,
    reached_scopes,
)

from .factories import GraphBuilder, contoso_like


def tenant():
    b = GraphBuilder()
    prod = b.mg("Production")
    b.mg("Payments-Prod", parent=prod)
    b.subscription("sub-pay", parent="/providers/Microsoft.Management/managementGroups/Payments-Prod", display_name="Payments Production")
    b.rg("sub-pay", "rg-api")
    b.resource("sub-pay", "rg-api", "Microsoft.Storage/storageAccounts", "stpay")
    b.mg("NonProduction")
    b.subscription("sub-dev", parent="/providers/Microsoft.Management/managementGroups/NonProduction", display_name="Development")

    b.user("u-jane", "Jane Smith")
    b.group("g-admins", "Azure-Platform-Admins")
    b.member_of("u-jane", "g-admins")
    b.role("rd-contributor", "Contributor", actions=("*",), not_actions=("Microsoft.Authorization/*/Write",))
    b.assign("g-admins", "rd-contributor", "/providers/Microsoft.Management/managementGroups/Production")
    return b.build()


RG = "/subscriptions/sub-pay/resourcegroups/rg-api"
RESOURCE = f"{RG}/providers/microsoft.storage/storageaccounts/stpay"


def test_group_assignment_reaches_the_member():
    g = tenant()
    paths = access_paths(g, "u-jane")
    assert len(paths) == 1
    assert paths[0].role.name == "Contributor"
    assert not paths[0].is_direct
    assert paths[0].membership_chain == ("g-admins",)


def test_assignment_at_management_group_is_inherited_all_the_way_down():
    g = tenant()
    for scope in ("/subscriptions/sub-pay", RG, RESOURCE):
        assert access_at_scope(g, "u-jane", scope), scope


def test_inheritance_does_not_leak_across_sibling_branches():
    g = tenant()
    assert access_at_scope(g, "u-jane", "/subscriptions/sub-dev") == []


def test_access_at_the_assignment_scope_is_not_inherited():
    g = tenant()
    prod = "/providers/microsoft.management/managementgroups/production"
    path = access_at_scope(g, "u-jane", prod)[0]
    assert not is_inherited_at(path, prod)
    assert is_inherited_at(path, RESOURCE)


def test_reached_scopes_cover_the_whole_subtree():
    g = tenant()
    path = access_paths(g, "u-jane")[0]
    reached = reached_scopes(g, path)
    assert RESOURCE in reached
    assert "/subscriptions/sub-dev" not in reached


def test_direct_assignment_beats_no_membership():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Direct Dana")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", "/subscriptions/sub-1")
    g = b.build()
    path = access_paths(g, "u-1")[0]
    assert path.is_direct
    assert path.membership_chain == ()


def test_nested_group_grants_production_access_to_a_developer():
    g = contoso_like()
    paths = access_at_scope(g, "u-dev", "/subscriptions/sub-payments")
    contributor = [p for p in paths if p.role.name == "Contributor"]
    assert contributor, "developer should inherit Contributor into production"
    assert all(p.is_nested_group for p in contributor)


def test_same_grant_reached_by_two_paths_is_grouped():
    g = contoso_like()
    grants = distinct_grants(access_paths(g, "u-dev"))
    duplicated = [key for key, paths in grants.items() if len(paths) > 1]
    assert duplicated, "expected at least one grant reachable by more than one path"


def test_assignment_referencing_an_unknown_role_is_ignored():
    b = GraphBuilder()
    b.subscription("sub-1", parent="/")
    b.user("u-1", "Dangler")
    b.assign("u-1", "rd-does-not-exist", "/subscriptions/sub-1")
    g = b.build()
    assert access_paths(g, "u-1") == []


def test_assignment_at_an_unimported_scope_still_resolves_for_the_identity():
    b = GraphBuilder()
    b.user("u-1", "Off Map")
    b.role("rd-reader", "Reader", actions=("*/read",))
    b.assign("u-1", "rd-reader", "/subscriptions/sub-unknown")
    g = b.build()
    paths = access_paths(g, "u-1")
    assert len(paths) == 1
    assert paths[0].scope_node is None


def test_reverse_lookup_lists_everyone_who_can_reach_a_resource():
    g = tenant()
    names = {p.principal.display_name for p in principals_with_access_to(g, RESOURCE)}
    assert "Jane Smith" in names


def test_assignees_of_role_include_indirect_holders():
    g = tenant()
    holders = {p.principal.display_name for p in assignees_of_role(g, "rd-contributor")}
    assert holders == {"Jane Smith", "Azure-Platform-Admins"}


def test_explanation_is_plain_english():
    g = tenant()
    sentence = explain(g, access_paths(g, "u-jane")[0])
    assert "Jane Smith" in sentence
    assert "Azure-Platform-Admins" in sentence
    assert "Contributor" in sentence
    assert "management group" in sentence


def test_nested_explanation_names_every_group_in_the_chain():
    g = contoso_like()
    path = next(
        p for p in access_paths(g, "u-dev") if p.role.name == "Contributor" and p.is_nested_group
    )
    sentence = explain(g, path)
    assert "Azure-Platform-Admins" in sentence
    assert "All-Engineering" in sentence


def test_inheritance_explanation_shows_the_scope_trail():
    g = tenant()
    path = access_paths(g, "u-jane")[0]
    text = explain_inheritance(g, path, RESOURCE)
    assert text.startswith("Inherited from Production")
    assert "rg-api" in text and "stpay" in text


def test_path_steps_are_ordered_identity_groups_role_scope():
    g = tenant()
    steps = path_steps(g, access_paths(g, "u-jane")[0])
    assert [s["kind"] for s in steps] == ["identity", "group", "role", "scope"]
    assert steps[0]["label"] == "Jane Smith"
    assert steps[-1]["breadcrumb"].startswith("Contoso > Production")
