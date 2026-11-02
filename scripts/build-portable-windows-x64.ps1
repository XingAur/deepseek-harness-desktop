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

function Assert-OwnedPath {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$OwnerRoot)
  $full = [IO.Path]::GetFullPath($Path)
  $root = [IO.Path]::GetFullPath($OwnerRoot)
  if ($full -match '(?i)^[\\/]Applications([\\/]|$)') {
    throw "portable build: refusing path under /Applications: $Path"
  }
  $prefix = if ($root.EndsWith([IO.Path]::DirectorySeparatorChar)) { $root } else { $root + [IO.Path]::DirectorySeparatorChar }
  if ($full -ne $root -and -not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "portable build: path is outside owned root ${OwnerRoot}: $Path"
  }
  return $full
}

function Remove-OwnedItem {
  param([Parameter(Mandatory)][string[]]$Path, [Parameter(Mandatory)][string]$OwnerRoot, [switch]$Recurse)
  foreach ($item in $Path) {
    if (-not (Test-Path -LiteralPath $item)) { continue }
    $full = Assert-OwnedPath -Path $item -OwnerRoot $OwnerRoot
    if ($Recurse) { Remove-Item -LiteralPath $full -Recurse -Force } else { Remove-Item -LiteralPath $full -Force }
  }
}

function Invoke-Checked {
  param([string]$File, [string[]]$Arguments, [string]$WorkingDirectory)
  Push-Location $WorkingDirectory
  try {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "portable build: $File failed with exit code $LASTEXITCODE" }
  } finally { Pop-Location }
}

function Get-AppInventory {
  param([Parameter(Mandatory)][string]$Root)
  $rootFull = Assert-OwnedPath -Path $Root -OwnerRoot $Stage
  $map = [ordered]@{}
  Get-ChildItem -LiteralPath $rootFull -Recurse -File -Force | ForEach-Object {
    $rel = $_.FullName.Substring($rootFull.Length).TrimStart('\', '/').Replace('\', '/')
    $map[$rel] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  return $map
}

function Assert-NsisInventoryDelta {
  param($Before, $After)
  foreach ($key in @($Before.Keys)) {
    if (-not (@($After.Keys) -contains $key)) { throw "portable build: NSIS removed $key" }
    if ($Before[$key] -ne $After[$key]) { throw "portable build: NSIS modified $key" }
  }
  foreach ($key in @($After.Keys)) {
    if (@($Before.Keys) -contains $key) { continue }
    if ($key -ne 'resources/elevate.exe') { throw "portable build: NSIS added unexpected $key" }
  }
}

function Invoke-UnsignedEnvironment {
  $sensitive = [regex]'(?i)(?:KEY|SECRET|TOKEN|PASSWORD)'
  $remove = New-Object System.Collections.Generic.List[string]
  foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $name = [string]$entry.Name
    if ($sensitive.IsMatch($name) -or $name.StartsWith('DSH_DESKTOP_WINDOWS_') -or $name -like 'CSC_*' -or $name -like 'WIN_CSC_*' -or $name -like 'APPLE_*' -or $name -like 'DSH_DESKTOP_MACOS_*') {
      [void]$remove.Add($name)
    }
  }
  foreach ($name in $remove) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
  $env:DSH_DESKTOP_TARGET_PLATFORM = 'win32'
  $env:DSH_DESKTOP_TARGET_ARCH = 'x64'
  $env:DSH_DESKTOP_UNSIGNED = '1'
  $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
  $env:ELECTRON_BUILDER_7Z_FILTER = 'BCJ'
}

$status = (& git -C $Official status --short) -join "`n"
if ($LASTEXITCODE -ne 0 -or $status) { throw 'portable build: official checkout must be clean' }
$OfficialSha = (& git -C $Official rev-parse HEAD).Trim()
$OfficialVersion = (& node -p "require(process.argv[1]).version" (Join-Path $Official 'package.json')).Trim()
if ($OfficialSha -ne $Lock.commit -or $OfficialVersion -ne $Lock.version) {
  throw 'portable build: official checkout does not match official-lock.json'
}
if (-not $BuildVersion) {
  $Iteration = if ($env:DSH_PORTABLE_ITERATION) { $env:DSH_PORTABLE_ITERATION } else { '01' }
  $BuildVersion = "${OfficialVersion}_$Iteration"
}
# The overlay reports this build identity in its version and update panel.
$env:DSH_OVERLAY_OFFICIAL_VERSION = $OfficialVersion
$env:DSH_OVERLAY_BUILD_VERSION = $BuildVersion
if (-not $env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/' }
if (-not (Test-Path Env:DSH_GITHUB_ASSET_MIRROR)) { $env:DSH_GITHUB_ASSET_MIRROR = '' }
$env:DSH_PORTABLE_SOURCE_ROOT = $Root

$NpmRegistry = if ($env:DSH_PORTABLE_NPM_REGISTRY) { $env:DSH_PORTABLE_NPM_REGISTRY } else { 'https://registry.npmjs.org' }
Invoke-Checked npm @('ci', '--registry', $NpmRegistry) $Overlay
Invoke-Checked npm @('run', 'bundle:host') $Overlay
Invoke-Checked npm @('run', 'bundle:client') $Overlay
Invoke-Checked npm @('run', 'check') $Overlay

if (Test-Path -LiteralPath $Stage) { Remove-OwnedItem -Path $Stage -OwnerRoot $StageBase -Recurse }
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
    Invoke-Checked corepack @('pnpm', '--dir', 'apps/desktop', 'run', 'package', '--', 'win-x64', '--unsigned', '--dir') $Stage
    $Packaged = $true
    break
  } catch {
    Write-Warning "portable build: package attempt $Attempt failed: $_"
  }
}
if (-not $Packaged) { throw 'portable build: package failed after three attempts' }

