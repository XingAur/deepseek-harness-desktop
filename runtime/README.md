# Managed Runtime format

`npm run runtime:build -- --target=<target> --url=<release-url>` creates a
platform archive plus an unsigned manifest. Supported targets are exactly
`windows-x86_64` and `darwin-aarch64`.

The archive contains the official Node.js runtime, DeepSeek Harness `0.1.0-rc.8`, pnpm,
the local Desktop Client plugin and a launcher. The Rust Runtime Manager verifies the signed manifest and SHA-256
before extraction and activation.

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
