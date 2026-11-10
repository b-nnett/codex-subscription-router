# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- One-command installer with safe source updates, prerequisite checks, signed
  rebuilds, recoverable upgrades, and automatic launch.
- Reset-aware routing that prioritizes weekly quota at risk of expiring and
  gives a bounded boost to subscriptions with banked usage resets.

### Fixed

- Support ChatGPT `26.1103.60933` (build `25894`) by locating renderer and
  main-process anchors by content instead of by chunk file name, so a build that
  ships one `bootstrap.js` plus many `early-bootstrap-*` chunks still patches.
- The injected account menu now binds to the host module's own JSX runtime,
  React namespace, and menu kit under freshly generated names, so minified
  host identifiers can no longer be shadowed.
- The installer forwards its own arguments to the patcher and expands an empty
  option array safely under macOS `/bin/bash` 3.2, which previously aborted
  with `patch_arguments[@]: unbound variable` and silently dropped
  `--allow-untested-source`.
- The Codex CLI proxy follows the CLI's move to
  `Contents/Resources/codex-cli/bin/codex`, and the nested `CodexCLI.app`,
  `native`, and framework code are re-signed so a deep verification passes.
- Rewrite the ASAR integrity digest that newer Electron embeds in the framework,
  and drop restricted entitlements for ad-hoc signatures. Without both, the
  copied app crashed at launch even though it signed cleanly.

## [0.1.0] - 2026-08-15

### Added

- Multi-subscription routing with quota-aware balancing and sticky threads.
- Account isolation, device-code sign-in, pooled usage, and quota failover.
- Native account menu, masked emails, plan labels, and profile photos.
- Combined Profile statistics with per-account selection.
- Account-scoped Apps and MCP connection state in Settings → Plugins.
- Per-account rate-limit reset selection and pooled depletion handling.
- Independently signed Appshots and Computer Use support.
- Fail-closed upstream compatibility checks and deepest-first nested helper signing.
- Loopback-only, token-authenticated diagnostic UI states.
- Source-only CI, draft release automation, security documentation, and smoke tests.

[Unreleased]: https://github.com/b-nnett/codex-subscription-router/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/b-nnett/codex-subscription-router/releases/tag/v0.1.0