$App = Join-Path $Stage 'apps/desktop/.desktop-build/targets/win-x64/unsigned-artifacts/win-unpacked'
$Resources = Join-Path $App 'resources'
$ExeName = Join-Path $App 'DeepSeek Harness.exe'
if (-not (Test-Path -LiteralPath $ExeName) -or -not (Test-Path -LiteralPath (Join-Path $Resources 'app.asar'))) {
  throw "portable build: expected unpacked application missing: $App"
}
$Asar = Join-Path $Resources 'app.asar'
$AsarCommand = Join-Path $Stage 'node_modules/.pnpm/node_modules/.bin/asar.cmd'
$Tsx = Join-Path $Stage 'node_modules/tsx/dist/cli.mjs'
if (-not (Test-Path -LiteralPath $Tsx)) { throw "portable build: tsx is missing: $Tsx" }
Invoke-Checked node @((Join-Path $Root 'scripts/test-repack-portable-asar.mjs'), (Join-Path $Stage 'node_modules/.pnpm/node_modules/@electron/asar')) $Root
$AsarTree = Join-Path $Stage '.asar-sanitize'
Remove-OwnedItem -Path $AsarTree -OwnerRoot $Stage -Recurse
Invoke-Checked $AsarCommand @('extract', $Asar, $AsarTree) $Stage
Invoke-Checked node @((Join-Path $Root 'scripts/sanitize-portable-paths.mjs'), $AsarTree, $Root, $Stage) $Root
Invoke-Checked node @((Join-Path $Root 'scripts/repack-portable-asar.mjs'), (Join-Path $Stage 'node_modules/.pnpm/node_modules/@electron/asar'), $Asar, $AsarTree, 'win32-x64') $Root
Remove-OwnedItem -Path $AsarTree -OwnerRoot $Stage -Recurse
$AsarUnpacked = "$Asar.unpacked"
if (Test-Path -LiteralPath $AsarUnpacked) { Invoke-Checked node @((Join-Path $Root 'scripts/sanitize-portable-paths.mjs'), $AsarUnpacked, $Root, $Stage) $Root }
$Runtime = Join-Path $Resources 'runtime'
if (Test-Path -LiteralPath $Runtime) { Invoke-Checked node @((Join-Path $Root 'scripts/sanitize-portable-paths.mjs'), $Runtime, $Root, $Stage) $Root }
Invoke-Checked node @((Join-Path $Root 'scripts/verify-portable-paths.mjs'), $Resources, $Root, $Stage) $Root

$FinalDescriptor = Join-Path $Stage '.portable-final/win-x64/desktop-runtime.json'
Invoke-Checked node @($Tsx, (Join-Path $Root 'scripts/finalize-portable-runtime.mjs'), $Stage, $App, $Root, $FinalDescriptor, 'win32-x64') $Root
Invoke-Checked node @($Tsx, (Join-Path $Root 'scripts/smoke-portable-final.mjs'), $Stage, $App, $FinalDescriptor) $Root
Invoke-Checked node @((Join-Path $Root 'scripts/windows-portable-integrity.mjs'), $Stage, $ExeName, $Asar, 'verify') $Root

