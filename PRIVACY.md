# DSH Desktop Privacy Notice

[中文](PRIVACY.zh.md)

- **Version:** 2.1
- **Effective and last updated:** November 2, 2026

DSH Desktop is a local-first, open-source desktop application. It is built on the official DeepSeek Harness desktop implementation (`apps/desktop`), adds a Cordis overlay maintained in this repository, and offers an optional phone relay. This notice explains what these builds and optional services process, why, who receives it, and what choices you have.

"We" means the **maintainer of XingAur/deepseek-harness-desktop**. This is not a DeepSeek-owned repository and has no affiliation, partnership, authorization, or endorsement relationship with DeepSeek. "Official" refers only to the upstream [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) implementation.

For privacy questions or rights requests, use [GitHub Issues](https://github.com/XingAur/deepseek-harness-desktop/issues). Do not put credentials, logs, session content, pairing links, or other private information in a public issue.

## 1. Scope

This notice applies to:

- DSH Desktop builds published through this repository's [GitHub Releases](https://github.com/XingAur/deepseek-harness-desktop/releases);
- the optional [`relay-server/`](relay-server/) phone relay that you deploy or use;
- issues, discussions, or other communications you voluntarily send to the maintainers.

It does not govern other forks, modified builds, third-party distributors, model providers, OAuth providers, MCP servers, proxy operators, or plugins.

## 2. Summary

- Profiles, settings, workspaces, sessions, attachments, logs, and credentials stay on your device by default.
- The overlay contains no telemetry, analytics, or automatic uploads. Upstream DSH session telemetry is disabled in the desktop's default composition.
- Phone relay is off by default. When enabled, it forwards only traffic for the paired session. The relay code keeps no session-content database and does not intentionally record session content. However, it sees plaintext while forwarding, so it must be treated as a party that can access the data. Deploy it only on a server you trust and always use TLS.
- This repository operates no automatic update service and does not collect installation identifiers or usage statistics. Updates are manual downloads from GitHub Releases; GitHub processes ordinary network metadata for downloads under its own policy.
- Model, OAuth, MCP, and proxy services that you configure process what you send them under their own terms.

## 3. Data stored locally by default

DSH Desktop uses `DSH_HOME` as its data directory. The portable build defaults to `~/.dsh-official`; development runs default to `~/.dsh-official-dev`. Release builds and the overlay use isolated directories and do not read, migrate, or overwrite the user's everyday `~/.dsh`. When developing, set `DSH_HOME` explicitly as shown in the documentation.

| Local data | Location | Notes |
| --- | --- | --- |
| Profiles, settings, workspaces, sessions, attachments, and caches | Under `$DSH_HOME` | Read and written by the official desktop and DSH runtime; exact subdirectories are defined by the official implementation. |
| Model and service credentials | Environment variables, a project `.env`, `$DSH_HOME/.env`, or `$DSH_HOME/.credentials.yaml` | Official credential storage. File permissions are restricted, but the data is not encrypted. Programs running as the same operating-system user can read it. |
| Overlay configuration | `$DSH_HOME/overlay-custom.yaml` | Stores the relay origin and MCP server configuration. On platforms with POSIX permissions, the file is created with mode `0600` in a `0700` directory. |
| Logs, crash files, and diagnostic archives | The official desktop's user-data directory or under `$DSH_HOME` | Created locally. This repository does not upload them. Logs may still contain paths, workspace or session IDs, commands, or plugin messages. |
| Pairing state and pending requests | Desktop and relay process memory | Not written to disk; lost on restart. |

This repository does not proactively upload session content. Content leaves the device only when you invoke a model, MCP server, proxy, or phone relay, and then only to the services you chose.

Uninstalling the application may not remove `$DSH_HOME`, Electron user data, logs, or diagnostic files you copied elsewhere. Back up anything you need before deleting local data.

## 4. Phone remote control and the relay

### 4.1 When it is enabled

Phone relay is off by default. The desktop opens a tunnel only after you set `remoteRelayOrigin` in `$DSH_HOME/overlay-custom.yaml` (or override it with `DSH_OVERLAY_RELAY_ORIGIN`) and enable pairing from the "Mobile" panel. Entering an origin alone does not start a tunnel.

### 4.2 Data flow

- The desktop connects outbound to the relay at `wss://<origin>/host`; no public inbound port is required.
- The phone requests `https://<origin>/r/<pair>/...`, and the relay forwards over the authenticated WebSocket tunnel.
- The desktop passes the request to the official Web UI listening on a loopback address.

The relay forwards only HTTP/WebSocket traffic belonging to that pairing. Each enable generates a new pair ID and secret, and the browser session token comes from the current sign-in. A desktop restart rotates all of them, so old links stop working immediately.

### 4.3 What the relay can see

The relay is a reverse proxy: it can see plaintext requests and responses, including pages, session content, messages, and attachments. The relay code keeps active pairings and pending requests in memory only. It creates no content database and does not intentionally record session content, pairing secrets, or browser cookies. The relay's hosting provider, reverse proxy, or network infrastructure may still produce access logs and network metadata.

Therefore:

- deploy the relay only on a server you control and always use TLS;
- treat the relay operator as a party that can access forwarded content;
- pairing links contain an access token, so anyone with the link can reach that session; do not share it publicly;
- phone browsers may cache pages or keep link history; be careful on shared devices.

## 5. Models, OAuth, MCP, and the proxy

### 5.1 Accounts and model services

Account sign-in is provided by the official desktop. Sign-in requests, model requests, and tool requests go to the official service or model provider you configure. A provider may receive API keys, prompts, conversation context, attachments or file contents, tool inputs and outputs, session identifiers, and network metadata. The exact scope depends on your configuration and request.

The overlay's **Test connection**, **Fetch model list**, and **Models shown in picker** actions use credentials from the current `DSH_HOME` to call the provider's endpoint and write the result into the current profile configuration. A failed probe may include text returned by the upstream service.

### 5.2 OAuth

Codex and xAI OAuth sign-in completes in your system browser. Authorization tokens are stored by the official credential store in the current `DSH_HOME`. The overlay does not copy credentials from another data directory and does not send tokens to this repository. The provider processes authorization and account information under its own policy.

### 5.3 MCP

MCP servers you add receive the tool calls, context, and results you initiate. MCP configuration is stored in `$DSH_HOME/overlay-custom.yaml` and mounted by the overlay on the next start. Add only MCP servers you trust. stdio servers run with your local permissions; remote servers process data under their operator's policy. Both can access the network and the context you provide.

### 5.4 Model network proxy

When the model proxy is enabled, model requests pass through the HTTP(S) proxy you configure. The proxy operator can see request destinations and network metadata, and may see request content for unencrypted HTTP. Direct mode and destinations on the bypass list do not use the proxy. The proxy applies to model requests only, not to MCP or other tools.

## 6. Telemetry and diagnostics

The overlay contains no telemetry, usage analytics, advertising identifiers, or automatic crash uploads.

Upstream DSH session telemetry is disabled in the desktop's default composition. Only if you or a deployment operator explicitly set `DSH_TELEMETRY_MODE` to `FULL` or `FEEDBACK_ONLY` may upstream send session telemetry and the upstream anonymous user ID to the endpoint in `DSH_TELEMETRY_OTLP_URL`. That processing is controlled by the upstream implementation and the recipient's policy, not by this repository.

Logs and crash files stay local by default. If you export a diagnostic archive and share it, review it first for paths, session IDs, commands, and other sensitive information.

## 7. Updates and downloads

Builds are distributed through GitHub Releases as prereleases with manual updates: download a new build, then reinstall or replace the old one. This repository has no automatic-update feed, runs no version-check or usage-statistics service, and does not generate or report installation identifiers.

GitHub, as the hosting provider, processes IP addresses, timestamps, requested resources, and network metadata for downloads under its own policy. Each artifact ships with `.sha256` and `.build.json` files so you can verify integrity and build information locally.

## 8. Repository pages, issues, and communication

The project communicates through its GitHub repository, Releases, and Issues. Issues, discussions, code, and attachments you submit are publicly visible, and GitHub and the maintainers can process them. Do not include credentials, logs, session excerpts, pairing links, or other private information in public content.

## 9. Sharing, processors, and international transfers

We share or permit processing only:

- by GitHub, to host the repository, Releases, and Issues;
- by a relay operator, model provider, OAuth provider, MCP server, or proxy service that you choose, under its own policy;
- by recipients reasonably necessary to comply with applicable law or a court order, or to protect users, the project, and public safety;
- by a successor operator that assumes the same purposes and obligations in a project reorganization or service migration.

We do not sell personal data or use it for advertising profiles. These providers may process data in other countries or regions depending on their infrastructure and your choices. Where a cross-border notice, consent, or other safeguard is required, the relevant provider is responsible for meeting it under applicable law.

## 10. Retention

- Local data remains until you delete `$DSH_HOME`, remove the relevant content in the app, or reset the data.
- The relay keeps active pairings and pending requests in memory only; they disappear when the process ends. The relay code creates no content archive. Access logs kept by the hosting provider or network infrastructure follow their own policies.
- Public GitHub content is governed by your controls and GitHub's retention rules.
- This repository has no user database, telemetry store, or update service, so it retains no corresponding server-side personal data.

## 11. Security

The relay origin must be an `https://` origin (loopback `http://127.0.0.1` is allowed for local development). Pairing secrets and browser tokens are not intentionally logged, and a desktop restart rotates them. Overlay configuration is written with restricted permissions on platforms that support POSIX permissions.

No measure provides absolute security. Third-party plugins, MCP servers, proxies, a relay you deploy, copied data directories, and publicly shared diagnostic archives can cross the intended boundaries. TLS protects traffic in transit, but the relay, as an endpoint, can still see plaintext.

## 12. Your choices and rights

You can:

- delete `$DSH_HOME`, or the sessions, attachments, logs, and credentials inside it;
- clear `remoteRelayOrigin` in `overlay-custom.yaml` or close pairing to disable the relay;
- remove model providers, MCP servers, and proxy settings in the app;
- revoke authorization with the OAuth provider;
- contact us through GitHub Issues to ask about or exercise rights of access, correction, deletion, restriction, or withdrawal of consent available under applicable law.

We cannot remotely delete files on your device. Data that has already left the device is handled by the relevant recipient under its policy.

## 13. Children

DSH Desktop is intended for developers and users who can manage a local computing environment, not specifically for children. Minors should use it with a guardian's guidance. If you believe we processed a child's personal data without meeting applicable requirements, contact us so we can investigate.

## 14. Changes to this notice

When data handling, recipients, or user choices change materially, we will update this notice, its version, and its effective date. Where the law requires renewed notice or consent, we will complete that step before the relevant processing begins.

The Chinese and English versions have equal authority. If they diverge, read them together and tell us through GitHub Issues so we can correct them.
