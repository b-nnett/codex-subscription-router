# Renderer anchors for build 8378 (Intel Core)

Source: `/Applications/ChatGPT.app` version `26.903.61454`, build `8378`,
`app.asar` `a5f9500e…7e7b450`, analysed from a read-only extraction with
`scripts/analyze_asar.py` (chunks listed with sizes and SHA-256 into a
temporary `MANIFEST.tsv`; the manifest is not committed because it describes
proprietary file hashes).

Build 8378 splits the renderer into `app-initial-*.js` (RPC, queries,
profile data) and `app-primary-*.js` (sidebar menu, usage modal, banners).
The old single-bundle assumption is replaced by the `split` renderer
profile; every anchor below was counted (`count == 1`) on the exact tested
build. No anchor is inherited from build 6396 without re-verification.

## Upstream symbol map (account menu component, app-primary scope)

All bindings are module-scope in `app-primary-*.js` (ESM imports or
top-level lazy-init `var`s shared with the injection point).

| Old (6396) | Role | New (8378) | Evidence |
| --- | --- | --- | --- |
| `e7` | JSX runtime | `dq` | ESM import, `(0,dq.jsx)` everywhere |
| `kXc` | React | `Pyn` | `var uq,Pyn,…`, `Pyn=n(Y(),1)` React interop, `(0,Pyn.useState)` |
| `$` | intl message | `$` | ESM import (`ypn as $`), `($,{id:`codex.profileDropdown…`})` |
| `CH` | menu primitives | `of` | ESM import, `(0,lq.jsx)(of.Separator,{})` |
| `QLs` | usage/reset modal | `tG` | `function tG(e){` takes the identical props (`defaultResetCreditsOpen`, `initialAvailableCount`, `isRateLimitReached`, `onClose`, `onResetComplete`) and renders `Adn` |
| `_H` | menu row | `Qy` | ESM import; observed props cover `LeftIcon`, `onClick`, `onSelect`, `SubText`, `children`, `rightIcon`, `keyboardShortcut`, `className`, `disabled` |
| `S2` | usage-row icon | `MG` | module-scope 20px SVG icon used by the native usage row |
| `BW` | modal opener | `Zv` | ESM import; `Zv(scope,tG,{…})` opens the usage modal natively |
| `Lo(Q)` | modal scope | `fo(HE)` | both module-scope; `Zv(i,tG,…)` with `i=fo(HE)` in `kyn` |
| `jLa` | avatar URL resolver | `uB` | `function uB(e){return eGt(e).src}`, used as `uB(profile_picture_url)` |
| `lt` | query client | `vr` | ESM import; `l=vr()` then `l.getQueryData(…)` in `tG` |

Deliberate UI deltas in the 8378 component variant: `tone:"danger"`,
`allowWrap`, and `subTextAllowWrap` are dropped from the error row because
`Qy` was not observed accepting them (unknown props); the error text itself
still renders via `SubText`.

## Patch mapping (old → new)

