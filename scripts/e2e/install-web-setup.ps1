param(
  [Parameter(Mandatory = $true)][string]$InstallerPath,
  [Parameter(Mandatory = $true)][string]$ArtifactRoot,
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [Parameter(Mandatory = $true)][string]$ProductName,
  [Parameter(Mandatory = $true)][string]$BundleId
)

$ErrorActionPreference = 'Stop'
function Assert-NotReparsePoint([string]$Path) { if (Test-Path -LiteralPath $Path) { if ((Get-Item -LiteralPath $Path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "拒绝 reparse point: $Path" } } }
function Assert-NoReparseComponents([string]$Path) { $current = [IO.Path]::GetFullPath($Path); while ($null -ne $current -and $current -ne [IO.Path]::GetPathRoot($current)) { Assert-NotReparsePoint $current; $current = [IO.Path]::GetDirectoryName($current) }; Assert-NotReparsePoint $current }
function Get-LocalAppData() { $p = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData); if ([string]::IsNullOrWhiteSpace($p) -or -not [IO.Path]::IsPathRooted($p)) { throw '无法确定 LocalAppData' }; return [IO.Path]::GetFullPath($p) }
if ($ProductName -ne 'DeepSeek Harness Desktop E2E') { throw 'Only the E2E product name is allowed' }
if ($BundleId -ne 'ai.deepseek.harness.desktop.e2e') { throw 'Only the E2E bundle id is allowed' }
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$artifact = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$recordFile = [System.IO.Path]::GetFullPath($RecordPath)
if (-not [System.IO.Path]::IsPathRooted($RecordPath)) { throw 'RecordPath must be absolute' }

if ([System.IO.Path]::GetDirectoryName($installer) -ne $artifact) {
  throw 'Installer must be a direct child of ArtifactRoot'
}
if ([System.IO.Path]::GetExtension($installer) -ne '.exe') {
  throw 'Installer must be an executable'
}
if (-not [System.IO.Path]::IsPathRooted($recordFile)) {
  throw 'RecordPath must be absolute'
}

$localAppData = (Get-LocalAppData).TrimEnd('\')
$dataRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $BundleId))
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $ProductName))
Assert-NoReparseComponents $artifact
Assert-NoReparseComponents $installer
Assert-NoReparseComponents $installRoot
Assert-NoReparseComponents $dataRoot
if (Test-Path -LiteralPath $dataRoot) { Assert-NotReparsePoint $dataRoot; if (-not (Test-Path -LiteralPath (Join-Path $dataRoot '.dsh-e2e-owned') -PathType Leaf)) { throw 'Existing E2E data root lacks ownership marker' } }
if ([System.IO.Path]::GetDirectoryName($dataRoot) -ne $localAppData -or [System.IO.Path]::GetFileName($dataRoot) -ne $BundleId) { throw 'Resolved dataRoot escaped LOCALAPPDATA' }
$receiptPath = Join-Path $dataRoot 'state\provisioning.json'
$uninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $ProductName
$process = Start-Process -FilePath $installer -ArgumentList @('/P') -PassThru -Wait -WindowStyle Hidden
$exitCode = $process.ExitCode

$registry = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
try {
  $installRoot = if ($null -eq $registry) { $null } else { [string]$registry.GetValue('InstallLocation') }
  $uninstallString = if ($null -eq $registry) { $null } else { [string]$registry.GetValue('UninstallString') }
  $mainBinary = if ($null -eq $registry) { $null } else { [string]$registry.GetValue('MainBinaryName') }
} finally {
  if ($null -ne $registry) { $registry.Close() }
}

