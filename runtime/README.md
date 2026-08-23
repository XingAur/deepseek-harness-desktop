# Managed Runtime format

`npm run runtime:build -- --target=<target> --url=<release-url>` creates a
platform archive plus an unsigned manifest. Supported targets are exactly
`windows-x86_64` and `darwin-aarch64`.

The archive contains the exact Node.js, DeepSeek Harness, and pnpm versions pinned in
`release/versions.json`,
the local Desktop Client plugin and a launcher. The Rust Runtime Manager verifies the signed manifest and SHA-256
before extraction and activation.

The pinned component versions come from `release/versions.json`. Run
`npm run release:versions:check` before building; the daily upstream workflow
updates the same source and refuses partial or decreasing version changes.

Sign release manifests with `scripts/sign-manifest.mjs`. Supply Ed25519 raw JWK coordinates through
`DSH_DESKTOP_SIGNING_PRIVATE_KEY` and `DSH_DESKTOP_SIGNING_PUBLIC_KEY`.
Production private keys must only exist in a release secret store.

## First-launch Runtime release contract

Publish the signed archive and `runtime-<target>.json` to an immutable
`runtime-v<semver>` GitHub prerelease before building the complete Windows installer.
The desktop binary must compile that exact manifest URL into the executable;
release builds must never reference a branch or `latest`.

The complete installer embeds the signed Windows Runtime. The first application
launch verifies, extracts, probes, and commits that Runtime in the normal startup
page before opening the workbench. Later launches use the verified local receipt
first; a healthy matching version starts without waiting for the network. If the
embedded or local Runtime is invalid or incompatible, the startup page can still
repair or upgrade it from the immutable online Runtime release.

Runtime assets are independent from the desktop application and user data. A Runtime
upgrade writes a versioned candidate, verifies and probes it, and only then changes the
active pointer. It never rebuilds or deletes Profile, Workspace, session, project, upload,
cache, or user-extension data as part of a release update.