| # | Old patch (6396) | Purpose | New chunk (8378) | New anchor (`count == 1`) | New edit |
| --- | --- | --- | --- | --- | --- |
| 1 | CSP `connect-src` | allow loopback control API | `webview/index.html` | unchanged literal | unchanged |
| 2 | prepend menu component before `wXc` | install account UI | app-primary | `function kyn(e){let t=(0,uq.c)(223),{sidebarFooter:n,triggerButton:r}=e` | prepend transformed component (symbol map above) |
| 3 | plugin RPC mapping anchors (7) | fail-closed RPC wiring check | app-initial | `"app/list"`, `"app/installed"`, `"app/read"`, `"mcpServer/oauth/login":`mcp``, `"mcpServerStatus/list":`mcp``, `listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})` (each ×1) | verify presence only; the choke point moved (see 4) |
| 4 | `gm` bridge → `codexMuxScopePluginRequest` | per-account plugin routing marker | app-initial | `async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`&&n?.timeoutMs==null?{...n,timeoutMs:cwn}:n)}` | `this.enqueueRequest(e,t,` → `this.enqueueRequest(e,codexMuxScopePluginRequest(e,t),`; helper injected via self-contained prelude (top of `ui/account-menu.js` before `CodexMuxUsageModal`, no upstream refs). The scope set is the union of the 6396 UI-action names and the 8378 RPC names (`app/list`, `app/installed`, `app/read`, `mcpServerStatus/list`, `mcpServer/oauth/login`); the multiplexer strips the marker before forwarding, so extra spellings are harmless no-ops on either build |
| 5 | profile query `T_.safeGet` → `codexMuxProfileData` | combined/per-account profile | app-initial | `let e=await vO.safeGet(`/wham/profiles/me`)` (inside `pJs`, query key `["profile","usage",…]`) | replace callee with `await codexMuxProfileData(globalThis.__codexMuxSelectedProfileAccountId??null)` |
| 6 | usage modal `QLs(e){` hook | per-account reset state in modal | app-primary | `function Adn(e){` | prepend `CodexMuxUseResetAccountState();` |
| 7 | reset query `l6r` | per-account reset balance | app-initial | full `function x5i(){…}` text (queryKey `rate-limit-reset-credits`, `queryFn:C5i`, `nD.ONE_MINUTE`) | `queryKey:[`rate-limit-reset-credits`]` → keyed variant; `queryFn:C5i` → account-conditional; memo shape and `dz(),sb(null)` preserved |
| 8 | reset mutation `d6r` | consume selected account reset | app-initial | full `function w5i(){…}` text (`mutationFn:T5i`, `s8i` reducer, `yb(r)`) | `mutationFn:T5i` → account-conditional; the two `w5i` cache keys (`setQueryData`, `Promise.all` invalidation) → keyed variant. The agentic tool-path `$E(r,[`rate-limit-reset-credits`])` intentionally stays on the base key (known limitation, same as 6396) |
| 9 | selected usage `let y=v;if(g!=null){` | show selected account windows | app-primary | `F=D!=null&&T!==`idle`&&!(T===`awaiting-refresh`&&N)?D:j` (in `ydn`) | `F=window.__codexMuxSelectedUsageWindows??(…)` |
| 10 | usage header children | reset account selector | app-primary | `(ge=(0,eG.jsxs)(Eb,{children:[me,he]}` | append `window.__codexMuxResetAccountSelector??null` |
| 11 | `usageItems:Ge` → mux menu | pooled usage + account rows | app-primary | `usageItems:wt` (`wt` = native `oyn` summary + `Qy` remaining row) | `usageItems:(0,dq.jsx)(CodexMuxAccountMenu,{})` |
| 12 | `onOpenChange:o` ×2 → login guard | keep menu open during device login | app-primary | `triggerButton:Ot,onOpenChange:c,children:[F,null]` (the `Jf` dropdown in `kyn`) | `onOpenChange:c` → `onOpenChange:CodexMuxProfileMenuOpenChange(c)` |
| 13 | depletion messages ×3 | pooled depletion wording | app-primary | ``defaultMessage:`You’re out of Codex and Work usage` ``, ``defaultMessage:`You’ve used all Codex and Work usage` ``, ``defaultMessage:`You’ve reached your usage limit` `` | replace text with ``All connected subscriptions are depleted`` (ids/descriptions untouched) |
| 14 | profile avatar/name/plan | avatar stack + per-account stats | `profile-108e93a1eff6.js` (selected by anchor from 3 `profile-*.js` files) | `avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`label`,` | prepend `globalThis.CodexMuxProfileAvatarStack?.({})??null,`; hide native label via `className:globalThis.CodexMuxProfileAvatarStack?`hidden`:ue(…)`; name/username left visible because the patched `pJs` already returns the selected account's data; stack self-invalidates `["profile",…]` queries via the mapped client |
| 15 | plugins settings `action:F,children:w})` | subscription picker above shared defs | `plugins-settings-4b013a628202.js` (selected by anchor from 3 files) | `action:F,children:w})` | `children:[globalThis.CodexMuxPluginScope?.()??null,w]` |
| 16 | thread component `bE` + section list | subscription indicator in summary | `local-conversation-thread-b5c1b90153e1.js` (selected by anchor from 3 files) | `function iE(e){let t=(0,sE.c)(37)` (summary root rendering `kb`) and `children:[m,h,g,_,v,y,b,x]` (`v` = `usage` section) | prepend transformed thread component (`$n(sr)`→`ve(ds)`, `TE`→`Uk`, `zE`→`cE`, `K`→`Z`); insert `(0,cE.jsx)(CodexMuxThreadSubscription,{})` after `v` |

`cE`, `Uk`, `ve`/`ds`, `Z` are module-scope in the thread chunk
(`cE=J()` jsx runtime, `Uk=t(De(),1)` React interop,
`ve`←`E3t`/`ds`←`fRt` imports, `Z`←`jh` import).
`route.value.routeKind===`local-thread`?route.value.conversationId:null`
is preserved verbatim by the `$n(sr)`→`ve(ds)` swap.
