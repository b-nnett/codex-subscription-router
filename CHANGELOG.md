# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Intel Mac (`x86_64`) port foundations: source-architecture detection,
  architecture-aware compatibility profiles, deterministic `GOARCH`/`-arch`
  builds for `codex-mux` and the native launcher, staged-tree architecture
  validation, and Core (no-helper) versus Full (Computer Use) capability
  levels with stale-helper retirement. The x86_64 Computer Use binary patch
  stays fail-closed until an Intel instruction pattern is proven.
- Intel audit (`docs/INTEL-AUDIT.md`) and Intel E2E report
  (`docs/INTEL-E2E-REPORT.md`).
- Build-8378 Intel renderer support (`docs/INTEL-RENDERER-8378.md`,
  `scripts/analyze_asar.py`): split `app-initial`/`app-primary` anchor set
  with exact occurrence checks and post-patch syntax validation, plus an
  allow-listed Core compatibility profile for ChatGPT 26.903.61454/8378 on
  `x86_64`.
- One-command installer with safe source updates, prerequisite checks, signed
  rebuilds, recoverable upgrades, and automatic launch.
- Reset-aware routing that prioritizes weekly quota at risk of expiring and
  gives a bounded boost to subscriptions with banked usage resets.

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
