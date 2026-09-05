import pytest

from rolegraph.domain.ids import (
    ScopeKind,
    ScopeParseError,
    normalize_scope,
    parent_scope_from_string,
    parse_scope,
)


def test_tenant_root_scope():
    ref = parse_scope("/")
    assert ref.kind is ScopeKind.TENANT
    assert ref.scope == "/"


def test_management_group_scope():
    ref = parse_scope("/providers/Microsoft.Management/managementGroups/Production")
    assert ref.kind is ScopeKind.MANAGEMENT_GROUP
    assert ref.name == "production"
    assert ref.raw.endswith("Production")


def test_subscription_scope():
    ref = parse_scope("/subscriptions/0000-1111")
    assert ref.kind is ScopeKind.SUBSCRIPTION
    assert ref.subscription_id == "0000-1111"


def test_resource_group_scope():
    ref = parse_scope("/subscriptions/0000-1111/resourceGroups/rg-payments")
    assert ref.kind is ScopeKind.RESOURCE_GROUP
    assert ref.resource_group == "rg-payments"


def test_resource_scope():
    ref = parse_scope(
        "/subscriptions/0000-1111/resourceGroups/rg-payments"
        "/providers/Microsoft.Storage/storageAccounts/stpayments"
    )
    assert ref.kind is ScopeKind.RESOURCE
    assert ref.name == "stpayments"
    assert ref.provider_path == "microsoft.storage/storageaccounts"


def test_scope_casing_and_trailing_slash_are_normalized():
    a = normalize_scope("/Subscriptions/ABC/ResourceGroups/RG1/")
    b = normalize_scope("/subscriptions/abc/resourcegroups/rg1")
    assert a == b


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "subscriptions/abc",
        "/notascope/abc",
        "/subscriptions",
        "/subscriptions/abc/resourceGroups",
        "/providers/Microsoft.Management/managementGroups/",
    ],
)
def test_malformed_scopes_are_rejected(bad):
    with pytest.raises(ScopeParseError):
        parse_scope(bad)


def test_parent_derived_from_string_only_below_subscription():
    assert parent_scope_from_string("/") is None
    assert parent_scope_from_string("/providers/Microsoft.Management/managementGroups/P") is None
    assert parent_scope_from_string("/subscriptions/abc") is None
    assert parent_scope_from_string("/subscriptions/abc/resourceGroups/rg1") == "/subscriptions/abc"
    assert (
        parent_scope_from_string(
            "/subscriptions/abc/resourceGroups/rg1/providers/Microsoft.Storage/storageAccounts/s"
        )
        == "/subscriptions/abc/resourcegroups/rg1"
    )
