#!/usr/bin/env python3
"""Generate the synthetic Contoso demo tenant.

Run: ``python scripts/generate_contoso_demo.py``

The dataset is deliberately imperfect. Each planted problem is listed in
``$notes.intentionalIssues`` so the demo can be explained to a customer without
anyone wondering whether it is a bug.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "demo" / "contoso.json"

RD = "/providers/Microsoft.Authorization/roleDefinitions"
OWNER = f"{RD}/8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
CONTRIBUTOR = f"{RD}/b24988ac-6180-42a0-ab88-20f7382dd24c"
READER = f"{RD}/acdd72a7-3385-48ef-bd42-f606fba81ae7"
UAA = f"{RD}/18d7d88d-d35e-4fb5-a5c3-7773c20a72d9"
KV_SECRETS_USER = f"{RD}/4633458b-17de-408a-b874-0445c86b69e6"
BLOB_CONTRIBUTOR = f"{RD}/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
PLATFORM_OPERATOR = f"{RD}/c1f0a8de-6b1e-4c1e-9a1e-2f77b0c3a911"
DEPLOY_AUTOMATION = f"{RD}/d47b2f60-9f31-4a7f-88a2-6b0f2d5c7e22"
LEGACY_READER = f"{RD}/e90c1b44-2a55-4f8e-9d33-71bb4c9f0a10"

SUB_PAY = "00000000-0000-0000-0000-0000000000a1"
SUB_CUST = "00000000-0000-0000-0000-0000000000a2"
SUB_SHARED = "00000000-0000-0000-0000-0000000000a3"
SUB_DEV = "00000000-0000-0000-0000-0000000000a4"


def mg(scope: str) -> str:
    return f"/providers/Microsoft.Management/managementGroups/{scope}"


def sub(sub_id: str) -> str:
    return f"/subscriptions/{sub_id}"


def rg(sub_id: str, name: str) -> str:
    return f"/subscriptions/{sub_id}/resourceGroups/{name}"


def res(sub_id: str, group: str, rtype: str, name: str) -> str:
    return f"{rg(sub_id, group)}/providers/{rtype}/{name}"


document = {
    "$schema": "rolegraph-import/v1",
    "$notes": {
        "description": "Synthetic Contoso tenant. No real identities, subscriptions or resources.",
        "intentionalIssues": [
            "All-Engineering is nested inside Azure-Platform-Admins, so every developer "
            "silently inherits Contributor on the whole Production management group.",
            "sp-payments-deploy holds Owner directly at the Payments Production subscription.",
            "Contoso-Platform-Operator is a custom role with wildcard permissions across "
            "compute, storage and network.",
            "Contoso-Deployment-Automation is a custom role that can create role assignments, "
            "so anyone holding it can escalate their own privilege.",
            "Payments-Squad has Contributor assigned twice on rg-payments-api, and also "
            "inherits Contributor from the Production management group.",
            "Dan Okafor reaches Azure-Developers by two different nesting paths.",
            "Priya Raman holds Owner directly on the Customer Production subscription.",
        ],
    },
    "tenants": [
        {"id": "11111111-2222-3333-4444-555555555555", "displayName": "Contoso Ltd", "domain": "contoso.onmicrosoft.com"}
    ],
    "managementGroups": [
        {"id": "Contoso-Root", "displayName": "Contoso Root", "parent": "/"},
        {"id": "Production", "displayName": "Production", "parent": "Contoso-Root"},
        {"id": "NonProduction", "displayName": "Non-Production", "parent": "Contoso-Root"},
        {"id": "Payments-Prod", "displayName": "Payments-Prod", "parent": "Production"},
        {"id": "Customer-Prod", "displayName": "Customer-Prod", "parent": "Production"},
        {"id": "Development", "displayName": "Development", "parent": "NonProduction"},
    ],
    "subscriptions": [
        {"subscriptionId": SUB_PAY, "displayName": "Payments Production", "managementGroup": "Payments-Prod"},
        {"subscriptionId": SUB_CUST, "displayName": "Customer Production", "managementGroup": "Customer-Prod"},
        {"subscriptionId": SUB_SHARED, "displayName": "Shared Production Services", "managementGroup": "Production"},
        {"subscriptionId": SUB_DEV, "displayName": "Development", "managementGroup": "Development"},
    ],
    "resourceGroups": [
        {"name": "rg-payments-api", "subscriptionId": SUB_PAY, "location": "westeurope"},
        {"name": "rg-payments-data", "subscriptionId": SUB_PAY, "location": "westeurope"},
        {"name": "rg-customer-web", "subscriptionId": SUB_CUST, "location": "northeurope"},
        {"name": "rg-shared-network", "subscriptionId": SUB_SHARED, "location": "westeurope"},
        {"name": "rg-dev-sandbox", "subscriptionId": SUB_DEV, "location": "westeurope"},
    ],
    "resources": [
        {"id": res(SUB_PAY, "rg-payments-api", "Microsoft.Web/sites", "app-payments-api"),
         "name": "app-payments-api", "type": "Microsoft.Web/sites", "location": "westeurope"},
        {"id": res(SUB_PAY, "rg-payments-data", "Microsoft.Storage/storageAccounts", "stpaymentsprod"),
         "name": "stpaymentsprod", "type": "Microsoft.Storage/storageAccounts", "location": "westeurope"},
        {"id": res(SUB_PAY, "rg-payments-data", "Microsoft.KeyVault/vaults", "kv-payments-prod"),
         "name": "kv-payments-prod", "type": "Microsoft.KeyVault/vaults", "location": "westeurope"},
        {"id": res(SUB_CUST, "rg-customer-web", "Microsoft.Sql/servers", "sql-customer-prod"),
         "name": "sql-customer-prod", "type": "Microsoft.Sql/servers", "location": "northeurope"},
        {"id": res(SUB_SHARED, "rg-shared-network", "Microsoft.Network/virtualNetworks", "vnet-hub"),
         "name": "vnet-hub", "type": "Microsoft.Network/virtualNetworks", "location": "westeurope"},
        {"id": res(SUB_DEV, "rg-dev-sandbox", "Microsoft.Storage/storageAccounts", "stdevsandbox"),
         "name": "stdevsandbox", "type": "Microsoft.Storage/storageAccounts", "location": "westeurope"},
    ],
    "roleDefinitions": [
        {"id": OWNER, "roleName": "Owner", "roleType": "BuiltInRole",
         "description": "Grants full access to manage all resources, including assigning roles.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}]},
        {"id": CONTRIBUTOR, "roleName": "Contributor", "roleType": "BuiltInRole",
         "description": "Grants full access to manage all resources, but cannot assign roles.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["*"],
                          "notActions": ["Microsoft.Authorization/*/Delete",
                                         "Microsoft.Authorization/*/Write",
                                         "Microsoft.Authorization/elevateAccess/Action"],
                          "dataActions": [], "notDataActions": []}]},
        {"id": READER, "roleName": "Reader", "roleType": "BuiltInRole",
         "description": "View all resources, but does not allow you to make any changes.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["*/read"], "notActions": [], "dataActions": [], "notDataActions": []}]},
        {"id": UAA, "roleName": "User Access Administrator", "roleType": "BuiltInRole",
         "description": "Lets you manage user access to Azure resources.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["*/read", "Microsoft.Authorization/*",
                                      "Microsoft.Support/*"],
                          "notActions": [], "dataActions": [], "notDataActions": []}]},
        {"id": KV_SECRETS_USER, "roleName": "Key Vault Secrets User", "roleType": "BuiltInRole",
         "description": "Read secret contents.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["Microsoft.KeyVault/vaults/read"], "notActions": [],
                          "dataActions": ["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
                          "notDataActions": []}]},
        {"id": BLOB_CONTRIBUTOR, "roleName": "Storage Blob Data Contributor", "roleType": "BuiltInRole",
         "description": "Read, write and delete Azure Storage containers and blobs.",
         "assignableScopes": ["/"],
         "permissions": [{"actions": ["Microsoft.Storage/storageAccounts/blobServices/containers/*"],
                          "notActions": [],
                          "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
                          "notDataActions": []}]},
        {"id": PLATFORM_OPERATOR, "roleName": "Contoso-Platform-Operator", "roleType": "CustomRole",
         "description": "Day-two operations across the platform estate.",
         "assignableScopes": [mg("Contoso-Root")],
         "permissions": [{"actions": ["Microsoft.Compute/*", "Microsoft.Storage/*",
                                      "Microsoft.Network/*", "Microsoft.Resources/*",
                                      "Microsoft.Authorization/*/read"],
                          "notActions": [], "dataActions": [], "notDataActions": []}]},
        {"id": DEPLOY_AUTOMATION, "roleName": "Contoso-Deployment-Automation", "roleType": "CustomRole",
         "description": "Used by the deployment pipeline to provision and wire up workloads.",
         "assignableScopes": [mg("Production")],
         "permissions": [{"actions": ["Microsoft.Resources/deployments/*",
                                      "Microsoft.Authorization/roleAssignments/write",
                                      "Microsoft.Authorization/roleAssignments/read",
                                      "Microsoft.Web/sites/*"],
                          "notActions": [], "dataActions": [], "notDataActions": []}]},
        {"id": LEGACY_READER, "roleName": "Contoso-Legacy-Auditor", "roleType": "CustomRole",
         "description": "Left over from the 2019 audit programme. No longer assigned to anyone.",
         "assignableScopes": [mg("Contoso-Root")],
         "permissions": [{"actions": ["*/read"], "notActions": [], "dataActions": [], "notDataActions": []}]},
    ],
    "users": [
        {"id": "u-1a2b3c4d-jane", "displayName": "Jane Smith",
         "userPrincipalName": "jane.smith@contoso.com", "mail": "jane.smith@contoso.com",
         "department": "Cloud Platform"},
        {"id": "u-2b3c4d5e-dan", "displayName": "Dan Okafor",
         "userPrincipalName": "dan.okafor@contoso.com", "mail": "dan.okafor@contoso.com",
         "department": "Payments Engineering"},
        {"id": "u-3c4d5e6f-priya", "displayName": "Priya Raman",
         "userPrincipalName": "priya.raman@contoso.com", "mail": "priya.raman@contoso.com",
         "department": "Security Engineering"},
        {"id": "u-4d5e6f70-marco", "displayName": "Marco Bianchi",
         "userPrincipalName": "marco.bianchi@contoso.com", "mail": "marco.bianchi@contoso.com",
         "department": "Payments Engineering"},
        {"id": "u-5e6f7081-sofia", "displayName": "Sofia Lindqvist",
         "userPrincipalName": "sofia.lindqvist@contoso.com", "mail": "sofia.lindqvist@contoso.com",
         "department": "Cloud Platform"},
        {"id": "u-6f708192-tom", "displayName": "Tom Becker",
         "userPrincipalName": "tom.becker@contoso.com", "mail": "tom.becker@contoso.com",
         "department": "Finance"},
    ],
    "groups": [
        {"id": "g-a1-platform-admins", "displayName": "Azure-Platform-Admins",
         "description": "Owns the Azure platform. Highly privileged."},
        {"id": "g-a2-developers", "displayName": "Azure-Developers",
         "description": "All application developers."},
        {"id": "g-a3-all-engineering", "displayName": "All-Engineering",
         "description": "Umbrella group for every engineering function."},
        {"id": "g-a4-payments-squad", "displayName": "Payments-Squad",
         "description": "The team that builds the payments platform."},
        {"id": "g-a5-security-engineering", "displayName": "Security-Engineering",
         "description": "Security engineering function."},
        {"id": "g-a6-contractors", "displayName": "Contractors",
         "description": "Third-party contractors with time-boxed access."},
    ],
    "servicePrincipals": [
        {"id": "sp-b1-payments-deploy", "displayName": "sp-payments-deploy",
         "appId": "aaaaaaaa-1111-2222-3333-444444444444",
         "description": "Release pipeline for the payments platform."},
        {"id": "sp-b2-terraform-ci", "displayName": "sp-terraform-ci",
         "appId": "bbbbbbbb-1111-2222-3333-444444444444",
         "description": "Terraform CI runner."},
    ],
    "managedIdentities": [
        {"id": "mi-c1-payments-api", "displayName": "mi-payments-api",
         "description": "System-assigned identity of app-payments-api."},
        {"id": "mi-c2-backup-runner", "displayName": "mi-backup-runner",
         "description": "Nightly backup job identity."},
    ],
    "groupMemberships": [
        {"groupId": "g-a1-platform-admins", "memberId": "u-1a2b3c4d-jane"},
        {"groupId": "g-a1-platform-admins", "memberId": "u-5e6f7081-sofia"},
        {"groupId": "g-a2-developers", "memberId": "u-2b3c4d5e-dan"},
        {"groupId": "g-a2-developers", "memberId": "u-4d5e6f70-marco"},
        {"groupId": "g-a4-payments-squad", "memberId": "u-2b3c4d5e-dan"},
        {"groupId": "g-a5-security-engineering", "memberId": "u-3c4d5e6f-priya"},
        {"groupId": "g-a6-contractors", "memberId": "u-6f708192-tom"},
        {"groupId": "g-a2-developers", "memberId": "g-a4-payments-squad"},
        {"groupId": "g-a3-all-engineering", "memberId": "g-a2-developers"},
        {"groupId": "g-a3-all-engineering", "memberId": "g-a5-security-engineering"},
        # The planted misconfiguration: the umbrella engineering group is a member
        # of the platform admin group, so every developer inherits production access.
        {"groupId": "g-a1-platform-admins", "memberId": "g-a3-all-engineering"},
    ],
    "roleAssignments": [
        {"id": "ra-0001", "principalId": "g-a1-platform-admins", "principalType": "Group",
         "roleDefinitionId": CONTRIBUTOR, "scope": mg("Production"),
         "createdOn": "2024-03-11T09:14:00Z", "createdBy": "jane.smith@contoso.com",
         "description": "Platform admins manage everything in production."},
        {"id": "ra-0002", "principalId": "sp-b1-payments-deploy", "principalType": "ServicePrincipal",
         "roleDefinitionId": OWNER, "scope": sub(SUB_PAY),
         "createdOn": "2023-11-02T16:40:00Z", "createdBy": "sofia.lindqvist@contoso.com",
         "description": "Temporary during migration - never revoked."},
        {"id": "ra-0003", "principalId": "u-1a2b3c4d-jane", "principalType": "User",
         "roleDefinitionId": UAA, "scope": sub(SUB_SHARED),
         "createdOn": "2024-01-20T11:02:00Z"},
        {"id": "ra-0004", "principalId": "g-a2-developers", "principalType": "Group",
         "roleDefinitionId": PLATFORM_OPERATOR, "scope": sub(SUB_DEV),
         "createdOn": "2024-06-05T08:30:00Z"},
        {"id": "ra-0005", "principalId": "g-a5-security-engineering", "principalType": "Group",
         "roleDefinitionId": READER, "scope": mg("Contoso-Root"),
         "createdOn": "2023-09-15T13:00:00Z"},
        {"id": "ra-0006", "principalId": "mi-c1-payments-api", "principalType": "ManagedIdentity",
         "roleDefinitionId": KV_SECRETS_USER,
         "scope": res(SUB_PAY, "rg-payments-data", "Microsoft.KeyVault/vaults", "kv-payments-prod"),
         "createdOn": "2024-04-18T10:12:00Z"},
        {"id": "ra-0007", "principalId": "u-4d5e6f70-marco", "principalType": "User",
         "roleDefinitionId": CONTRIBUTOR, "scope": rg(SUB_DEV, "rg-dev-sandbox"),
         "createdOn": "2024-07-01T15:45:00Z"},
        {"id": "ra-0008", "principalId": "sp-b2-terraform-ci", "principalType": "ServicePrincipal",
         "roleDefinitionId": DEPLOY_AUTOMATION, "scope": mg("Production"),
         "createdOn": "2024-02-27T07:20:00Z"},
        {"id": "ra-0009", "principalId": "g-a4-payments-squad", "principalType": "Group",
         "roleDefinitionId": CONTRIBUTOR, "scope": rg(SUB_PAY, "rg-payments-api"),
         "createdOn": "2024-05-09T09:00:00Z"},
        {"id": "ra-0010", "principalId": "u-6f708192-tom", "principalType": "User",
         "roleDefinitionId": READER, "scope": sub(SUB_CUST),
         "createdOn": "2024-08-12T12:30:00Z"},
        {"id": "ra-0011", "principalId": "u-3c4d5e6f-priya", "principalType": "User",
         "roleDefinitionId": OWNER, "scope": sub(SUB_CUST),
         "createdOn": "2024-06-30T17:05:00Z",
         "description": "Granted during an incident and never removed."},
        {"id": "ra-0012", "principalId": "mi-c2-backup-runner", "principalType": "ManagedIdentity",
         "roleDefinitionId": BLOB_CONTRIBUTOR,
         "scope": res(SUB_PAY, "rg-payments-data", "Microsoft.Storage/storageAccounts", "stpaymentsprod"),
         "createdOn": "2024-03-03T02:00:00Z"},
        {"id": "ra-0013", "principalId": "g-a6-contractors", "principalType": "Group",
         "roleDefinitionId": CONTRIBUTOR, "scope": sub(SUB_DEV),
         "createdOn": "2024-09-01T09:30:00Z"},
        {"id": "ra-0014", "principalId": "u-5e6f7081-sofia", "principalType": "User",
         "roleDefinitionId": CONTRIBUTOR, "scope": mg("NonProduction"),
         "createdOn": "2024-01-05T10:00:00Z"},
        # Same grant as ra-0009, created again by a different process.
        {"id": "ra-0015", "principalId": "g-a4-payments-squad", "principalType": "Group",
         "roleDefinitionId": CONTRIBUTOR, "scope": rg(SUB_PAY, "rg-payments-api"),
         "createdOn": "2024-10-22T14:10:00Z", "createdBy": "sp-terraform-ci"},
    ],
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    counts = {k: len(v) for k, v in document.items() if isinstance(v, list)}
    print(f"wrote {OUT}")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
