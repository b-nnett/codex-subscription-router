# Intel E2E report (`intel-x86_64` branch)

Date: 2026-09-09. Host: Intel `x86_64`, macOS 13.7.8. Source:
`/Applications/ChatGPT.app` version `26.903.61454` (build `8378`),
`app.asar` `a5f9500e…7e7b450`. No team-backed signing identity on this host
(ad-hoc only). Full audit: `docs/INTEL-AUDIT.md`.

## Automated verification (all green)

- `npm run check` — PASS: `go test ./...`, `go vet ./...`, `node --check`
  (3 UI files), Python unit tests (30 tests), `bash -n install.sh`.
- `git diff --check` — PASS.
- New Python suite (`scripts/test_patch_app.py`, 30 tests) covers: `x86_64` ↔
  `amd64` mapping, unsupported architectures, architecture-aware compatibility
  lookup, Mach-O slice validation (accept host slice / reject missing slice),
  source-architecture detection (host slice / mismatch refusal / non-Mach-O
  refusal), ARM64 team-patch exact-occurrence rewriting and fail-closed counts,
  x86_64 patch refusal, dispatcher routing, Core-vs-Full desktop-profile output
  (no helper env vars in Core, helper pin in Full, updater exactly-once for
  both bootstrap shapes), plus real `go build` (`GOOS=darwin GOARCH=amd64`)
  and `xcrun clang -arch x86_64` builds verified with `lipo -archs`.
- `npm run release:check` — PASS (no release artifacts or credentials tracked).

## Diagnostic build (isolated destination, ad-hoc, untested source)

Command (destination kept under `/tmp`, official app never touched):

```sh
python3 scripts/patch_app.py \
  --destination "/tmp/csr-intel-diag/Codex Subscription Router.app" \
  --allow-untested-source --allow-adhoc-signing
```

Result:

- `Source ChatGPT version: 26.903.61454 (8378), architecture: x86_64` — PASS
  (detection works and is printed).
- `Capability level: core …` — PASS (untested source defaults to Core).
- `Building multiplexer for x86_64…` — PASS (`file`/`lipo -archs` → `x86_64`).
- `Copying ChatGPT.app…` + launcher install — PASS (launcher `x86_64`).
- `Patching desktop profile and renderer…` — profile isolation and the
  new-shape updater disable applied to the real Intel bootstrap/main bundles
  (verified separately on the extracted tree); then fail-closed stop:
  `patch failed: could not find the native ChatGPT profile menu component`.
- Full-mode attempt (`--enable-computer-use`) stops even earlier and
  fail-closed: `expected 17 Computer Use references in app.asar, found 16`.
- Official `app.asar` SHA-256 identical before and after (`a5f9500e…7e7b450`).
  Staged temp directory cleaned up; nothing installed to `~/Applications`.

## Runtime E2E — Core

Not executed: no installable Core build exists yet because the renderer
JS-bundle anchors for upstream build 8378 are not re-derived (see audit
table). The patcher refuses to produce a partially patched app, which is the
required behaviour. Remaining work, in order:

1. Re-derive the ~20 renderer anchors for build 8378 (new chunk layout:
   3x `profile-*`, 3x `plugins-settings-*`, 3x `local-conversation-thread-*`;
   new minified symbols) and map every inserted snippet symbol.
2. Add the tested Intel `SourceCompatibility` profile (with
   `computer_use_supported=False`) only after a full anchor review.
3. Rebuild on a macOS 14+ Intel Mac with a team-backed identity, then run the
   Core checklist (launch, two subscriptions, routing, sticky threads,
   failover, pooled usage, Profile/Plugins/reset selectors).

## Runtime E2E — Full (Computer Use / Appshots)

Not implemented: Intel build 8378 ships no `SkyComputerUseService` (empty
`@oai/sky/bin/`, on-demand canonical sync instead), so there is no Intel
binary whose team-check instruction sequence could be proven. The x86_64
patch branch raises instead of guessing. No live helper test is possible or
claimed.

## Regression — ARM64

- ARM64 code path preserved: `arm64_swift_small_string()`, occurrence counts
  (49/17), and the `26.803.61601`/`6396` compatibility profile are unchanged
  and covered by unit tests (synthetic-binary exact-occurrence test).
- `install.sh` still accepts `arm64`.
- Live ARM64 E2E: **not executed** (no Apple-silicon Mac available).

## Verdict

```text
INTEL CORE: FAIL (blocked on renderer anchor re-derivation for build 8378; infrastructure PASS, fail-closed PASS)
INTEL COMPUTER USE: NOT IMPLEMENTED (no Intel helper binary shipped; x86_64 patch fail-closed by design)
ARM64 REGRESSION: PASS (unit/static) / NOT TESTED (live E2E)
```

No check was weakened to reach a PASS: every anchor/occurrence assertion is
intact, the Intel build is not allow-listed, and the official ChatGPT app is
byte-identical before and after.
