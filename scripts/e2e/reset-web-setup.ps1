param(
  [Parameter(Mandatory = $true)][string]$ProductName,
  [Parameter(Mandatory = $true)][string]$BundleId
)

$ErrorActionPreference = 'Stop'
. $PSScriptRoot\owned-tree-cleanup.ps1
function Assert-NotReparsePoint([string]$Path) { if (Test-Path -LiteralPath $Path) { if ((Get-Item -LiteralPath $Path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "拒绝 reparse point: $Path" } } }
function Assert-NoReparseComponents([string]$Path) { $current = [IO.Path]::GetFullPath($Path); while ($null -ne $current -and $current -ne [IO.Path]::GetPathRoot($current)) { Assert-NotReparsePoint $current; $current = [IO.Path]::GetDirectoryName($current) }; Assert-NotReparsePoint $current }
function Assert-OwnedDataRootMarker([string]$Marker) {
  if (-not (Test-Path -LiteralPath $Marker -PathType Leaf)) { throw 'Existing data root lacks .dsh-e2e-owned; manual cleanup is required' }
  $before = Get-Item -LiteralPath $Marker -Force
  if ($before -isnot [System.IO.FileInfo] -or $before.PSIsContainer -or ($before.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw 'Existing data root ownership marker is unsafe' }
  $contents = [System.IO.File]::ReadAllText($Marker, [System.Text.UTF8Encoding]::new($false))
  $after = Get-Item -LiteralPath $Marker -Force
  if ($after -isnot [System.IO.FileInfo] -or $after.PSIsContainer -or ($after.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $contents -cne 'E2E-owned') { throw 'Existing data root ownership marker is invalid' }
}
function Get-LocalAppData() { $p = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData); if ([string]::IsNullOrWhiteSpace($p) -or -not [IO.Path]::IsPathRooted($p)) { throw '无法确定 LocalAppData' }; return [IO.Path]::GetFullPath($p) }
if ($ProductName -ne 'DeepSeek Harness Desktop E2E') { throw 'Only the E2E product name is allowed' }
if ($BundleId -ne 'ai.deepseek.harness.desktop.e2e') { throw 'Only the E2E bundle id is allowed' }
if ($ProductName -match '[\\/:*?"<>|]' -or [string]::IsNullOrWhiteSpace($ProductName)) {
  throw 'ProductName is not a safe path segment'
}
if ($BundleId -match '[\\/:*?"<>|]' -or [string]::IsNullOrWhiteSpace($BundleId)) {
  throw 'BundleId is not a safe path segment'
}

$localAppData = (Get-LocalAppData).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $ProductName))
$dataRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $BundleId))
Assert-NoReparseComponents $installRoot
Assert-NoReparseComponents $dataRoot
if ([System.IO.Path]::GetDirectoryName($installRoot) -ne $localAppData -or [System.IO.Path]::GetFileName($installRoot) -ne $ProductName) {
  throw 'Resolved installRoot escaped LOCALAPPDATA'
}
if ([System.IO.Path]::GetDirectoryName($dataRoot) -ne $localAppData -or [System.IO.Path]::GetFileName($dataRoot) -ne $BundleId) {
  throw 'Resolved dataRoot escaped LOCALAPPDATA'
}

$uninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $ProductName
$registry = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
try {
  $uninstallString = if ($null -eq $registry) { $null } else { [string]$registry.GetValue('UninstallString') }
} finally {
  if ($null -ne $registry) { $registry.Close() }
}

if (-not [string]::IsNullOrWhiteSpace($uninstallString)) {
  $uninstaller = [System.IO.Path]::GetFullPath($uninstallString.Trim().Trim('"'))
  $expectedUninstaller = [System.IO.Path]::GetFullPath((Join-Path $installRoot 'uninstall.exe'))
  if ($uninstaller -ne $expectedUninstaller) { throw 'Registered uninstaller is outside the isolated E2E install root' }
  if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'Registered E2E uninstaller is missing' }
  Assert-NoReparseComponents $uninstaller
  $process = Start-Process -FilePath $uninstaller -ArgumentList @('/P') -PassThru -Wait -WindowStyle Hidden
  if ($process.ExitCode -ne 0) { throw "Existing E2E uninstall failed with exit code $($process.ExitCode)" }
}

if (Test-Path -LiteralPath $dataRoot) {
  if ((Get-Item -LiteralPath $dataRoot -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw 'Existing data root is a reparse point' }
  $marker = Join-Path $dataRoot '.dsh-e2e-owned'
  Assert-OwnedDataRootMarker $marker
  Remove-OwnedTreeWithoutFollowingReparsePoints $dataRoot
}
