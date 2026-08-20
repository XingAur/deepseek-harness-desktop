param(
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [Parameter(Mandatory = $true)][string]$SentinelsPath,
  [switch]$DeleteAppData
)

$ErrorActionPreference = 'Stop'
$recordFile = (Resolve-Path -LiteralPath $RecordPath).Path
$sentinelsFile = (Resolve-Path -LiteralPath $SentinelsPath).Path
$record = Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
$sentinels = Get-Content -LiteralPath $sentinelsFile -Raw | ConvertFrom-Json

if (-not [System.IO.Path]::IsPathRooted($record.installRoot)) { throw 'Recorded installRoot must be absolute' }
if (-not [System.IO.Path]::IsPathRooted($record.uninstallerPath)) { throw 'Recorded uninstallerPath must be absolute' }
$uninstaller = [System.IO.Path]::GetFullPath($record.uninstallerPath)
if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'Recorded uninstaller does not exist' }
$quotedUninstaller = '"' + $uninstaller + '"'
if ($record.uninstallString -ne $uninstaller -and $record.uninstallString -ne $quotedUninstaller) {
  throw 'Recorded uninstallString does not match the exact uninstaller path'
}

foreach ($sentinel in $sentinels.entries) {
  if (-not (Test-Path -LiteralPath $sentinel.path -PathType Leaf)) { throw 'Preservation sentinel is missing before uninstall' }
  if ((Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256) { throw 'Preservation sentinel changed before uninstall' }
}

$arguments = @('/P')
if ($DeleteAppData) {
  $localDataRoot = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'ai.deepseek.harness.desktop'))
  $ownershipMarker = Join-Path $localDataRoot '.dsh-e2e-owned'
  if (-not (Test-Path -LiteralPath $ownershipMarker -PathType Leaf)) {
    throw 'Explicit data deletion requires an isolated root with a .dsh-e2e-owned marker'
  }
  $arguments += '/DELETEAPPDATA'
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
  while ((Test-Path -LiteralPath $localDataRoot) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
  }
  if (Test-Path -LiteralPath $localDataRoot) { throw 'Explicit uninstall did not move the application data root' }
} else {
  foreach ($sentinel in $sentinels.entries) {
    if (-not (Test-Path -LiteralPath $sentinel.path -PathType Leaf)) { throw 'Default uninstall removed preserved data' }
    if ((Get-FileHash -LiteralPath $sentinel.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sentinel.sha256) { throw 'Default uninstall changed preserved data' }
  }
}
