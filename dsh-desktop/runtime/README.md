# Managed Runtime format

`npm run runtime:build -- --target=<target> --url=<release-url>` creates a
platform archive plus an unsigned manifest. Supported targets are exactly
`windows-x86_64` and `darwin-aarch64`.

The archive contains the official Node.js runtime, DSH `0.1.0-rc.7`, pnpm,
the local Desktop Host/Client plugin, a launcher and the signed curated
catalog. The Rust Runtime Manager verifies the signed manifest and SHA-256
before extraction and activation.

Sign release manifests and catalogs with `scripts/sign-manifest.mjs` and
`scripts/sign-catalog.mjs`. Supply Ed25519 raw JWK coordinates through
`DSH_DESKTOP_SIGNING_PRIVATE_KEY` and `DSH_DESKTOP_SIGNING_PUBLIC_KEY`.
Production private keys must only exist in a release secret store.
