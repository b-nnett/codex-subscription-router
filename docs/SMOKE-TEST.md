# Signed-app smoke test

Complete this checklist on the exact official build recorded in
`docs/COMPATIBILITY.md` before publishing a release draft. Use a team-backed
signature and reuse the same Apple team as the previous installed build.

On Intel Macs (macOS 14+, Core capability level while Computer Use is
unimplemented), skip the Appshots and Computer Use section and additionally
confirm: the patcher prints `architecture: x86_64`, `codex-mux` and the
launcher report `x86_64` via `lipo -archs`, no
`Codex Subscription Router Computer Use.app` is installed, and any helper
from a previous Full install was retired to `~/.codex-mux/backups`.

## Build and identity

- Confirm the patcher reports the expected version, build, and ASAR SHA-256.
- Verify the official `/Applications/ChatGPT.app` is unchanged.
- Verify the app and every nested Computer Use application with
  `codesign --verify --deep --strict`.
- Confirm the installed app and helper report the intended bundle IDs and the
  same `TeamIdentifier`.

## Accounts and routing

- Connect at least two subscriptions and confirm photos, plans, masked emails,
  pooled usage, and loading states.
- Start chats until each account has received one; confirm every follow-up stays
  on its original account.
- Spoof one depleted account and confirm the thread continues on an account with
  quota. Spoof all accounts depleted and confirm the combined alert.
- Open a quota-triggered reset sheet, switch subscriptions, consume a reset, and
  confirm only the selected account changes.

## Settings and plugins

- Confirm Profile opens in the combined state, uses 20 px avatar overlap, and
  toggles between combined and per-account statistics.
- In Settings → Plugins, select each subscription and verify Apps, MCP status,
  and MCP OAuth login reflect that account while installed definitions remain
  shared.

## Appshots and Computer Use

- In System Settings, grant Accessibility to Codex Subscription Router and
  Screen & System Audio Recording to Codex Subscription Router Computer Use.
  Quit and reopen when macOS asks.
- Capture an Appshot from the attachment menu and with the Command-key shortcut.
- Run a Computer Use task and confirm the native helper performs the action
  without falling back to `osascript`.
- Rebuild once with the same signing team and confirm existing permissions still
  work without adding duplicate permission rows.

Record the tested commit, macOS version, signing team ID, and any deviations in
the release draft before publishing it.

## Uninstall and reinstall (Core)

There is no dedicated uninstaller. Removal is manual and never touches
account state:

```sh
# Quit the app and helper first, then remove only the bundles:
rm -rf "$HOME/Applications/Codex Subscription Router.app" \
  "$HOME/Applications/Codex Subscription Router Computer Use.app"
# Optional: drop the independent Chromium profile (signed-in desktop state):
rm -rf "$HOME/Library/Application Support/Codex Subscription Router"
```

The following are intentionally preserved: `~/.codex` (primary credentials),
`~/.codex-mux` (token, account homes, sticky thread ownership, backups),
and the official `/Applications/ChatGPT.app`. A later reinstall reuses the
same state; timestamped backups under `~/.codex-mux/backups` can be deleted
manually once the rebuilt app passes this checklist.
