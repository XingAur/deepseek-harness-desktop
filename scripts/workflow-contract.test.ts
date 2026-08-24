import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function normalizeLineEndings(value: string) {
  return value.replace(/\r\n/g, '\n')
}

describe('automated upstream release workflows', () => {
  it('runs a non-cancelling daily sync with recoverable release state', () => {
    const sync = normalizeLineEndings(readFileSync('.github/workflows/upstream-sync.yml', 'utf8'))

    expect(sync).toContain("cron: '30 2 * * *'")
    expect(sync.slice(0, sync.indexOf('jobs:'))).toContain('contents: read')
    expect(sync).toContain('contents: write')
    expect(sync).toContain('actions: write')
    expect(sync).toContain('  publish_refs:')
    expect(sync).toContain('  dispatch:')
    expect(sync).toContain('persist-credentials: false')
    expect(sync).toContain('source_sha: ${{ steps.source.outputs.sha }}')
    expect(sync).toContain('ref: ${{ needs.prepare.outputs.source_sha }}')
    expect(sync).toContain('prepared-release-${{ github.run_id }}-${{ github.run_attempt }}')
    expect(sync).toContain('cancel-in-progress: false')
    expect(sync).toContain('node scripts/release-state.mjs')
    expect(sync).toContain('node scripts/prepare-upstream-release.mjs')
    expect(sync).toContain('node-version: ${{ steps.versions.outputs.node_version }}')
    expect(sync).toContain('npm run release:versions:check')
    expect(sync).not.toContain('Install Tauri Linux system dependencies')
    const prepareJob = sync.slice(sync.indexOf('  prepare:'), sync.indexOf('  verify_supported_platform:'))
    const supportedPlatformVerification = sync.slice(
      sync.indexOf('  verify_supported_platform:'),
      sync.indexOf('  publish_refs:'),
    )
    expect(prepareJob).not.toContain('cargo test --manifest-path src-tauri/Cargo.toml --locked')
    expect(supportedPlatformVerification).toContain('runs-on: macos-15')
    expect(supportedPlatformVerification).toContain('cargo test --manifest-path src-tauri/Cargo.toml --locked')
    const publishHeader = sync.slice(sync.indexOf('  publish_refs:'), sync.indexOf('    steps:', sync.indexOf('  publish_refs:')))
    expect(publishHeader).toContain('needs: [prepare, verify_supported_platform]')
    expect(publishHeader).toContain("needs.verify_supported_platform.result == 'success'")
    expect(sync).toContain('push --atomic origin')
    expect(sync).toContain('gh workflow run desktop.yml')
    expect(sync).toContain('--ref "${RELEASE_TAG}"')
    expect(sync).toContain('"${STATUS}" == \'blocked\'')
    expect(sync).toContain("needs.prepare.outputs.state_status == 'pending-tag'")
    expect(sync).toContain("needs.prepare.outputs.state_status == 'pending-release'")
    expect(sync).toContain('queued,in_progress')
    expect(sync).toContain('sync DSH ${{ needs.prepare.outputs.previous_dsh_version }} to ${{ needs.prepare.outputs.dsh_version }}')
    const prepareHeader = sync.slice(sync.indexOf('  prepare:'), sync.indexOf('    steps:'))
    expect(prepareHeader).not.toContain('GH_TOKEN')
    expect(sync).not.toContain('--force')
  })

  it('builds both platforms before one immutable final publication job', () => {
    const workflow = normalizeLineEndings(readFileSync('.github/workflows/desktop.yml', 'utf8'))

    expect(workflow).toContain("runtime_target: windows-x86_64")
    expect(workflow).toContain("runtime_target: darwin-aarch64")
    expect(workflow).toContain("rust_target: aarch64-apple-darwin")
    expect(workflow).toContain('release/versions.json')
    expect(workflow.match(/node-version: \$\{\{ steps\.versions\.outputs\.node_version \}\}/g)).toHaveLength(2)
    expect(workflow.slice(0, workflow.indexOf('jobs:'))).toContain('contents: read')
    expect(workflow.slice(workflow.indexOf('  publish:'))).toContain('permissions:\n      contents: write')
    expect(workflow).toContain('Create ephemeral Runtime signing keys for pull requests')
    expect(workflow).toContain("generateKeyPairSync('ed25519')")
    expect(workflow).toContain('cancel-in-progress: false')
    expect(workflow).toContain("- 'desktop-v*'")
    expect(workflow.match(/persist-credentials: false/g)).toHaveLength(2)
    expect(workflow).toContain('test "${GITHUB_REF_NAME}" = "desktop-v${DESKTOP_VERSION}"')
    expect(workflow).toContain('actions/upload-artifact@v4')
    expect(workflow).toContain('actions/download-artifact@v4')
    const contractVerification = workflow.slice(
      workflow.indexOf('      - name: Verify source and platform contracts'),
      workflow.indexOf('      - name: Require immutable Runtime signing keys for a release'),
    )
    expect(contractVerification).toContain('shell: bash')
    expect(contractVerification).toContain('set -euo pipefail')
    expect(contractVerification).toContain("if [[ \"${RUNNER_OS}\" == 'Windows' ]]; then")
    expect(contractVerification).toContain('cargo test --manifest-path src-tauri/Cargo.toml --locked --no-run')
    expect(contractVerification).toContain('cargo test --manifest-path src-tauri/Cargo.toml --locked\n          fi')
    const artifactStaging = workflow.slice(
      workflow.indexOf('      - name: Stage release artifacts'),
      workflow.indexOf('      - name: Verify Windows updater signature against the bundled public key'),
    )
    expect(artifactStaging).toContain('windows_installers')
    expect(artifactStaging).toContain('windows_updater_signatures')
    expect(artifactStaging).toContain('*_x64-setup.exe.sig')
    expect(artifactStaging).not.toContain('nsis.zip')
    expect(artifactStaging).not.toContain('desktop_count')
    expect(workflow).toContain('DeepSeek.Harness.Desktop_${DESKTOP_VERSION}_x64-setup.exe')
    expect(workflow).toContain('DeepSeek.Harness.Desktop_${DESKTOP_VERSION}_x64-setup.exe.sig')
    expect(workflow).toContain('DeepSeek.Harness.Desktop_${DESKTOP_VERSION}_aarch64.dmg')
    expect(workflow).toContain('node scripts/desktop-release.mjs')
    expect(workflow).toContain('node scripts/runtime-release-manifest.mjs')
    expect(workflow).toContain('gh release create "${RUNTIME_TAG}" --draft --prerelease')
    expect(workflow).toContain('gh release edit "${RUNTIME_TAG}" --draft=false --prerelease')
    expect(workflow).toContain('Runtime draft contains a manifest without its immutable archive')
    expect(workflow).toContain('verify_updater_signature')
    expect(workflow).toContain('gh release create "${DESKTOP_TAG}" --draft')
    expect(workflow).toContain('Multiple desktop Releases use ${DESKTOP_TAG}')
    expect(workflow).toContain('release_id=${DESKTOP_RELEASE_ID}')
    expect(workflow).toContain('releases/${DESKTOP_RELEASE_ID}')
    expect(workflow).toContain('releases/assets/${asset_id}')
    expect(workflow).toContain('-F draft=false -f make_latest=true')
    expect(workflow).not.toContain('gh release edit "${DESKTOP_TAG}" --draft=false --latest')
    expect(workflow).toContain('desktop-release.json')
    expect(workflow).toContain('latest.json')
    expect(workflow).toContain('cmp -s')
    expect(workflow.match(/for attempt in \{1\.\.12\}; do/g)).toHaveLength(3)
    expect(workflow).toContain('Runtime Release asset list was not consistent after 12 attempts')
    expect(workflow).toContain('Desktop Release ID was not visible after 12 attempts')
    expect(workflow).toContain('Desktop Release asset list was not consistent after 12 attempts')
    expect(workflow).toContain('not Developer ID signed or notarized')
    expect(workflow).not.toContain('tagName:')
    expect(workflow).not.toContain('releaseDraft:')
    expect(workflow).not.toContain('includeUpdaterJson:')
    expect(workflow).not.toContain('updaterJsonPreferNsis:')
    expect(workflow).not.toContain('--clobber')
    expect(workflow.slice(0, workflow.indexOf('steps:'))).not.toContain('DSH_DESKTOP_SIGNING_PRIVATE_KEY')
  })

  it('normalizes Windows workflow line endings before checking contracts', () => {
    expect(normalizeLineEndings('permissions:\r\n      contents: write')).toBe(
      'permissions:\n      contents: write',
    )
  })

  it('pins and validates the macOS desktop entrypoint before staging the DMG', () => {
    const cargoManifest = readFileSync('src-tauri/Cargo.toml', 'utf8')
    const tauriConfig = JSON.parse(readFileSync('src-tauri/tauri.conf.json', 'utf8')) as {
      mainBinaryName?: string
    }
    const workflow = normalizeLineEndings(readFileSync('.github/workflows/desktop.yml', 'utf8'))

    expect(cargoManifest).toContain('default-run = "deepseek-harness-desktop"')
    expect(cargoManifest).toContain('updater-verifier-cli = []')
    expect(cargoManifest).toContain('required-features = ["updater-verifier-cli"]')
    expect(tauriConfig.mainBinaryName).toBe('deepseek-harness-desktop')
    expect(workflow).toContain('- name: Verify macOS application bundle entrypoint')
    expect(workflow).toContain("Print :CFBundleExecutable")
    expect(workflow).toContain("test \"${EXECUTABLE_NAME}\" = 'deepseek-harness-desktop'")
    expect(workflow).toContain('file "${EXECUTABLE_PATH}" | grep -q')
    expect(workflow).toContain("'arm64'")
    expect(workflow).toContain('Contents/MacOS/verify_updater_signature')
    expect(workflow).toContain('--features updater-verifier-cli --bin verify_updater_signature')
    expect(workflow).toContain('MACOS_DISK_IMAGES=(src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg)')
    expect(workflow).toContain('hdiutil attach -readonly -nobrowse')
    expect(workflow).toContain("trap 'hdiutil detach \"${MOUNT_POINT}\"' EXIT")
    expect(workflow).toContain('APP_BUNDLES=("${MOUNT_POINT}"/*.app)')

    const entrypointVerification = workflow.slice(
      workflow.indexOf('      - name: Verify macOS application bundle entrypoint'),
      workflow.indexOf('      - name: Stage release artifacts'),
    )
    expect(entrypointVerification).toContain("if: runner.os == 'macOS'")
    expect(entrypointVerification).toContain('set -euo pipefail')
    expect(entrypointVerification).not.toContain('bundle/macos/*.app')
  })
})
