# Intel E2E report (`intel-x86_64` branch)

Date: 2026-09-09. Host: Intel `x86_64`, macOS 13.7.8. Source:
`/Applications/ChatGPT.app` version `26.903.61454` (build `8378`),
`app.asar` `a5f9500e…7e7b450`. No team-backed signing identity on this host
(ad-hoc via `CODEX_MUX_SIGNING_IDENTITY="-"`). Audit: `docs/INTEL-AUDIT.md`;
renderer mapping: `docs/INTEL-RENDERER-8378.md`.

This specific Intel Router Core configuration was verified against ChatGPT
26.903.61454 build 8378 on macOS 13.7.8. Computer Use is unsupported.

## Automated verification (all green)

- `npm run check` — PASS: `go test ./...`, `go vet ./...`, `node --check`
  (UI files), Python unit tests (**60 tests**, incl. 8378 fixture/transform,
  installer-policy, and update-safety tests), `bash -n install.sh`.
- `npm run release:check` — PASS. `git diff --check` — PASS.
- Post-patch validation is built into the patcher: every 8378 anchor asserts
  `count == 1` before replacement, injected markers are asserted after, and
  each modified bundle passes `node --check` as ESM.
- Routing/sticky/failover/pooled-state logic is unchanged portable Go,
  covered by unit tests (`TestRouteUrgency*`, `TestAggregateRateLimits*`,
  `TestAllSubscriptionsDepleted*`, `TestScopedPluginRequest*`,
  `rate_limit_preview_test.go`, `rate_limit_resets_test.go`).

## Installer (P1–P2)

- Capability-aware floor via `--print-install-plan`: this host prints
  `Architecture: x86_64 / Mode: Core / macOS: 13.7.8 / Minimum supported:
  13.5 / Source: ChatGPT 26.903.61454 (8378)`. Untested Intel sources and
  Full builds stay at 14.0; Apple-silicon keeps the historical policy.
- Real `./install.sh` run on macOS 13.7.8: **PASS** (`exit=0`), installed to
  `~/Applications/Codex Subscription Router.app`, launched via `open`,
  LaunchServices registration confirmed (186 references in the LS dump).
- `codesign --verify --deep --strict` on the installed app: PASS.
- Fixed empty-array handling on the macOS system bash (3.2) under `set -u`.
- Official `app.asar` SHA-256 identical before and after
  (`a5f9500e…7e7b450`).

## Runtime E2E — Core, single account (live, this host)

| Check | Result |
| --- | --- |
| App launches, no `Bad CPU type` / `wrong architecture` | PASS |
| Independent Chromium profile | PASS |
| `codex` mux starts and fans out to `codex.real` app-server child | PASS |
| Primary subscription detected (Plus, masked email, avatar) | PASS |
| Usage loads (12% / 300 min, 29% / 10080 min, 27 threads) | PASS |
| Combined profile endpoint returns identity + stats | PASS |
| Rate-limit reset credits listed for primary (1 available) | PASS |
| App-server RPCs route with `errorCode=null` | PASS |
| Control API rejects unauthenticated calls (`unauthorized`) | PASS |
| Control service binds loopback-only (`127.0.0.1:48123`) | PASS |
| `~/.codex-mux` `0700`, `control-token`/`state.json` `0600` | PASS |
| No OAuth tokens in control responses; state holds only id/label/home | PASS |
| Test preview routes `404` in production, `401` without token in UI-test mode | PASS |
| Logs: no `Uncaught`, `TypeError`, `ReferenceError`, `panic`, `address already in use`, `database locked` | PASS |
| Push-notification registration fails with `Bundle identifier mismatch` | Expected (independent bundle ID; push only) |

## Preview-driven failover (live, `CODEX_MUX_UI_TESTS=1` session)

No real quota was spent; all states below used the token-authenticated
deterministic preview routes, then `clear` + restart:

| Check | Result |
| --- | --- |
| `single_depleted` on primary → account shows 100%/100% | PASS |
| `all_depleted` → `legacy_rate_limit_reached` aggregated type | PASS |
| Combined endpoint stays deterministic under depletion (no hang) | PASS |
| Reset preview (2 synthetic credits) set → read → verified | PASS |
| `clear` restores real state (12%/29%, no reached type) | PASS |
| Restart clears in-memory previews; real reset count (1) returns | PASS |

## Persistence / restart / reinstall / concurrency (live)

| Check | Result |
| --- | --- |
| Quit → relaunch: primary reconnects, usage + 27 sticky owners persist | PASS |
| Relaunch: no duplicate listeners, control API responsive | PASS |
| 12× concurrent `/v1/accounts` calls: 12/12 consistent | PASS |
| Reinstall over existing (`./install.sh` → `--force`): backup created, 27 owners + homes intact, app relaunches, strict verify PASS | PASS |
| Process cleanup after quit: main/mux/children terminate (orphaned crashpad/monitor helpers match upstream Electron behavior, cleaned manually) | PASS |
| Uninstall: no upstream uninstaller; manual bundle removal preserves `~/.codex`, `~/.codex-mux`, and the official app (see `docs/SMOKE-TEST.md`) | Documented |

## Interactive multi-account session (user-driven, in progress)

Status: user is adding a second subscription via the Router profile menu
(*Add another subscription* → device-code login) and clicking through the
Profile / Plugins / reset selectors plus several new chats with follow-ups.
Pending verification after the user's “готово”: isolated `CODEX_HOME`,
pooled usage vs control API, selector data per account, thread→owner
mapping with quota notes, sticky follow-ups, restart persistence, and a
log sweep for React/console errors. No tokens or secrets are recorded.

## Runtime E2E — Full (Computer Use / Appshots)

Not implemented: Intel build 8378 ships no `SkyComputerUseService`, so there
is no Intel binary whose team-check sequence could be proven. The x86_64
patch branch raises instead of guessing.

## Regression — ARM64

- ARM64 code path untouched: legacy single-bundle anchors,
  `arm64_swift_small_string()`, 49/17 occurrence counts, and the
  `26.803.61601`/`6396` profiles are intact.
- `install.sh` still accepts `arm64`.
- Live ARM64 E2E: **not executed** (no Apple-silicon Mac available).

## Verdict

```text
INTEL INSTALLER macOS 13: PASS (Core floor 13.5, real install.sh run)
INTEL CORE BUILD: PASS (production flags, strict codesign verify)
INTEL MULTI-ACCOUNT: IN PROGRESS (single-account PASS; second account pending user login)
INTEL ROUTING: PARTIAL (unit-tested logic + single-account live; distribution needs two accounts)
INTEL STICKY THREADS: PARTIAL (27 owners persist; live follow-ups pending)
INTEL FAILOVER: PARTIAL (preview-driven depletion PASS live; real two-account failover pending)
INTEL UI SELECTORS: NOT TESTED (pending user clicks; anchors + syntax verified statically)
INTEL PERSISTENCE: PASS (restart + reinstall preserve state)
INTEL SECURITY: PASS (loopback, 401s, perms, no token leaks)

INTEL CORE FINAL: PENDING (interactive multi-account session)

INTEL COMPUTER USE: NOT IMPLEMENTED

ARM64 STATIC REGRESSION: PASS
ARM64 LIVE E2E: NOT TESTED
```
