param(
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [Parameter(Mandatory = $true)][string]$SentinelsPath,
  [switch]$DeleteAppData,
  [switch]$DeleteProjects
)

$ErrorActionPreference = 'Stop'
function Assert-NotReparsePoint([string]$Path) { if (Test-Path -LiteralPath $Path) { if ((Get-Item -LiteralPath $Path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "拒绝 reparse point: $Path" } } }
function Assert-NoReparseComponents([string]$Path) { $current = [IO.Path]::GetFullPath($Path); while ($null -ne $current -and $current -ne [IO.Path]::GetPathRoot($current)) { Assert-NotReparsePoint $current; $current = [IO.Path]::GetDirectoryName($current) }; Assert-NotReparsePoint $current }
function Get-LocalAppData() { $p = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData); if ([string]::IsNullOrWhiteSpace($p) -or -not [IO.Path]::IsPathRooted($p)) { throw '无法确定 LocalAppData' }; return [IO.Path]::GetFullPath($p) }
if ($DeleteAppData -and $DeleteProjects) { throw 'DeleteAppData and DeleteProjects cannot be combined' }
$recordFile = (Resolve-Path -LiteralPath $RecordPath).Path
$sentinelsFile = (Resolve-Path -LiteralPath $SentinelsPath).Path
$record = Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
$sentinels = Get-Content -LiteralPath $sentinelsFile -Raw | ConvertFrom-Json
$localAppData = (Get-LocalAppData).TrimEnd('\')
$dataRoot = [System.IO.Path]::GetFullPath([string]$record.dataRoot)
$e2eRootValue = if ([string]::IsNullOrWhiteSpace($env:DSH_E2E_ROOT)) { (Get-Location).Path } else { $env:DSH_E2E_ROOT }
$e2eRoot = [System.IO.Path]::GetFullPath($e2eRootValue)
$artifactRootValue = if ([string]::IsNullOrWhiteSpace($env:DSH_E2E_ARTIFACTS)) { Join-Path $e2eRoot 'e2e-artifacts' } else { $env:DSH_E2E_ARTIFACTS }
$artifactRoot = [System.IO.Path]::GetFullPath($artifactRootValue)
if ([System.IO.Path]::GetDirectoryName($dataRoot) -ne $localAppData) { throw 'Recorded dataRoot must be a direct child of LOCALAPPDATA' }
if ([System.IO.Path]::GetFileName($dataRoot) -ne 'ai.deepseek.harness.desktop.e2e') { throw 'Recorded dataRoot must use the E2E bundle id' }
$expectedInstallRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData 'DeepSeek Harness Desktop E2E'))
if ([System.IO.Path]::GetFullPath([string]$record.installRoot) -ne $expectedInstallRoot) { throw 'Recorded installRoot is not the fixed E2E install root' }
Assert-NoReparseComponents $expectedInstallRoot
Assert-NoReparseComponents $dataRoot

if (-not [System.IO.Path]::IsPathRooted($record.installRoot)) { throw 'Recorded installRoot must be absolute' }
if (-not [System.IO.Path]::IsPathRooted($record.uninstallerPath)) { throw 'Recorded uninstallerPath must be absolute' }
$uninstaller = [System.IO.Path]::GetFullPath($record.uninstallerPath)
$expectedUninstaller = [System.IO.Path]::GetFullPath((Join-Path $expectedInstallRoot 'uninstall.exe'))
if ($uninstaller -ne $expectedUninstaller) { throw 'Recorded uninstaller is not the fixed E2E uninstaller' }
Assert-NoReparseComponents $uninstaller
if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'Recorded uninstaller does not exist' }
$quotedUninstaller = '"' + $uninstaller + '"'
if ($record.uninstallString -ne $uninstaller -and $record.uninstallString -ne $quotedUninstaller) {
  throw 'Recorded uninstallString does not match the exact uninstaller path'
}

