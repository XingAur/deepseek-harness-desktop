param(
  [string]$BuildVersion = $env:DSH_PORTABLE_BUILD_VERSION
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

if (-not $IsWindows -or [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
  throw 'portable build: Windows x64 host required'
}
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Official = Join-Path $Root 'deepseek-harness'
$Overlay = Join-Path $Root 'overlay'
$Lock = Get-Content (Join-Path $Root 'official-lock.json') -Raw | ConvertFrom-Json
$StageBase = if ($env:DSH_PORTABLE_STAGE_ROOT) { $env:DSH_PORTABLE_STAGE_ROOT } elseif ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { Join-Path $Root '.portable-stage' }
$Stage = Join-Path $StageBase 'dsh-win-x64'
$Dist = Join-Path $Root 'dist'

function Invoke-Checked {
  param([string]$File, [string[]]$Arguments, [string]$WorkingDirectory)
  Push-Location $WorkingDirectory
  try {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "portable build: $File failed with exit code $LASTEXITCODE" }
  } finally { Pop-Location }
}

$status = (& git -C $Official status --short) -join "`n"
if ($LASTEXITCODE -ne 0 -or $status) { throw 'portable build: official checkout must be clean' }
$OfficialSha = (& git -C $Official rev-parse HEAD).Trim()
$OfficialVersion = (& node -p "require(process.argv[1]).version" (Join-Path $Official 'package.json')).Trim()
if ($OfficialSha -ne $Lock.commit -or $OfficialVersion -ne $Lock.version) {
  throw 'portable build: official checkout does not match official-lock.json'
}
if (-not $BuildVersion) { $BuildVersion = "$OfficialVersion.$(Get-Date -Format yyyyMMdd).1" }
if (-not $env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/' }
if (-not (Test-Path Env:DSH_GITHUB_ASSET_MIRROR)) { $env:DSH_GITHUB_ASSET_MIRROR = '' }
$env:DSH_PORTABLE_SOURCE_ROOT = $Root

Invoke-Checked npm @('ci') $Overlay
Invoke-Checked npm @('run', 'bundle:host') $Overlay
Invoke-Checked npm @('run', 'bundle:client') $Overlay
Invoke-Checked npm @('run', 'check') $Overlay

Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item $Stage, $Dist -ItemType Directory -Force | Out-Null
$Archive = Join-Path ([System.IO.Path]::GetTempPath()) "dsh-official-$PID.tar"
try {
  Invoke-Checked git @('-C', $Official, 'archive', '-o', $Archive, 'HEAD') $Root
  Invoke-Checked tar.exe @('-xf', $Archive, '-C', $Stage) $Root
} finally { Remove-Item $Archive -Force -ErrorAction SilentlyContinue }
$GitDirectory = (Join-Path $Official '.git').Replace('\', '/')
Set-Content (Join-Path $Stage '.git') "gitdir: $GitDirectory" -Encoding utf8NoBOM
Invoke-Checked node @((Join-Path $Root 'scripts/prepare-portable-stage.mjs'), $Stage, $Overlay) $Root
Invoke-Checked corepack @('pnpm', 'install', '--no-frozen-lockfile') $Stage
Invoke-Checked corepack @('pnpm', 'run', 'bundle:host') (Join-Path $Stage 'packages/external/overlay-custom')
Invoke-Checked corepack @('pnpm', 'run', 'bundle:client') (Join-Path $Stage 'packages/external/overlay-custom')

$Packaged = $false
for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
  $Types = Join-Path $Stage 'packages/external/overlay-custom/lib/types'
  New-Item $Types -ItemType Directory -Force | Out-Null
  Set-Content (Join-Path $Types 'index.js') "export * from '../../src/index.ts'" -Encoding utf8NoBOM
  try {
    Invoke-Checked corepack @('pnpm', '--dir', 'apps/desktop', 'run', 'package', '--', 'win-x64', '--unsigned', '--dir', '--build-version', $BuildVersion) $Stage
    $Packaged = $true
    break
  } catch {
    Write-Warning "portable build: package attempt $Attempt failed: $_"
  }
}
if (-not $Packaged) { throw 'portable build: package failed after three attempts' }

$App = Join-Path $Stage 'apps/desktop/.desktop-build/targets/win-x64/unsigned-artifacts/win-unpacked'
$Resources = Join-Path $App 'resources'
if (-not (Test-Path (Join-Path $App 'DeepSeek Harness.exe')) -or -not (Test-Path (Join-Path $Resources 'app.asar'))) {
  throw "portable build: expected unpacked application missing: $App"
}
Invoke-Checked node @((Join-Path $Root 'scripts/verify-portable-paths.mjs'), $Resources, $Root, $Stage) $Root

$Zip = Join-Path $Dist "deepseek-harness-overlay-$BuildVersion-win-x64-unsigned-manual.zip"
Remove-Item $Zip, "$Zip.sha256", "$Zip.build.json" -Force -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($App, $Zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
if (-not (Test-Path $Zip) -or (Get-Item $Zip).Length -eq 0) { throw 'portable build: Windows ZIP is empty' }
Invoke-Checked node @((Join-Path $Root 'scripts/write-portable-metadata.mjs'), $Zip, (Join-Path $Stage 'packages/external/overlay-custom'), $OfficialSha, $OfficialVersion, $BuildVersion, 'win32', 'x64', 'unsigned') $Root

$FinalStatus = (& git -C $Official status --short) -join "`n"
$FinalSha = (& git -C $Official rev-parse HEAD).Trim()
if ($FinalStatus -or $FinalSha -ne $OfficialSha) { throw 'portable build: official checkout changed during staging' }
Write-Host "portable app: $App"
Write-Host "manual update ZIP: $Zip"
