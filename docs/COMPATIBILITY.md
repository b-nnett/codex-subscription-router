# Compatibility

The patcher is intentionally tied to known ChatGPT desktop bundle structures.
It verifies every modified renderer, main-process, and native binary anchor and
stops instead of applying a partial patch.

## Supported builds

| Official ChatGPT version | Bundle build | `app.asar` SHA-256 |
| --- | --- | --- |
| `26.1103.60933` | `25894` | `a77588b2dcfa9b394d722dd8c2c8b13d3a09bacd49c839cc715245742dba6048` |
| `26.803.61601` | `6396` | `d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf` |

Architecture: Apple silicon (`arm64`).

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, or ASAR hash mismatch by
default; `--allow-untested-source` is an explicit diagnostic override. Never
weaken an anchor-count or binary-constant check merely to make a new build
complete. Review the upstream change and update the patch deliberately.

## How the patcher finds its anchors

[OI] renumbers and re-splits the renderer with most releases, so the patcher
does not hard-code chunk file names:

- Each renderer and main-process anchor is located by a distinctive string, and
  the file that contains it must be unique. A missing or duplicated anchor
  stops the patch instead of editing the wrong chunk.
- The injected account menu is bound to the host module's own JSX runtime,
  React namespace, and menu kit. Their local minified names are detected from
  the module that exports the profile dropdown, and the injected aliases get
  fresh names so nothing is shadowed.
- The desktop profile, updater, and Computer Use hooks are matched with
  regexes rather than an exact minified statement.
- Per-release counts that legitimately change (the Computer Use identifier
  references inside `app.asar`) are recorded in
  `ASAR_CUA_IDENTIFIER_REPLACEMENTS`; an unknown build warns instead of
  failing, while every anchor that must stay unique still fails closed.

## Release-specific notes

### ChatGPT 26.1103.60933

- The Codex CLI moved from `Contents/Resources/codex` to
  `Contents/Resources/codex-cli/bin/codex`; the router installs its proxy there
  and keeps the original as `codex.real`.
- The desktop shell embeds the Computer Use bundle identifier in fewer places
  (`8` replacements in `app.asar` instead of `17`).
- Electron now seals `Info.plist`'s `ElectronAsarIntegrity` dictionary with a
  SHA-256 digest stored in the framework's `__asar_integrity` section. The
  patcher rewrites that slot after changing the archive hash; without it the
  copy dies at launch with "Failed to get integrity for validatable asar
  archive".
- An ad-hoc signature cannot carry Apple-restricted entitlements, so the
  `com.apple.developer.*` keys are dropped and the hardened runtime is not
  requested for ad-hoc builds. Library validation between components would
  otherwise fail, because ad-hoc code has no team to share.
- The desktop shell no longer launches `Resources/codex`. It provisions its own
  copy of `codex-cli/CodexCLI.app` into `~/.codex/plugins/.plugin-appserver`
  and re-copies it whenever the source bytes differ, so the proxy has nowhere
  to live. The patcher therefore leaves the CLI alone on this build and prints
  a warning: the app, the account menu, and the independent profile work, but
  subscription multiplexing is inactive until the provisioning path can be
  redirected.
