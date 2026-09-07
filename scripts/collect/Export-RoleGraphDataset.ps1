#Requires -Version 7.4
<#
.SYNOPSIS
Export a tenant's visible Azure RBAC graph to one RoleGraph JSON file.
.DESCRIPTION
Uses an existing Azure CLI login and Microsoft Graph PowerShell session for
the same tenant. Reads only. Does not log in, install modules, change the
selected subscription, or create directories. DryRun needs neither session.
See docs/COLLECTION.md for permissions, coverage and authentication-cache limits.
.EXAMPLE
./Export-RoleGraphDataset.ps1 -OutputPath ./tenant.json -DryRun
.EXAMPLE
./Export-RoleGraphDataset.ps1 -OutputPath ./tenant.json -TenantId <tenant-guid> -IncludeResources
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$OutputPath,
    [string]$TenantId,
    [switch]$IncludeResources,
    [switch]$DryRun,
    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$warnings = [System.Collections.Generic.List[object]]::new()

function Get-Field($Record, [string]$Name, $Default = $null) {
    if ($null -eq $Record) { return $Default }
    if ($Record -is [System.Collections.IDictionary]) {
        if ($Record.Contains($Name)) { return ,$Record[$Name] }
    } elseif ($Record.PSObject.Properties[$Name]) { return ,$Record.$Name }
    return ,$Default
}

function Require-Text($Value, [string]$Label) {
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing or invalid $Label in a required response. Export stopped."
    }
    return $Value.Trim()
}

function Add-CollectionWarning([string]$Category, [string]$Message) {
    $warnings.Add(@{ category = $Category; message = $Message })
    [Console]::Error.WriteLine("Warning: $Message")
}

function Get-OutputFile {
    $provider = $null
    $drive = $null
    $path = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath, [ref]$provider, [ref]$drive)
    if ($provider.Name -ne 'FileSystem' -or [IO.Path]::GetExtension($path) -ine '.json') {
        throw 'OutputPath must be a filesystem .json file.'
    }
    if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($path))) {
        throw 'The output directory must already exist; the collector does not create directories.'
    }
    # Reject links in both the destination and its parents (including dangling links).
    $cursor = $path
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'OutputPath must not use a symbolic link, junction, or other reparse point.'
        }
        if ($entry -and $entry.PSIsContainer -and $cursor -eq $path) { throw 'OutputPath is a directory.' }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    if ([IO.File]::Exists($path) -and -not $Overwrite) {
        throw 'Output file already exists. Choose a new filename or explicitly use -Overwrite.'
    }
    return $path
}

function Read-Azure([string[]]$Arguments, [switch]$Single) {
    # Fixed read-only command vocabulary; no shell evaluation or account set.
    $verb = ($Arguments | Select-Object -First 3) -join ' '
    if ($verb -notmatch '^(account (show|list|management-group)|group list|resource list|role (definition|assignment) (list|show))') {
        throw 'Unsupported Azure read operation.'
    }
    $json = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure read failed: $($Arguments -join ' '). No export was written." }
    try { $data = ($json -join "`n") | ConvertFrom-Json -AsHashtable -NoEnumerate }
    catch { throw "Azure returned invalid JSON for $($Arguments -join ' ')." }
    if ($Single) {
        if ($data -isnot [System.Collections.IDictionary]) { throw 'Azure returned an invalid object response.' }
    } elseif ($data -isnot [array]) { throw 'Azure returned an invalid collection response.' }
    return ,$data
}

function Read-Graph([string]$Url) {
    $result = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    while ($Url) {
        $uri = [uri]$Url
        if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or $uri.Host -ne 'graph.microsoft.com' -or
            $uri.Port -ne 443 -or $uri.UserInfo -or -not $uri.AbsolutePath.StartsWith('/v1.0/')) {
            throw 'Graph returned an unexpected pagination URL; export stopped.'
        }
        if (-not $seen.Add($Url) -or $seen.Count -gt 10000) { throw 'Graph pagination repeated or exceeded 10,000 pages; export stopped.' }
        try { $page = Invoke-MgGraphRequest -Method GET -Uri $Url -OutputType Hashtable }
        catch { throw "Graph read failed for $($uri.AbsolutePath). Check read permissions and retry; no export was written." }
        $items = Get-Field $page 'value'
        if ($items -isnot [array]) { throw 'Graph returned an invalid collection response.' }
        foreach ($item in $items) {
            if ($item -isnot [System.Collections.IDictionary]) { throw 'Graph returned an invalid record.' }
            $result.Add($item)
        }
        $Url = Get-Field $page '@odata.nextLink' ''
    }
    return ,$result.ToArray()
}

