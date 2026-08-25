param(
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [switch]$TerminateRecorded
)

$ErrorActionPreference = 'Stop'
function Assert-NotReparsePoint([string]$Path) { if (Test-Path -LiteralPath $Path) { if ((Get-Item -LiteralPath $Path).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "拒绝 reparse point: $Path" } } }
function Assert-NoReparseComponents([string]$Path) { $current = [IO.Path]::GetFullPath($Path); while ($null -ne $current -and $current -ne [IO.Path]::GetPathRoot($current)) { Assert-NotReparsePoint $current; $current = [IO.Path]::GetDirectoryName($current) }; Assert-NotReparsePoint $current }
$local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($local) -or -not [IO.Path]::IsPathRooted($local)) { throw '无法确定 LocalAppData' }
$recordFile = (Resolve-Path -LiteralPath $RecordPath).Path
$record = Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
$local = [System.IO.Path]::GetFullPath($local).TrimEnd('\')
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $local 'DeepSeek Harness Desktop E2E')).TrimEnd('\') + '\'
$dataRoot = [System.IO.Path]::GetFullPath((Join-Path $local 'ai.deepseek.harness.desktop.e2e'))
$runtimeRoot = [System.IO.Path]::GetFullPath((Join-Path $dataRoot 'runtime')).TrimEnd('\') + '\'
if ([System.IO.Path]::GetFullPath($record.installRoot) -ne $installRoot.TrimEnd('\') -or [System.IO.Path]::GetFullPath($record.dataRoot) -ne $dataRoot) { throw 'Recorded root identity is invalid' }
Assert-NoReparseComponents $installRoot
Assert-NoReparseComponents $dataRoot
Assert-NoReparseComponents $runtimeRoot
Assert-NoReparseComponents $record.appBinary

function Test-RecordedProcess($process, [string]$label) {
  if ($null -eq $process) { return }
  $path = [System.IO.Path]::GetFullPath($process.Path)
  $allowed = if ($label -eq 'desktop') { [System.IO.Path]::GetFullPath($record.appBinary) -eq $path -and $path.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase) } else { $path.StartsWith($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase) }
  if (-not $allowed) {
    throw "$label PID identity is outside recorded roots"
  }
  if ($TerminateRecorded) {
    Stop-Process -Id $process.Id
    $process.WaitForExit(5000) | Out-Null
  } else {
    throw "$label PID is still running"
  }
}

if ($null -ne $record.desktopPid) {
  $desktop = Get-Process -Id $record.desktopPid -ErrorAction SilentlyContinue
  Test-RecordedProcess $desktop 'desktop'
}
if ($null -ne $record.runtimePid) {
  $runtime = Get-Process -Id $record.runtimePid -ErrorAction SilentlyContinue
  Test-RecordedProcess $runtime 'runtime'
}

if ($null -ne $record.runtimePort) {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $task = $client.ConnectAsync('127.0.0.1', [int]$record.runtimePort)
    if ($task.Wait(500) -and $client.Connected) { throw 'Recorded runtime port is still open' }
  } finally {
    $client.Dispose()
  }
}