$Zip = Join-Path $Dist "deepseek-harness-overlay-$BuildVersion-win-x64-unsigned-manual.zip"
$Exe = Join-Path $Dist "deepseek-harness-overlay-$BuildVersion-win-x64-unsigned-manual.exe"
$ExeStage = Join-Path $Stage 'apps/desktop/.desktop-build/portable-nsis'
Remove-OwnedItem -Path @($Zip, "$Zip.sha256", "$Zip.build.json", $Exe, "$Exe.sha256", "$Exe.build.json") -OwnerRoot $Dist
Remove-OwnedItem -Path $ExeStage -OwnerRoot $Stage -Recurse

$InventoryBeforeNsis = Get-AppInventory -Root $App
$AsarHashBeforeNsis = (Get-FileHash -LiteralPath $Asar -Algorithm SHA256).Hash.ToLowerInvariant()
$ExeHashBeforeNsis = (Get-FileHash -LiteralPath $ExeName -Algorithm SHA256).Hash.ToLowerInvariant()

foreach ($line in Get-Content (Join-Path $Stage 'apps/desktop/.env.windows')) {
  if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') { continue }
  $value = $Matches[2]
  if ($value.StartsWith("'") -and $value.EndsWith("'") -and $value.Length -ge 2) {
    $value = $value.Substring(1, $value.Length - 2)
  }
  Set-Item -Path "Env:$($Matches[1])" -Value $value
}
Invoke-UnsignedEnvironment
Invoke-Checked powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Stage 'apps/desktop/scripts/prepare-windows-installer.ps1')) (Join-Path $Stage 'apps/desktop')
New-Item $ExeStage -ItemType Directory -Force | Out-Null
Invoke-Checked corepack @(
  'pnpm', 'exec', 'electron-builder',
  '--config', 'electron-builder.config.mjs',
  '--win', 'nsis', '--x64', '--publish', 'never',
  '--prepackaged', $App,
  '--config.directories.output', $ExeStage
) (Join-Path $Stage 'apps/desktop')
$ExeSource = @(Get-ChildItem $ExeStage -File -Filter '*.exe' | Where-Object { $_.Name -notmatch 'uninstall' })
if ($ExeSource.Count -ne 1) { throw "portable build: expected exactly one NSIS EXE in $ExeStage" }
Move-Item $ExeSource[0].FullName $Exe -Force
if (-not (Test-Path -LiteralPath $Exe) -or (Get-Item -LiteralPath $Exe).Length -eq 0) { throw 'portable build: Windows EXE is empty' }

$InventoryAfterNsis = Get-AppInventory -Root $App
Assert-NsisInventoryDelta -Before $InventoryBeforeNsis -After $InventoryAfterNsis
$AsarHashAfterNsis = (Get-FileHash -LiteralPath $Asar -Algorithm SHA256).Hash.ToLowerInvariant()
$ExeHashAfterNsis = (Get-FileHash -LiteralPath $ExeName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($AsarHashAfterNsis -ne $AsarHashBeforeNsis) { throw 'portable build: NSIS changed app.asar' }
if ($ExeHashAfterNsis -ne $ExeHashBeforeNsis) { throw 'portable build: NSIS changed DeepSeek Harness.exe' }
Invoke-Checked node @((Join-Path $Root 'scripts/windows-portable-integrity.mjs'), $Stage, $ExeName, $Asar, 'verify') $Root
Invoke-Checked node @((Join-Path $Root 'scripts/verify-portable-paths.mjs'), $Resources, $Root, $Stage) $Root
Invoke-Checked node @($Tsx, (Join-Path $Root 'scripts/smoke-portable-final.mjs'), $Stage, $App, $FinalDescriptor) $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($App, $Zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
if (-not (Test-Path -LiteralPath $Zip) -or (Get-Item -LiteralPath $Zip).Length -eq 0) { throw 'portable build: Windows ZIP is empty' }

$OverlayPackage = Join-Path $Stage 'packages/external/overlay-custom'
Invoke-Checked node @((Join-Path $Root 'scripts/write-portable-metadata.mjs'), $Zip, $OverlayPackage, $OfficialSha, $OfficialVersion, $BuildVersion, 'win32', 'x64', 'unsigned') $Root
Invoke-Checked node @((Join-Path $Root 'scripts/write-portable-metadata.mjs'), $Exe, $OverlayPackage, $OfficialSha, $OfficialVersion, $BuildVersion, 'win32', 'x64', 'unsigned') $Root

$FinalStatus = (& git -C $Official status --short) -join "`n"
$FinalSha = (& git -C $Official rev-parse HEAD).Trim()
if ($FinalStatus -or $FinalSha -ne $OfficialSha) { throw 'portable build: official checkout changed during staging' }
Write-Host "portable app: $App"
Write-Host "manual update ZIP: $Zip"
Write-Host "installer EXE: $Exe"
