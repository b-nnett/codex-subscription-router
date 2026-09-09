# Fork notes (Intel Mac + macOS 13.5+)

This branch is a fork of the upstream Codex Subscription Router
(`https://github.com/b-nnett/codex-subscription-router`), published as a
complement for Macs the upstream does not target. Upstream remains the
reference for Apple-silicon Full builds; this fork tracks it and adds:

- Intel `x86_64` support: architecture detection, architecture-aware
  compatibility profiles, deterministic `GOARCH=amd64` / `clang -arch x86_64`
  builds, and staged-tree architecture validation.
- macOS 13.5+ installs for tested Intel Core sources via a
  capability-aware installer floor (`--print-install-plan`); untested Intel
  sources and Full builds stay at 14.0, Apple-silicon policy is unchanged.
- Core mode (no Computer Use helper): the 8378 Intel ChatGPT bundle ships no
  `SkyComputerUseService`, so the x86_64 helper patch stays fail-closed and
  Core installs, runs, and retires stale helpers cleanly.
- A re-derived renderer anchor set for ChatGPT 26.903.61454 / build 8378
  (`docs/INTEL-RENDERER-8378.md`), dispatched per build without touching the
  ARM64 anchors.

## What is intentionally identical

Routing, sticky threads, failover, account isolation, pooled usage, the
control API security model (loopback-only, token-authenticated), and the
never-touch-`/Applications/ChatGPT.app` rule are unchanged shared code.

## Syncing with upstream

```sh
git remote add upstream https://github.com/b-nnett/codex-subscription-router.git
git fetch upstream
git checkout intel-x86_64
git merge upstream/main
```

After merging: re-verify every renderer anchor against the new ChatGPT
build (the patcher fails closed on drift by design), add a tested
compatibility profile only after a full review, then run `npm run check`,
`npm run release:check`, and `git diff --check`.

## Publishing checklist

1. Create the GitHub fork and push this branch.
2. In `install.sh`, change `DEFAULT_REPOSITORY_URL` to the fork so the
   one-command installer and source updates track the fork (or export
   `CODEX_SUBSCRIPTION_ROUTER_REPOSITORY_URL` per run).
3. Update `package.json` repository/homepage/bugs URLs to the fork.
4. Update the `curl … install.sh | /bin/bash` line in `README.md` to the
   fork's raw URL.
5. Keep the OpenAI-binary rules: source-only releases, never commit
   `.app` bundles, `.asar` files, or credentials (enforced by
   `npm run release:check`).
