# Security model

## Trust boundaries

- The official ChatGPT app is trusted build input and remains unchanged.
- The patcher has local filesystem and code-signing access by design.
- Each real Codex child is trusted with only its assigned account home.
- The injected renderer is trusted with the loopback control token.
- Other local users and remote origins are outside the control API boundary.
- Processes running as the same macOS user are not considered isolated from
  one another; they can already read that user's app data subject to macOS
  permissions.

## Credentials

OAuth material stays in `auth.json` under each account's Codex home. The
multiplexer reads an account token only to call the same authenticated ChatGPT
profile and rate-limit-reset endpoints used by the desktop experience. It does
not log or return tokens. State persisted by the mux contains account paths,
labels, enabled state, and thread ownership only.

Conversation rollout files and the SQLite thread index are deliberately shared
between account children. This enables cross-account continuation and lets the
official app discover router-created chats. The shared store contains chat
history, but not the per-account `auth.json` credential files.

The state root is mode `0700`; state, config, and control-token files are mode
`0600`. Existing control tokens are validated as 256-bit hexadecimal values and
their permissions are repaired on startup.

Plugin and MCP configuration is deliberately synchronized from the Primary
account so installed definitions remain consistent. Inline environment values
inside those definitions are therefore copied into every isolated account home
with mode `0600`; account isolation is not a separate secret boundary for
shared plugin configuration.

## Network

The control server binds to `127.0.0.1`. Private endpoints require the token
embedded into the independently built local renderer. Profile images must use
HTTPS. Response sizes and JSON request bodies are bounded.

The project itself does not provide a telemetry or update endpoint. Network
traffic beyond loopback is performed by the official Codex children or by the
documented ChatGPT profile and rate-limit APIs.

## Signing and native access

The source app is copied into a temporary staging directory. Native modules,
the Computer Use helper, Node runtime, mux, and final app are signed under one
selected Apple team and verified before replacement. Official OpenAI
application-group and keychain entitlements are removed from modified callers.

The native helper's caller allowlist is patched to the selected team and the
independent desktop bundle ID. This is required for the helper's peer checks;
it does not bypass macOS Accessibility or Screen Recording consent.

## Diagnostics

`CODEX_MUX_UI_TESTS=1` enables deterministic preview and screenshot endpoints.
They are unavailable during a normal launch, bind only to loopback, and require
the same control token. Release workflows never set this variable.

## Distribution

Releases contain source only. Publishing the patched `.app`, the official ASAR,
or any extracted OpenAI binary is outside this project's release process.
