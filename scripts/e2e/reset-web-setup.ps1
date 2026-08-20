param(
  [Parameter(Mandatory = $true)][string]$ProductName,
  [Parameter(Mandatory = $true)][string]$BundleId
)

$ErrorActionPreference = 'Stop'
if ($ProductName -match '[\\/:*?"<>|]' -or [string]::IsNullOrWhiteSpace($ProductName)) {
  throw 'ProductName is not a safe path segment'
}
if ($BundleId -match '[\\/:*?"<>|]' -or [string]::IsNullOrWhiteSpace($BundleId)) {
  throw 'BundleId is not a safe path segment'
}

$localAppData = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $ProductName))
$dataRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData $BundleId))
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
  $process = Start-Process -FilePath $uninstaller -ArgumentList @('/P') -PassThru -Wait -WindowStyle Hidden
  if ($process.ExitCode -ne 0) { throw "Existing E2E uninstall failed with exit code $($process.ExitCode)" }
}

if (Test-Path -LiteralPath $dataRoot) {
  Remove-Item -LiteralPath $dataRoot -Recurse -Force
}
