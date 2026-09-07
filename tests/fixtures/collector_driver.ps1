param([string]$Collector, [string]$Fixture, [string]$OutputPath, [switch]$DryRun, [switch]$Overwrite, [switch]$IncludeResources)
$ErrorActionPreference = 'Stop'
$global:FixtureData = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json -AsHashtable
function global:Get-MgContext { return $global:FixtureData.context }
function global:Invoke-MgGraphRequest {
    param($Method, $Uri, $OutputType)
    if ($Method -ne 'GET') { throw 'A cloud write was attempted.' }
    if (-not $global:FixtureData.graph.ContainsKey($Uri)) { throw "Unexpected Graph request: $Uri" }
    $result = $global:FixtureData.graph[$Uri]
    if ($result -is [string]) { throw $result }
    return $result
}
function global:az {
    $key = $args -join ' '
    if (-not $global:FixtureData.az.ContainsKey($key)) { throw "Unexpected Azure command: $key" }
    $result = $global:FixtureData.az[$key]
    if ($result -is [string]) { $global:LASTEXITCODE = 1; return $result }
    $global:LASTEXITCODE = 0
    ConvertTo-Json -InputObject $result -Depth 50 -Compress
}
$global:LASTEXITCODE = 0
& $Collector -OutputPath $OutputPath -DryRun:$DryRun -Overwrite:$Overwrite -IncludeResources:$IncludeResources
exit $LASTEXITCODE
