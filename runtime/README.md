# Managed Runtime format

`npm run runtime:build -- --target=<target> --url=<release-url>` creates a
platform archive plus an unsigned manifest. Supported targets are exactly
`windows-x86_64` and `darwin-aarch64`.

The archive contains the official Node.js runtime, DeepSeek Harness `0.1.0-rc.7`, pnpm,
the local Desktop Client plugin and a launcher. The Rust Runtime Manager verifies the signed manifest and SHA-256
before extraction and activation.

Sign release manifests with `scripts/sign-manifest.mjs`. Supply Ed25519 raw JWK coordinates through
`DSH_DESKTOP_SIGNING_PRIVATE_KEY` and `DSH_DESKTOP_SIGNING_PUBLIC_KEY`.
Production private keys must only exist in a release secret store.

## First-launch Runtime release contract

Publish the signed archive and `runtime-<target>.json` to an immutable
`runtime-v<semver>` GitHub prerelease before building the Windows online installer.
The desktop binary must compile that exact manifest URL into the executable;
release builds must never reference a branch or `latest`.

The installer deploys only the desktop shell. The first application launch shows
Runtime progress in the normal startup page, then downloads, verifies, probes,
and commits the Runtime before opening the workbench. Later launches use the
verified local receipt first; a healthy matching version starts without waiting
for the network, while an incompatible version is upgraded in the startup page.