function Add-Record($Records, [string]$Section, [string]$Key, $Record) {
    if ($Records[$Section].ContainsKey($Key)) {
        $prior = ConvertTo-Json $Records[$Section][$Key] -Depth 40 -Compress
        $current = ConvertTo-Json $Record -Depth 40 -Compress
        if ($prior -cne $current) { throw "Conflicting $Section records for '$Key'. Retry a stable collection." }
        Add-CollectionWarning 'duplicate' "Repeated $Section record '$Key' was deduplicated."
    } else { $Records[$Section][$Key] = $Record }
}

function Get-RoleId($Raw) {
    $id = Require-Text $Raw 'role definition ID'
    $guid = [guid]::Empty
    if (-not [guid]::TryParse(($id.TrimEnd('/') -split '/')[-1], [ref]$guid)) { throw 'Invalid role definition GUID.' }
    # ARM can return the same definition under multiple subscription prefixes.
    return '/providers/Microsoft.Authorization/roleDefinitions/' + $guid.ToString()
}

function Add-Role($Records, $Raw) {
    $r = Get-Field $Raw 'properties' $Raw
    $id = Get-RoleId (Get-Field $Raw 'id')
    $blocks = Get-Field $r 'permissions'
    if ($blocks -isnot [array]) { throw "Role '$id' has no permission-block array." }
    $permissions = @(foreach ($block in $blocks) {
        $mapped = [ordered]@{}
        foreach ($field in @('actions', 'notActions', 'dataActions', 'notDataActions')) {
            $values = Get-Field $block $field @()
            if ($values -isnot [array]) { throw "Invalid $field on role '$id'." }
            $mapped[$field] = @($values | Sort-Object)
        }
        $mapped
    })
    Add-Record $Records 'roleDefinitions' $id ([ordered]@{
        id = $id; roleName = Require-Text (Get-Field $r 'roleName') 'role name'
        description = Get-Field $r 'description' ''; roleType = Get-Field $r 'roleType' (Get-Field $r 'type' 'BuiltInRole')
        assignableScopes = @((Get-Field $r 'assignableScopes' @()) | Sort-Object); permissions = $permissions
    })
}

function Add-Assignment($Records, $Raw) {
    $r = Get-Field $Raw 'properties' $Raw
    $originalId = Require-Text (Get-Field $Raw 'id') 'assignment ID'
    $id = ($originalId.TrimEnd('/') -split '/')[-1]
    $record = [ordered]@{
        id = $id; principalId = Require-Text (Get-Field $r 'principalId') 'assignment principal'
        roleDefinitionId = Get-RoleId (Get-Field $r 'roleDefinitionId')
        scope = Require-Text (Get-Field $r 'scope') 'assignment scope'
    }
    foreach ($field in @('principalType', 'createdOn', 'createdBy', 'description', 'condition', 'conditionVersion')) {
        $value = Get-Field $r $field
        if ($null -ne $value) { $record[$field] = $value }
    }
    if (Get-Field $r 'condition') {
        Add-CollectionWarning 'conditionNotEvaluated' "Assignment '$id' has a condition that RoleGraph does not evaluate."
    }
    Add-Record $Records 'roleAssignments' $id $record
}