$projectRoots = @{}
if ($null -eq $sentinels.entries -or @($sentinels.entries).Count -ne 3) { throw 'Preservation sentinels must contain exactly three entries' }
$scopeCounts = @{}
foreach ($sentinel in $sentinels.entries) {
  if ($sentinel.scope -notin @('app-data', 'project', 'external')) { throw 'Invalid preservation sentinel scope' }
  if ([string]::IsNullOrWhiteSpace($sentinel.sha256) -or $sentinel.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid preservation sentinel hash' }
  $scopeCounts[$sentinel.scope] = 1 + [int]($scopeCounts[$sentinel.scope])
  $sentinelPath = [System.IO.Path]::GetFullPath([string]$sentinel.path)
  Assert-NoReparseComponents $sentinelPath
  if ($sentinel.scope -eq 'app-data' -and -not $sentinelPath.StartsWith($dataRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'App-data sentinel escaped dataRoot' }
  if ($sentinel.scope -eq 'project' -and -not $sentinelPath.StartsWith(([System.IO.Path]::GetFullPath((Join-Path $e2eRoot 'projects-owned'))).TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Project sentinel escaped projects-owned' }
  if ($sentinel.scope -eq 'external' -and -not $sentinelPath.StartsWith(([System.IO.Path]::GetFullPath((Join-Path $artifactRoot 'preserved-external'))).TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'External sentinel escaped artifacts' }
  if ($sentinel.scope -eq 'project') { $projectRoot = [System.IO.Path]::GetDirectoryName($sentinelPath); $projectMarker = Join-Path $projectRoot '.dsh-e2e-project-owned'; if (-not (Test-Path -LiteralPath $projectMarker -PathType Leaf)) { throw 'Project sentinel lacks ownership marker' }; Assert-NoReparseComponents $projectRoot; Assert-NoReparseComponents $projectMarker; $projectRoots[$projectRoot] = $true }
  if (-not (Test-Path -LiteralPath $sentinel.path -PathType Leaf)) { throw 'Preservation sentinel is missing before uninstall' }
  if ((Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256) { throw 'Preservation sentinel changed before uninstall' }
}
foreach ($scope in @('app-data', 'project', 'external')) { if ([int]($scopeCounts[$scope]) -ne 1) { throw 'Preservation sentinel scopes must be unique' } }

$arguments = @('/P')
if ($DeleteAppData -or $DeleteProjects) {
  if ((Test-Path -LiteralPath $dataRoot -PathType Container) -and ((Get-Item -LiteralPath $dataRoot -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw 'E2E data root is a reparse point' }
$ownershipMarker = Join-Path $dataRoot '.dsh-e2e-owned'
  Assert-NoReparseComponents $ownershipMarker
  if (-not (Test-Path -LiteralPath $ownershipMarker -PathType Leaf)) {
    throw 'Explicit data deletion requires an isolated root with a .dsh-e2e-owned marker'
  }
  if ($DeleteProjects) { $arguments += '/DELETEPROJECTS' }
  elseif ($DeleteAppData) { $arguments += '/DELETEAPPDATA' }
}

$process = Start-Process -FilePath $uninstaller -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
if ($process.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($process.ExitCode)" }
if (Test-Path -LiteralPath $record.appBinary -PathType Leaf) { throw 'Application binary still exists after uninstall' }
foreach ($shortcut in $record.shortcuts) {
  if (Test-Path -LiteralPath $shortcut -PathType Leaf) { throw 'Recorded application shortcut still exists after uninstall' }
}
$registry = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($record.uninstallKey)
try {
  if ($null -ne $registry) { throw 'Uninstall registry entry still exists' }
} finally {
  if ($null -ne $registry) { $registry.Close() }
}
if ($DeleteAppData) {
  $deadline = (Get-Date).AddSeconds(60)
  while ((Test-Path -LiteralPath $dataRoot) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
  }
  if (Test-Path -LiteralPath $dataRoot) { throw 'Explicit uninstall did not move the application data root' }
  foreach ($sentinel in $sentinels.entries) {
    $exists = Test-Path -LiteralPath $sentinel.path -PathType Leaf
    if ($sentinel.scope -eq 'app-data' -and $exists) { throw 'DeleteAppData did not remove app-data preservation data' }
    if ($sentinel.scope -ne 'app-data' -and (-not $exists -or (Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256)) { throw 'DeleteAppData changed preserved project or external data' }
  }
} elseif ($DeleteProjects) {
  $deadline = (Get-Date).AddSeconds(60)
  while ((Test-Path -LiteralPath $dataRoot) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
  foreach ($sentinel in $sentinels.entries) {
    $exists = Test-Path -LiteralPath $sentinel.path -PathType Leaf
    if ($sentinel.scope -in @('project', 'app-data')) {
      if ($exists) { throw 'DeleteProjects did not remove owned preservation data' }
    } elseif (-not $exists -or (Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256) {
      throw 'DeleteProjects changed external preservation data'
    }
  }
  if (Test-Path -LiteralPath $dataRoot) { throw 'DeleteProjects did not remove data root' }
  foreach ($projectRoot in $projectRoots.Keys) { if (Test-Path -LiteralPath $projectRoot -PathType Container) { throw 'DeleteProjects did not remove project root' } }
} else {
  foreach ($sentinel in $sentinels.entries) {
    if (-not (Test-Path -LiteralPath $sentinel.path -PathType Leaf)) { throw 'Default uninstall removed preserved data' }
    if ((Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256) { throw 'Default uninstall changed preserved data' }
  }
}
