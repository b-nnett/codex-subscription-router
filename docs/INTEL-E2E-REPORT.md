# Intel E2E report (`intel-x86_64` branch)

Date: 2026-09-09. Host: Intel `x86_64`, macOS 13.7.8. Source:
`/Applications/ChatGPT.app` version `26.903.61454` (build `8378`),
`app.asar` `a5f9500e…7e7b450`. No team-backed signing identity on this host
(ad-hoc only). Audit: `docs/INTEL-AUDIT.md`; renderer mapping:
`docs/INTEL-RENDERER-8378.md`.

## Automated verification (all green)

- `npm run check` — PASS: `go test ./...`, `go vet ./...`, `node --check`
  (UI files), Python unit tests (**72 tests**, incl. 21 new 8378
  fixture/transform tests), `bash -n install.sh`.
- `npm run release:check` — PASS. `git diff --check` — PASS.
- Post-patch validation is built into the patcher: every 8378 anchor asserts
  `count == 1` before replacement, injected markers are asserted after, and
  each modified bundle passes `node --check` as ESM.

## Builds

- Diagnostic Core build (`--allow-untested-source --allow-adhoc-signing`,
  destination under `/tmp`): PASS.
- Production Core build (`--allow-adhoc-signing` only, no untested-source
  flag after the compatibility entry was added): **PASS** (`exit=0`).
- `codesign --verify --deep --strict` on the built app: PASS.
- Built artifacts: `codex` (mux) `x86_64`, `codex.real` `x86_64`,
  `CodexSubscriptionRouterLauncher` `x86_64`, identifier `app.cdxmux.multi`,
  no Computer Use helper installed.
- Repacked `app.asar` re-extracted with the locked asar tool: all 22
  marker post-conditions hold (scope helper ×1, menu ×1, depletion ×3,
  avatar stack, plugin scope, thread section, old anchors gone).
- Official `app.asar` SHA-256 identical before and after every build
  (`a5f9500e…7e7b450`). `~/.codex` untouched; `~/.codex-mux` gained only the
  operational `control-token`/`state.json`.

## Runtime E2E — Core (live, this host)

Launched via both the direct binary and `open` (first `open` awaited a
one-time Gatekeeper verification of the ad-hoc signature — environmental,
not a port issue; a team signature removes it).

| Check | Result |
| --- | --- |
| App launches, no `Bad CPU type` / `wrong architecture` | PASS |
| Independent Chromium profile (`…/Application Support/Codex Subscription Router`) | PASS |
| GPU/renderer/network/storage helpers start under the new profile | PASS |
| `codex` mux starts and fans out to `codex.real` app-server child | PASS |
| Primary subscription detected (Plus, masked email, avatar) | PASS |
| Pooled usage loads (8% / 300 min, 28% / 10080 min, 27 threads) | PASS |
| Combined profile endpoint returns identity + stats | PASS |
| Rate-limit reset credits listed for primary | PASS |
| App-server RPCs route with `errorCode=null` (`account/read`, `config/read`, …) | PASS |
| Control API rejects unauthenticated calls (`unauthorized`) | PASS |
| Control service binds loopback-only (`127.0.0.1:48123`) | PASS |
| Renderer/main logs: no `Uncaught`, `TypeError`, `ReferenceError`, `SyntaxError`; no control API 4xx/5xx | PASS |
| Push-notification registration fails with `Bundle identifier mismatch` | Expected (independent bundle ID; push only) |

Not executed live (need an interactive session with a second paid
subscription and UI clicks): second-subscription device login, new-chat
routing distribution, sticky-owner follow-ups, simulated failover,
all-depleted UI, Profile/Plugins/reset selector clicks, thread indicator
visual, Appshots. Routing/sticky/failover **logic** is unchanged portable Go
covered by unit tests; only the live two-account exercise is pending.

## Runtime E2E — Full (Computer Use / Appshots)

Not implemented: Intel build 8378 ships no `SkyComputerUseService`, so there
is no Intel binary whose team-check sequence could be proven. The x86_64
patch branch raises instead of guessing.

## macOS 13 compatibility assessment — verdict **C**

`LSMinimumSystemVersion` is `13.0`; `LC_BUILD_VERSION` minos is 13.0 for the
main executable, the Electron framework, and all helpers (cua `node`:
13.5); our mux declares 12.0 and the launcher 13.0. The 14+ installer rule
for Intel is therefore policy-only for this build and stays unchanged; the
live E2E above ran on macOS 13.7.8 without OS-related failures.

## Regression — ARM64

- ARM64 code path untouched: legacy single-bundle anchors, `arm64_swift_small_string()`,
  49/17 occurrence counts, and the `26.803.61601`/`6396` profiles are intact.
- `install.sh` still accepts `arm64`.
- Live ARM64 E2E: **not executed** (no Apple-silicon Mac available).

## Verdict

```text
INTEL RENDERER 8378: PASS (16/16 patches, exact counts, syntax-checked)
INTEL CORE BUILD: PASS (production flags, ad-hoc signature verified strict)
INTEL CORE E2E: PASS (launch → primary → usage → mux → API → logs) / PARTIAL (second account + interactive selectors NOT TESTED)
HOST macOS 13: COMPATIBLE (verdict C; minos 13.0 throughout)
ARM64 STATIC REGRESSION: PASS (unit/static) / NOT TESTED (live E2E)
```

Split per the task: build compatibility PASS, runtime compatibility PASS
for everything executable headlessly on this host, host-OS compatibility
PASS, with the interactive two-account items explicitly outstanding — not a
renderer or architecture failure.
