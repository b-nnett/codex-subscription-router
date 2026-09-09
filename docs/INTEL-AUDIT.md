# Intel (x86_64) source audit

Audited host and official bundle for the `intel-x86_64` port. The official
`/Applications/ChatGPT.app` was only read, never modified (its `app.asar`
SHA-256 was identical before and after every experiment).

## Host

| Component        | Value                                   |
| ---              | ---                                     |
| CPU              | `x86_64` (`uname -m`)                   |
| macOS            | 13.7.8, build 22H730                    |
| Go               | go1.26.3 `darwin/amd64`                 |
| Node.js          | v25.9.0                                 |
| Python           | 3.14.7                                  |
| Signing identity | none (0 valid identities; ad-hoc only)  |

Note: this host runs macOS 13.7.8 while the Intel installer requires macOS
14+. Installer behaviour on this host is therefore a clean refusal; all
build experiments below invoked `scripts/patch_app.py` directly.

## Official ChatGPT bundle (Intel)

| Component                  | Value                                                        |
| ---                        | ---                                                          |
| Version (`CFBundleShortVersionString`) | `26.903.61454`                                    |
| Build (`CFBundleVersion`)  | `8378`                                                       |
| Bundle ID                  | `com.openai.codex`                                           |
| `LSMinimumSystemVersion`   | 13.0                                                         |
| `app.asar` SHA-256         | `a5f9500e023a65f98dafca3a24a3c0fdf23646caf961b8b3df960d6057e7b450` |
| Main executable signature  | ad-hoc, `TeamIdentifier=not set`                             |
| Bundled `codex` signature  | `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)`     |

Tested ARM64 reference for comparison: version `26.803.61601`, build `6396`,
`app.asar` `d5a44ed9…434cf`. The Intel bundle is a different, newer upstream
build, so no ARM64 anchor or occurrence count is inherited.

## Mach-O architecture survey (all `x86_64`, none universal)

| Path in bundle                                              | `lipo -archs` |
| ---                                                         | ---           |
| `Contents/MacOS/ChatGPT`                                    | `x86_64`      |
| `Contents/Resources/codex`                                  | `x86_64`      |
| `Contents/Resources/codex-code-mode-host`                   | `x86_64`      |
| `Contents/Resources/cua_node/bin/node`                      | `x86_64`      |
| `Contents/Resources/cua_node/bin/node_repl`                 | `x86_64`      |
| `Codex Framework.framework/Codex Framework`                 | `x86_64`      |
| Helpers `Codex (Alerts/GPU/Renderer/Service).app`           | `x86_64`      |
| Helpers `app_mode_loader`, `browser_crashpad_handler`, `web_app_shortcut_copier` | `x86_64` |
| `app.asar.unpacked/.../better_sqlite3.node`                 | `x86_64`      |
| `app.asar.unpacked/.../node-pty pty.node`                   | `x86_64`      |
| `app.asar.unpacked/.../objc-js darwin-x64/node.napi.node`   | `x86_64`      |
| `.../wl-device-kit/dist/native/darwin/permissions.node`     | universal `x86_64 arm64` (only exception) |

The `objc-js` prebuilds directory ships only `darwin-x64`; there is no
`node-mac-permissions` `.node` match by that exact name in this build (device
permissions moved under `@worklouder/device-kit-oai`).

## Computer Use service: not bundled on Intel

- `Contents/Resources/cua_node/lib/node_modules/@oai/sky/bin/` is **empty**.
- No `SkyComputerUseService` and no `Codex Computer Use.app` exist anywhere
  under `/Applications/ChatGPT.app` (verified with `find`).
- `@oai/sky@0.6.26` lists the helper paths only as `publishConfig`
  `executableFiles`; the main bundle instead syncs a canonical service at
  startup (`oie({sourceServiceAppPath:uie(),codexHome:…})`, warning
  `Failed to sync canonical Computer Use service app during startup`).
- Raw `HX7739G8FX` occurrences in the main executable: 0. Raw `2DC432GLL2`:
  16 (app-group / notification-container strings, not team-check constants).
- `~/.codex/computer-use/` on this host contains only `config.json`
  (overlay strings), no helper binary.

Consequence: there is no Intel `SkyComputerUseService` to reverse engineer,
so no x86_64 team-identity byte pattern is defined. The patcher dispatches
Computer Use patching by architecture and the x86_64 branch stays fail-closed
(`patch_x86_64_team_identity` raises). Intel builds run at the **Core**
capability level. This also matches the audit rule: never apply the ARM64
`MOVZ/MOVK` Swift small-string pattern to an x86_64 binary.

## ASAR / renderer anchor survey (Intel build 8378 vs ARM64 build 6396 anchors)

Extracted with the locked `@electron/asar` to `/tmp/chatgpt-intel-asar`.

