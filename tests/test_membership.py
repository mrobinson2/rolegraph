from rolegraph.domain.entities import GroupMembership
from rolegraph.resolver.membership import (
    describe_chain,
    direct_group_ids,
    effective_group_ids,
    membership_cycles,
    membership_paths,
    transitive_members,
)

from .factories import GraphBuilder


def nested_graph():
    """u-dev -> Developers -> All-Engineering -> Platform-Admins, plus a shortcut."""
    b = GraphBuilder()
    b.user("u-dev", "Dev Devlin")
    b.user("u-jane", "Jane Smith")
    b.group("g-dev", "Azure-Developers")
    b.group("g-eng", "All-Engineering")
    b.group("g-admins", "Azure-Platform-Admins")
    b.member_of("u-dev", "g-dev")
    b.member_of("g-dev", "g-eng")
    b.member_of("g-eng", "g-admins")
    b.member_of("u-jane", "g-admins")
    return b.build()


def test_direct_membership_is_depth_one():
    g = nested_graph()
    paths = membership_paths(g, "u-jane")
    assert len(paths) == 1
    assert paths[0].group_id == "g-admins"
    assert paths[0].is_direct
    assert not paths[0].is_nested


def test_nested_membership_walks_all_levels():
    g = nested_graph()
    groups = effective_group_ids(g, "u-dev")
    assert groups == {"g-dev", "g-eng", "g-admins"}


def test_nested_path_records_the_full_chain():
    g = nested_graph()
    admin_path = next(p for p in membership_paths(g, "u-dev") if p.group_id == "g-admins")
    assert admin_path.chain == ("g-dev", "g-eng", "g-admins")
    assert admin_path.depth == 3
    assert admin_path.is_nested


def test_direct_groups_exclude_nested_ones():
    g = nested_graph()
    assert direct_group_ids(g, "u-dev") == ["g-dev"]


def test_two_routes_to_the_same_group_are_both_returned():
    b = GraphBuilder()
    b.user("u-dev", "Dev Devlin")
    b.group("g-dev", "Azure-Developers")
    b.group("g-eng", "All-Engineering")
    b.member_of("u-dev", "g-dev")
    b.member_of("u-dev", "g-eng")
    b.member_of("g-dev", "g-eng")
    g = b.build()
    eng_paths = [p for p in membership_paths(g, "u-dev") if p.group_id == "g-eng"]
    assert sorted(p.chain for p in eng_paths) == [("g-dev", "g-eng"), ("g-eng",)]


def test_membership_cycle_terminates():
    b = GraphBuilder()
    b.user("u-1", "Looper")
    b.group("g-a", "A")
    b.group("g-b", "B")
    b.member_of("u-1", "g-a")
    b.member_of("g-a", "g-b")
    b.member_of("g-b", "g-a")
    g = b.build()
    assert effective_group_ids(g, "u-1") == {"g-a", "g-b"}


def test_membership_cycles_are_detected():
    b = GraphBuilder()
    b.group("g-a", "A")
    b.group("g-b", "B")
    b.member_of("g-a", "g-b")
    b.member_of("g-b", "g-a")
    g = b.build()
    cycles = membership_cycles(g)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"g-a", "g-b"}


def test_no_cycle_reported_for_a_clean_graph():
    assert membership_cycles(nested_graph()) == []


def test_transitive_members_expands_downward():
    g = nested_graph()
    assert transitive_members(g, "g-admins") == {"g-eng", "g-dev", "u-dev", "u-jane"}


def test_principal_with_no_groups():
    b = GraphBuilder()
    b.user("u-solo", "Solo")
    g = b.build()
    assert membership_paths(g, "u-solo") == []
    assert effective_group_ids(g, "u-solo") == set()


def test_unknown_principal_has_no_memberships():
    assert membership_paths(nested_graph(), "u-nobody") == []


def test_deep_nesting_is_bounded():
    b = GraphBuilder()
    b.user("u-1", "Deep")
    b.member_of("u-1", "g-0")
    for i in range(60):
        b.group(f"g-{i}", f"G{i}")
        b.member_of(f"g-{i}", f"g-{i + 1}")
    b.group("g-60", "G60")
    g = b.build()
    paths = membership_paths(g, "u-1")
    assert max(p.depth for p in paths) <= 20


def test_describe_chain_uses_display_names():
    g = nested_graph()
    assert describe_chain(g, ("g-dev", "g-eng")) == "Azure-Developers > All-Engineering"


def test_membership_to_a_group_that_was_never_imported_is_still_traversed():
    b = GraphBuilder()
    b.user("u-1", "Ghost Member")
    g = b.graph
    g.memberships.append(GroupMembership(group_id="g-missing", member_id="u-1"))
    g.build_indexes()
    assert effective_group_ids(g, "u-1") == {"g-missing"}