try {
    $outputFile = Get-OutputFile
    if ($DryRun) {
        Write-Output @"
Dry run — no cloud calls, authentication, or file writes.
Output: $outputFile
Tenant: $(if ($TenantId) { $TenantId } else { 'must match both existing sessions' })
Prerequisites: PowerShell 7.4+, Azure CLI, Microsoft.Graph.Authentication.
Read permissions: Azure Reader on the tenant root management group; Graph Directory.Read.All.
Hidden memberships additionally require Member.Read.Hidden and a supported directory role.
Plan:
  Verify az account show and Get-MgContext refer to the same public-cloud tenant.
  Read organization, all users, groups, service principals (Graph GET, every page).
  Read direct groupMemberships and service-principal memberOf edges (Graph GET, every page).
  Read account list --all and management-group entities list (Azure CLI).
  Read role definition list and role assignment list at each management group.
  Read group list, role definition list and role assignment list --all for each subscription.
  Resources: $([bool]$IncludeResources) (resource list per subscription when enabled).
  Normalize all 12 schema sections, report coverage limits, then write only OutputPath.
  Existing output: $(if ($Overwrite) { 'explicit overwrite allowed only after successful collection' } else { 'refused' }).
The live collector never grants permissions, logs in, installs modules or changes subscriptions.
"@
        return
    }
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI is required. See docs/COLLECTION.md.' }
    if (-not (Get-Command Invoke-MgGraphRequest -ErrorAction SilentlyContinue)) {
        Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    }
    $context = Get-MgContext
    if (-not $context) { throw 'Connect to Microsoft Graph first; see docs/COLLECTION.md.' }
    $account = Read-Azure @('account', 'show') -Single
    $actualTenant = Require-Text (Get-Field $account 'tenantId') 'Azure session tenant'
    if ($actualTenant -ine (Get-Field $context 'TenantId') -or ($TenantId -and $TenantId -ine $actualTenant)) {
        throw 'Tenant mismatch between Azure CLI, Microsoft Graph, or -TenantId. Export stopped.'
    }
    if ((Get-Field $account 'environmentName') -ne 'AzureCloud' -or (Get-Field $context 'Environment') -ne 'Global') {
        throw 'This collector supports public Azure and Microsoft Graph Global only.'
    }
    $TenantId = $actualTenant
    $base = 'https://graph.microsoft.com/v1.0'
    $sections = @('tenants','managementGroups','subscriptions','resourceGroups','resources','roleDefinitions',
        'users','groups','servicePrincipals','managedIdentities','groupMemberships','roleAssignments')
    $records = @{}
    foreach ($section in $sections) { $records[$section] = @{} }
    $started = [datetime]::UtcNow.ToString('o')
    $orgs = Read-Graph "$base/organization?`$select=id,displayName,verifiedDomains"
    if ($orgs.Count -ne 1 -or (Get-Field $orgs[0] 'id') -ine $TenantId) { throw 'Organization response does not match the session tenant.' }
    $domains = @((Get-Field $orgs[0] 'verifiedDomains' @()) | Where-Object { Get-Field $_ 'isDefault' $false })
    Add-Record $records 'tenants' $TenantId ([ordered]@{
        id = $TenantId; displayName = Get-Field $orgs[0] 'displayName' $TenantId
        domain = $(if ($domains.Count) { Get-Field $domains[0] 'name' } else { $null })
    })
    Write-Output 'Reading directory identities and direct group memberships...'
    foreach ($kind in @('users','groups','servicePrincipals')) {
        $select = switch ($kind) {
            users { 'id,displayName,userPrincipalName,mail,department' }
            groups { 'id,displayName,description,visibility' }
            servicePrincipals { 'id,displayName,appId,description,servicePrincipalType' }
        }
        foreach ($raw in (Read-Graph "$base/${kind}?`$select=$select")) {
            $id = Require-Text (Get-Field $raw 'id') 'directory object ID'
            $destination = $kind
            if ($kind -eq 'servicePrincipals' -and (Get-Field $raw 'servicePrincipalType') -eq 'ManagedIdentity') { $destination = 'managedIdentities' }
            $record = [ordered]@{ id = $id; displayName = Get-Field $raw 'displayName' $id }
            foreach ($field in @('userPrincipalName','mail','department','description','appId')) {
                $value = Get-Field $raw $field
                if ($null -ne $value) { $record[$field] = $value }
            }
            if ($kind -eq 'groups' -and (Get-Field $raw 'visibility') -eq 'HiddenMembership' -and
                'Member.Read.Hidden' -notin @(Get-Field $context 'Scopes' @())) {
                throw "Hidden membership group '$id' requires Member.Read.Hidden; export stopped."
            }
            Add-Record $records $destination $id $record
        }
    }
    foreach ($groupId in @($records.groups.Keys | Sort-Object)) {
        foreach ($member in (Read-Graph "$base/groups/$([uri]::EscapeDataString($groupId))/members")) {
            $memberId = Require-Text (Get-Field $member 'id') 'group member ID'
            $type = Get-Field $member '@odata.type'
            if ($type -notin @('#microsoft.graph.user','#microsoft.graph.group','#microsoft.graph.servicePrincipal')) {
                Add-CollectionWarning 'unsupportedMember' "Group '$groupId': member '$memberId' of type '$type' is outside the RoleGraph schema."
                continue
            }
            Add-Record $records 'groupMemberships' "$groupId/$memberId" ([ordered]@{ groupId = $groupId; memberId = $memberId })
        }
    }
    # Graph v1.0 /groups/{id}/members can omit SPs. Supplement with direct memberOf.
    foreach ($id in @(@($records.servicePrincipals.Keys) + @($records.managedIdentities.Keys) | Sort-Object)) {
        foreach ($group in (Read-Graph "$base/servicePrincipals/$([uri]::EscapeDataString($id))/memberOf")) {
            if ((Get-Field $group '@odata.type') -ne '#microsoft.graph.group') {
                Add-CollectionWarning 'unsupportedMembership' "Non-group membership for service principal '$id' is outside the RoleGraph schema."
                continue
            }
            $groupId = Require-Text (Get-Field $group 'id') 'group ID'
            Add-Record $records 'groupMemberships' "$groupId/$id" ([ordered]@{ groupId = $groupId; memberId = $id })
        }
    }
    Write-Output 'Reading scope hierarchy and role assignments...'
    $parents = @{}
    foreach ($entity in (Read-Azure @('account','management-group','entities','list'))) {
        $id = Require-Text (Get-Field $entity 'id') 'hierarchy entity ID'
        $properties = Get-Field $entity 'properties' $entity
        $parent = Get-Field (Get-Field $properties 'parent') 'id' '/'
        if ($id -match '^/providers/Microsoft.Management/managementGroups/([^/]+)$') {
            $name = $Matches[1]
            Add-Record $records 'managementGroups' $name ([ordered]@{
                id = $name; displayName = Get-Field $properties 'displayName' $name; parent = $parent
            })
        } elseif ($id -match '^/subscriptions/([^/]+)$') { $parents[$Matches[1]] = $parent }
        else { throw "Unrecognized hierarchy entity '$id'; export stopped." }
    }
    foreach ($sub in (Read-Azure @('account','list','--all'))) {
        if ((Get-Field $sub 'tenantId') -ine $TenantId) {
            Add-CollectionWarning 'otherTenant' 'A subscription in another tenant was excluded.'
            continue
        }
        $id = Require-Text (Get-Field $sub 'id') 'subscription ID'
        if ((Get-Field $sub 'state') -ne 'Enabled') {
            Add-CollectionWarning 'disabledSubscription' "Subscription '$id' is not enabled and was excluded."
            continue
        }
        if (-not $parents.ContainsKey($id)) { throw "Subscription '$id' is missing from the management hierarchy; verify root management-group Reader access." }
        Add-Record $records 'subscriptions' $id ([ordered]@{
            subscriptionId = $id; displayName = Get-Field $sub 'name' $id; managementGroup = $parents[$id]
        })
    }
    if (-not $records.managementGroups.Count) { throw 'No management groups were visible; verify Reader access at the root management group.' }
    foreach ($id in @($parents.Keys | Sort-Object)) {
        if (-not $records.subscriptions.ContainsKey($id)) {
            Add-CollectionWarning 'subscriptionNotCollected' "Hierarchy subscription '$id' was not collected: it is absent from the CLI cache or not enabled."
        }
    }
    foreach ($name in @($records.managementGroups.Keys | Sort-Object)) {
        $scope = "/providers/Microsoft.Management/managementGroups/$name"
        foreach ($role in (Read-Azure @('role','definition','list','--scope',$scope))) { Add-Role $records $role }
        foreach ($assignment in (Read-Azure @('role','assignment','list','--scope',$scope,'--fill-principal-name','false','--fill-role-definition-name','false'))) {
            Add-Assignment $records $assignment
        }
    }
    foreach ($id in @($records.subscriptions.Keys | Sort-Object)) {
        foreach ($group in (Read-Azure @('group','list','--subscription',$id))) {
            $name = Require-Text (Get-Field $group 'name') 'resource group name'
            Add-Record $records 'resourceGroups' "$id/$name" ([ordered]@{ name = $name; subscriptionId = $id; location = Get-Field $group 'location' })
        }
        if ($IncludeResources) {
            foreach ($resource in (Read-Azure @('resource','list','--subscription',$id))) {
                $rid = Require-Text (Get-Field $resource 'id') 'resource ID'
                Add-Record $records 'resources' $rid ([ordered]@{
                    id = $rid; name = Get-Field $resource 'name'; type = Get-Field $resource 'type'; location = Get-Field $resource 'location'
                })
            }
        }
        foreach ($role in (Read-Azure @('role','definition','list','--scope',"/subscriptions/$id",'--subscription',$id))) { Add-Role $records $role }
        foreach ($assignment in (Read-Azure @('role','assignment','list','--all','--subscription',$id,'--fill-principal-name','false','--fill-role-definition-name','false'))) {
            Add-Assignment $records $assignment
        }
    }
    foreach ($assignment in @($records.roleAssignments.Values)) {
        if (-not $records.roleDefinitions.ContainsKey($assignment.roleDefinitionId)) {
            # A custom role may only be assignable at a resource group, not its subscription.
            $roleGuid = ($assignment.roleDefinitionId -split '/')[-1]
            $arguments = @('role','definition','list','--name',$roleGuid,'--scope',$assignment.scope)
            if ($assignment.scope -match '^/subscriptions/([^/]+)') { $arguments += @('--subscription',$Matches[1]) }
            foreach ($role in (Read-Azure $arguments)) { Add-Role $records $role }
            if (-not $records.roleDefinitions.ContainsKey($assignment.roleDefinitionId)) {
                throw "Role definition '$($assignment.roleDefinitionId)' was not readable at its assignment scope; export stopped."
            }
        }
        if ($records.managedIdentities.ContainsKey($assignment.principalId)) { $assignment.principalType = 'ManagedIdentity' }
    }
    if (-not $IncludeResources) { Add-CollectionWarning 'resourcesOmitted' 'Individual resources were not collected; use -IncludeResources for resource inventory.' }
    Add-CollectionWarning 'modelLimitations' 'RoleGraph does not evaluate deny assignments, ABAC conditions, PIM eligibility or effective action exclusions. Tenant / assignments and classic administrators are not collected.'
    Add-CollectionWarning 'visibility' 'Coverage is limited to records visible to these sessions and the Azure CLI subscription cache; an export is not proof of complete tenant access.'
    $document = [ordered]@{ '$schema' = 'rolegraph-import/v1' }
    foreach ($section in $sections) {
        $document[$section] = @($records[$section].GetEnumerator() | Sort-Object Key | ForEach-Object { $_.Value })
    }
    $document['$collection'] = [ordered]@{
        collectorVersion = '1.0'; startedAt = $started; finishedAt = [datetime]::UtcNow.ToString('o')
        tenantId = $TenantId; includeResources = [bool]$IncludeResources; warnings = $warnings.ToArray()
    }
    $json = ConvertTo-Json -InputObject $document -Depth 50
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
    # Recheck immediately before opening; all cloud reads and serialization precede writes.
    $outputFile = Get-OutputFile
    $options = [IO.FileStreamOptions]::new()
    $options.Mode = $(if ($Overwrite -and [IO.File]::Exists($outputFile)) { [IO.FileMode]::Open } else { [IO.FileMode]::CreateNew })
    $options.Access = [IO.FileAccess]::Write
    $options.Share = [IO.FileShare]::None
    if (-not $IsWindows -and $options.Mode -eq [IO.FileMode]::CreateNew) {
        $options.UnixCreateMode = [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite
    }
    $stream = [IO.FileStream]::new($outputFile, $options)
    try { $stream.Write($bytes); $stream.SetLength($bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    Write-Output "Exported to $outputFile. Review $($warnings.Count) collection warnings before importing."
} catch {
    [Console]::Error.WriteLine("Collection failed: $($_.Exception.Message)")
    exit 1
}
