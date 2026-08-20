param(
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [switch]$TerminateRecorded
)

$ErrorActionPreference = 'Stop'
$recordFile = (Resolve-Path -LiteralPath $RecordPath).Path
$record = Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
$installRoot = [System.IO.Path]::GetFullPath($record.installRoot).TrimEnd('\') + '\'
$runtimeRoot = [System.IO.Path]::GetFullPath((Join-Path $record.dataRoot 'runtime')).TrimEnd('\') + '\'

function Test-RecordedProcess($process, [string]$label) {
  if ($null -eq $process) { return }
  $path = [System.IO.Path]::GetFullPath($process.Path)
  if (-not $path.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase) -and -not $path.StartsWith($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
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