| Anchor area | ARM64 expectation | Intel 8378 finding |
| --- | --- | --- |
| CSP `connect-src &#39;self&#39;` in `webview/index.html` | present | **present** |
| One `webview/assets/app-initial-*.js` | 1 file | 1 file (`app-initial-1b87ae739476.js`, 10.4 MB) |
| Profile menu component `function wXc({sidebarFooter:e,triggerButton:t})` | count 1 | **0** (`sidebarFooter` gone; `triggerButton` x7 in new shapes) |
| Plugin RPC mapping anchors (`"list-apps":q9…`, `"list-installed-apps"…`, `"read-apps"…`, `"login-mcp-server"…`, `"list-mcp-server-status":K9…`) | 1 each | **0** except `listMcpServers(e,t){…` and ``mcpServerStatus/list`` (x5 each, new shapes) |
| App-server bridge `function gm(e,t,n){…h6e.sendRequest…}` | 1 | **0** |
| Profile query `let e=await T_.safeGet(`/wham/profiles/me`)` | 1 | **0** (`/wham/profiles/me` x3 in new `pJs`/`vJs` shapes; reset query renamed `x5i`/`C5i`, mutation `w5i`/`T5i`) |
| Usage modal `function QLs(e){` | 1 | **0** |
| Reset query `function l6r(){…}` | 1 | **0** (new shape `function x5i(){let e=(0,lq.c)(1);…`) |
| Reset mutation `function d6r(){…}` | 1 | **0** (new shape `function w5i(){let e=(0,lq.c)(3),…`) |
| Usage-window / usage-header / `usageItems:Ge` | 1 each | **0** (`usageItems` gone) |
| Profile-menu `onOpenChange` hooks (2) | 1 each | shapes changed (`onOpenChange` x114 total) |
| Depletion `defaultMessage` alerts (3) | 1 each | **0** (new `rate_limit_reached_type` logic, e.g. `workspace_owner_credits_depleted`) |
| One `profile-*.js` | 1 file | **3 files** (`profile-108e93a1eff6.js` + 2x `profile-state-*.js`) |
| One filtered `plugins-settings-*.js` | 1 file | **3 files** |
| One `local-conversation-thread-*.js` | 1 file | **3 files** |
| `plugin_scope_anchor` `action:F,children:w})` | 1 | re-derivation required |
| Thread anchors `function bE(){let e=(0,wE.c)(57)`, `children:[c,l,u,d,f,p,m,h,g,_,v,y,b,x]` | 1 each | re-derivation required |
| Bootstrap profile isolation regex | 1 | **1 (matches Intel bundle)** |
| Bootstrap updater disable (legacy `try{let{runMainAppStartup:`) | 1 | **0 legacy / 1 new-shape** (`let{runMainAppStartup:` after `Promise.resolve().then(()=>require("./main-*.js"))`); patcher now accepts either shape, exactly once |
| Main managed-service pin (`=new X(Y(codexHome),{onServiceAvailable:`) | 1 | **1 (matches)** |
| Computer Use tool instruction (strict replacement) | 1 | **1 (matches)** |
| `com.openai.sky.CUAService` in extracted ASAR | 17 (ARM) | **16 total**, including coincidental byte matches inside native `.node` binaries and `spawn-helper`; not adopted as an Intel expectation |
| Native `better_sqlite3.node` kept unpacked after repack | required | unchanged requirement |

Result: the main-process anchors (bootstrap profile, updater, managed-service
pin, tool instruction) are verified against Intel build 8378; the renderer
JS-bundle anchors need full re-derivation (new minified symbol names such as
`lq`/`nD`/`gb`/`C5i`/`s8i` replace `$F`/`vm`/`Lt`/`u6r`/`F3r`, and every
inserted snippet references those names). The patcher therefore stays
fail-closed on the renderer for this build instead of weakening any
`count != 1` check.

## Differences from the ARM64 build (summary)

1. Newer upstream version/build/ASAR hash (`26.903.61454`/`8378`).
2. No bundled Computer Use helper on Intel (on-demand canonical sync).
3. Official main executable ad-hoc signed on this host (no TeamIdentifier);
   bundled `codex` keeps the `2DC432GLL2` Developer ID signature.
4. Renderer heavily refactored: new chunk file names/counts, new minified
   identifiers, new rate-limit/depletion response shapes.
5. Updater bootstrap shape changed (still disabled deterministically).
6. Device-permission natives moved to `@worklouder/device-kit-oai`.

## Outcome

The renderer drift above was resolved by the `split` anchor set in
`docs/INTEL-RENDERER-8378.md` (all anchors `count == 1` on this exact
build, post-patch `node --check` green). Build 8378 is now an allow-listed
Core source; the Computer Use findings (no Intel helper binary) stand.
