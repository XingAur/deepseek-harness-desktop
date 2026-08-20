[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Executable,

  [ValidateRange(10, 600)]
  [int]$TimeoutSeconds = 90,

  [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'ai.deepseek.harness.desktop\runtime'
$startedAt = Get-Date
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$desktopProcess = Start-Process -FilePath $resolvedExecutable -PassThru -WindowStyle Hidden

function Stop-SmokeProcesses {
  if ($KeepRunning) { return }
  if (-not $desktopProcess.HasExited) {
    Stop-Process -Id $desktopProcess.Id -Force -ErrorAction SilentlyContinue
  }
  Get-CimInstance Win32_Process | Where-Object {
    if (-not $_.ExecutablePath) { return $false }
    if (-not $_.ExecutablePath.StartsWith($runtimeRoot + [IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    return $_.CreationDate -ge $startedAt.AddSeconds(-2)
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

try {
  while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    $desktopProcess.Refresh()
    if ($desktopProcess.HasExited) {
      throw "Desktop process exited before the workbench was ready (exit code $($desktopProcess.ExitCode))."
    }

    $runtimeProcesses = Get-CimInstance Win32_Process | Where-Object {
      if (-not $_.CommandLine) { return $false }
      if ($_.CommandLine.IndexOf($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
      if ($_.CommandLine -notmatch '@deepseek-ai[\\/]dsh[\\/]lib[\\/]bin\.js') { return $false }
      if ($_.CommandLine -notmatch '--profile\s+desktop') { return $false }
      return $_.CreationDate -ge $startedAt.AddSeconds(-2)
    }

    foreach ($runtimeProcess in $runtimeProcesses) {
      $portMatch = [regex]::Match($runtimeProcess.CommandLine, '--port(?:=|\s+)"?(?<port>\d+)')
      if (-not $portMatch.Success) { continue }

      $port = [int]$portMatch.Groups['port'].Value
      try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200 -and $response.Content -match '(?i)(deepseek|dsh)') {
          [pscustomobject]@{
            ready = $true
            elapsedSeconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
            port = $port
            desktopProcessId = $desktopProcess.Id
            runtimeProcessId = $runtimeProcess.ProcessId
          } | ConvertTo-Json -Compress
          Stop-SmokeProcesses
          return
        }
      } catch {
        # The Runtime process may exist briefly before its page is ready.
      }
    }

    Start-Sleep -Milliseconds 500
  }

  throw "Workbench did not become HTTP-ready within $TimeoutSeconds seconds."
} catch {
  Stop-SmokeProcesses
  $elapsed = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
  throw "Runtime readiness smoke test failed after $elapsed seconds: $($_.Exception.Message)"
}
