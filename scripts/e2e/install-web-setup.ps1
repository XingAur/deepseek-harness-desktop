param(
  [Parameter(Mandatory = $true)][string]$InstallerPath,
  [Parameter(Mandatory = $true)][string]$ArtifactRoot,
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [Parameter(Mandatory = $true)][string]$ProductName,
  [Parameter(Mandatory = $true)][string]$BundleId
)

$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$artifact = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$recordFile = [System.IO.Path]::GetFullPath($RecordPath)

if ([System.IO.Path]::GetDirectoryName($installer) -ne $artifact) {
  throw 'Installer must be a direct child of ArtifactRoot'
}
if ([System.IO.Path]::GetExtension($installer) -ne '.exe') {
  throw 'Installer must be an executable'
}
if (-not [System.IO.Path]::IsPathRooted($recordFile)) {
  throw 'RecordPath must be absolute'
}

$dataRoot = Join-Path $env:LOCALAPPDATA $BundleId
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
$appBinary = if ([string]::IsNullOrWhiteSpace($installRoot) -or [string]::IsNullOrWhiteSpace($mainBinary)) { $null } else { Join-Path $installRoot $mainBinary }
$uninstallerPath = if ([string]::IsNullOrWhiteSpace($installRoot)) { $null } else { Join-Path $installRoot 'uninstall.exe' }
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
  schemaVersion = 1
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

if ($exitCode -ne 0) { exit $exitCode }
exit 0