if (-not [string]::IsNullOrWhiteSpace($installRoot)) { $installRoot = $installRoot.Trim('"') }
if (-not [string]::IsNullOrWhiteSpace($installRoot)) { $installRoot = [System.IO.Path]::GetFullPath($installRoot); if ($installRoot -ne [System.IO.Path]::GetFullPath((Join-Path $localAppData $ProductName))) { throw 'Registered installRoot escaped fixed E2E root' }; Assert-NotReparsePoint $installRoot }
if (-not [string]::IsNullOrWhiteSpace($mainBinary)) { if ([IO.Path]::GetFileName($mainBinary) -ne $mainBinary -or $mainBinary -in @('.', '..') -or [IO.Path]::GetExtension($mainBinary) -ne '.exe' -or $mainBinary -match '[\\/]') { throw 'Registered MainBinaryName is unsafe' } }
$appBinary = if ([string]::IsNullOrWhiteSpace($installRoot) -or [string]::IsNullOrWhiteSpace($mainBinary)) { $null } else { [IO.Path]::GetFullPath((Join-Path $installRoot $mainBinary)) }
if ($null -ne $appBinary -and [IO.Path]::GetDirectoryName($appBinary) -ne [IO.Path]::GetFullPath((Join-Path $localAppData $ProductName))) { throw 'Application binary escaped fixed install root' }
if ($null -ne $appBinary) { Assert-NotReparsePoint $appBinary }
$uninstallerPath = if ([string]::IsNullOrWhiteSpace($installRoot)) { $null } else { Join-Path $installRoot 'uninstall.exe' }
if ($null -ne $uninstallerPath) { Assert-NotReparsePoint $uninstallerPath }
$desktopShortcut = Join-Path ([System.Environment]::GetFolderPath('Desktop')) ($ProductName + '.lnk')
$programs = [System.Environment]::GetFolderPath('Programs')
$shortcuts = @(
  (Join-Path $programs ($ProductName + '.lnk')),
  (Join-Path (Join-Path $programs $ProductName) ($ProductName + '.lnk')),
  $desktopShortcut
)
$receipt = if (Test-Path -LiteralPath $receiptPath -PathType Leaf) { Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json } else { $null }
$completedInstallEntry = $null -ne $registry -or (-not [string]::IsNullOrWhiteSpace($installRoot) -and (Test-Path -LiteralPath $appBinary -PathType Leaf))
$activeCandidate = $null -ne $receipt -and -not [string]::IsNullOrWhiteSpace($receipt.activeDir) -and (Test-Path -LiteralPath $receipt.activeDir -PathType Container)

$record = [ordered]@{
  schemaVersion = 2
  installerPath = $installer
  artifactRoot = $artifact
  installerPid = $process.Id
  exitCode = $exitCode
  uninstallKey = $uninstallKey
  uninstallString = $uninstallString
  uninstallerPath = $uninstallerPath
  installRoot = $installRoot
  appBinary = $appBinary
  shortcuts = $shortcuts
  dataRoot = $dataRoot
  provisioningReceipt = $receiptPath
  receipt = $receipt
  desktopPid = $null
  runtimePid = $null
  runtimePort = $null
  completedInstallEntry = [bool]$completedInstallEntry
  activeCandidate = [bool]$activeCandidate
}


$parent = [System.IO.Path]::GetDirectoryName($recordFile)
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
$json = $record | ConvertTo-Json -Depth 16
[System.IO.File]::WriteAllText($recordFile, $json, [System.Text.UTF8Encoding]::new($false))

if ($exitCode -eq 0) {
  if (Test-Path -LiteralPath $dataRoot) {
    Assert-NoReparseComponents $dataRoot
    Assert-NotReparsePoint (Join-Path $dataRoot '.dsh-e2e-owned')
  }
  [System.IO.Directory]::CreateDirectory($dataRoot) | Out-Null
  Assert-NoReparseComponents $dataRoot
  [System.IO.File]::WriteAllText((Join-Path $dataRoot '.dsh-e2e-owned'), 'E2E-owned', [System.Text.UTF8Encoding]::new($false))
}

if ($exitCode -ne 0) { exit $exitCode }
exit 0
