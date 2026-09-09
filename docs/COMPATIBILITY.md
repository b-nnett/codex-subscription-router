# Compatibility

The patcher is intentionally tied to known ChatGPT desktop bundle structures.
It verifies every modified renderer, main-process, and native binary anchor and
stops instead of applying a partial patch.

## Release 0.1.0

| Component | Tested value |
| --- | --- |
| Official ChatGPT version | `26.803.61601` |
| Official bundle build | `6396` |
| `app.asar` SHA-256 | `d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf` |
| Architecture | Apple silicon (`arm64`) |

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, architecture, or ASAR
hash mismatch by default; `--allow-untested-source` is an explicit diagnostic
override. Never weaken an anchor-count or binary-constant check merely to make
a new build complete. Review the upstream change and update the patch deliberately.

## Intel x86_64 (port in progress, `intel-x86_64` branch)

| Component | Value |
| --- | --- |
| Architecture | Intel (`x86_64`) |
| Official ChatGPT version | `26.903.61454` |
| Official bundle build | `8378` |
| `app.asar` SHA-256 | `a5f9500e023a65f98dafca3a24a3c0fdf23646caf961b8b3df960d6057e7b450` |
| Computer Use | Core only (no helper shipped in this Intel build; x86_64 binary patch not implemented) |
| Tested macOS | none yet (audit host: macOS 13.7.8; installer requires macOS 14+ on Intel) |

This Intel build is intentionally **not** allow-listed: its renderer anchors
differ from the ARM64 build (see `docs/INTEL-AUDIT.md`) and the patcher stops
fail-closed on the first unverified anchor. A diagnostic Core build gets
through architecture detection, `x86_64` multiplexer/launcher builds, desktop
profile isolation, and the updater disable before that stop. Details and the
remaining renderer re-derivation work are recorded in
`docs/INTEL-E2E-REPORT.md`.
