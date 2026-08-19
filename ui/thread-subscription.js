const CODEX_MUX_THREAD_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_THREAD_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

function CodexMuxThreadSubscription() {
  const route = Xn(ec);
  const threadId =
    route.value.routeKind === "local-thread" ? route.value.conversationId : null;
  const [account, setAccount] = Fk.useState(null);

  Fk.useEffect(() => {
    let active = true;
    if (!threadId) {
      setAccount(null);
      return () => {
        active = false;
      };
    }

    const refresh = async () => {
      try {
        const response = await fetch(
          `${CODEX_MUX_THREAD_API}/thread-account?threadId=${encodeURIComponent(threadId)}`,
          { headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN } },
        );
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const body = await response.json();
        if (active) setAccount(body.account || null);
      } catch {
        if (active) setAccount(null);
      }
    };

    refresh();
    const events = new EventSource(
      `${CODEX_MUX_THREAD_API}/events?token=${encodeURIComponent(CODEX_MUX_THREAD_TOKEN)}`,
    );
    events.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (
          payload.type === "account-updated" ||
          (payload.type === "thread-failed-over" &&
            payload.data?.threadId === threadId)
        ) {
          refresh();
        }
      } catch {}
    };
    const warmupTimer = setTimeout(refresh, 2_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      active = false;
      clearTimeout(warmupTimer);
      clearInterval(timer);
      events.close();
    };
  }, [threadId]);

  if (!account) return null;
  const weekly = codexMuxThreadWeeklyWindow(account.rateLimits);
  const remaining = weekly == null ? null : Math.max(0, 100 - weekly.usedPercent);
  const depleted = remaining === 0;
  const AccountAvatar = globalThis.CodexMuxAccountAvatar;
  return (0, TE.jsx)(Z.Section, {
    sectionKey: "codex-mux-subscription",
    title: "Subscription",
    children: (0, TE.jsxs)("div", {
      className: "flex min-h-9 items-center justify-between gap-3 py-1 text-sm",
      children: [
        (0, TE.jsxs)("div", {
          className: "flex min-w-0 items-center gap-2",
          children: [
            AccountAvatar
              ? (0, TE.jsx)(AccountAvatar, {
                  imageUrl: account.profileImageUrl,
                  label: account.label,
                  className: "size-5 shrink-0",
                })
              : null,
            (0, TE.jsx)("span", {
              className: "truncate text-token-text-primary",
              children: account.planLabel
                ? `${account.label} · ${account.planLabel}`
                : account.label,
            }),
          ],
        }),
        (0, TE.jsx)("span", {
          className: "shrink-0 tabular-nums text-token-description-foreground",
          children:
            remaining == null
              ? "Usage unavailable"
              : depleted
                ? "Depleted"
                : `${Math.round(remaining)}% remaining`,
        }),
      ],
    }),
  });
}

function codexMuxThreadWeeklyWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}
