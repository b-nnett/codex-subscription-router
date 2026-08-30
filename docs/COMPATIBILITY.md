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

## ChatGPT build 7303

### Build 7303

| Component | Tested value |
| --- | --- |
| Official ChatGPT version | `26.825.32147` |
| Official bundle build | `7303` |
| `app.asar` SHA-256 | `0462b03e878f0e78b223b849ee14cbba0de043f2c16acebee163cb95daa622ef` |
| Architecture | Apple silicon (`arm64`) |

Build 7303 regenerates the renderer bundles and uses a dedicated, fail-closed
set of native UI anchors. The original build 6396 patch remains supported.

### Build 7345

| Component | Tested value |
| --- | --- |
| Official ChatGPT version | `26.825.41651` |
| Official bundle build | `7345` |
| `app.asar` SHA-256 | `c089b63abb7ca4a751072c0da434248db13c32bed9c363e1b7e5428584b0576d` |
| Architecture | Apple silicon (`arm64`) |

Build 7345 retains the reviewed build-7303 renderer anchors. The full patch,
repack, signing, and signature verification flow was repeated against this
exact ASAR.

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, or ASAR hash mismatch by
default; `--allow-untested-source` is an explicit diagnostic override. Never
weaken an anchor-count or binary-constant check merely to make a new build
complete. Review the upstream change and update the patch deliberately.
