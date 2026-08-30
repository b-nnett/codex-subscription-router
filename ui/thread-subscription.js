const CODEX_MUX_THREAD_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_THREAD_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

function CodexMuxThreadSubscription() {
  const route = $n(sr);
  const threadId =
    route.value.routeKind === "local-thread" ? route.value.conversationId : null;
  const [account, setAccount] = TE.useState(null);
  const [accounts, setAccounts] = TE.useState([]);
  const [switching, setSwitching] = TE.useState(false);
  const [error, setError] = TE.useState("");
  const [selectorOpen, setSelectorOpen] = TE.useState(false);

  TE.useEffect(() => {
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
        const [body, accountsResponse] = await Promise.all([
          response.json(),
          fetch(`${CODEX_MUX_THREAD_API}/accounts`, {
            headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN },
          }).then((result) => (result.ok ? result.json() : { accounts: [] })),
        ]);
        if (active) {
          setAccount(body.account || null);
          setAccounts(
            (accountsResponse.accounts || []).filter(
              (candidate) =>
                candidate.enabled &&
                candidate.connected &&
                candidate.authType === "chatgpt",
            ),
          );
          setError("");
        }
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
          ((payload.type === "thread-failed-over" ||
            payload.type === "thread-account-changed") &&
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
  const short = codexMuxThreadFiveHourWindow(account.rateLimits);
  const weekly = codexMuxThreadWeeklyWindow(account.rateLimits);
  const remaining =
    short == null ? null : Math.max(0, 100 - short.usedPercent);
  const depleted = remaining === 0;
  const AccountAvatar = globalThis.CodexMuxAccountAvatar;

  async function switchAccount(accountId) {
    if (!accountId || accountId === account.id || switching) return;
    setSelectorOpen(false);
    setSwitching(true);
    setError("");
    try {
      const response = await fetch(`${CODEX_MUX_THREAD_API}/thread-account`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN,
        },
        body: JSON.stringify({ threadId, accountId }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || `Request failed (${response.status})`);
      }
      setAccount(body.account || null);
    } catch (switchError) {
      setError(switchError.message);
    } finally {
      setSwitching(false);
    }
  }
  return (0, zE.jsx)(K.Section, {
    sectionKey: "codex-mux-subscription",
    title: "Subscription",
    children: (0, zE.jsxs)("div", {
      className: "space-y-2 py-1 text-sm",
      children: [
        (0, zE.jsxs)("div", {
          className: "flex min-h-9 items-center justify-between gap-3",
          children: [
            (0, zE.jsxs)("div", {
              className: "flex min-w-0 items-center gap-2",
              children: [
                AccountAvatar
                  ? (0, zE.jsx)(AccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-5 shrink-0",
                    })
                  : null,
                (0, zE.jsx)("span", {
                  className: "truncate text-token-text-primary",
                  children: account.planLabel
                    ? `${account.label} · ${account.planLabel}`
                    : account.label,
                }),
              ],
            }),
            (0, zE.jsxs)("span", {
              className: "shrink-0 text-right tabular-nums text-token-description-foreground",
              children: [
                (0, zE.jsx)("span", {
                  className: "block",
                  children: remaining == null ? "5h unavailable" : depleted ? "5h depleted" : `${Math.round(remaining)}% 5h`,
                }),
                (0, zE.jsx)("span", {
                  className: "block text-xs",
                  children: weekly == null ? "weekly unavailable" : `${Math.round(Math.max(0, 100 - weekly.usedPercent))}% weekly`,
                }),
              ],
            }),
          ],
        }),
        accounts.length > 1
          ? (0, zE.jsxs)("div", {
              className: "relative",
              children: [
                (0, zE.jsxs)("button", {
                  type: "button",
                  className: "flex w-full items-center justify-between rounded-xl border border-token-border bg-token-bg-primary px-3 py-2 text-left text-sm text-token-text-primary shadow-xs transition-colors hover:bg-token-foreground/5 focus:border-token-text-secondary",
                  "aria-expanded": selectorOpen,
                  "aria-haspopup": "listbox",
                  onClick: () => setSelectorOpen((open) => !open),
                  children: [
                    (0, zE.jsx)("span", {
                      className: "truncate",
                      children: `${account.label} · ${remaining == null ? "5h unavailable" : `${Math.round(remaining)}% 5h`} · ${weekly == null ? "weekly unavailable" : `${Math.round(Math.max(0, 100 - weekly.usedPercent))}% weekly`}`,
                    }),
                    (0, zE.jsx)("span", {
                      className: "ml-2 text-token-text-secondary",
                      children: selectorOpen ? "⌃" : "⌄",
                    }),
                  ],
                }),
                selectorOpen
                  ? (0, zE.jsx)("div", {
                      role: "listbox",
                      className: "mt-1 overflow-hidden rounded-xl border border-token-border bg-token-bg-primary p-1 shadow-lg",
                      children: accounts.map((candidate) => {
                        const candidateShort = codexMuxThreadFiveHourWindow(candidate.rateLimits);
                        const candidateRemaining = candidateShort == null ? null : Math.max(0, 100 - candidateShort.usedPercent);
                        const candidateWeekly = codexMuxThreadWeeklyWindow(candidate.rateLimits);
                        const candidateWeeklyRemaining = candidateWeekly == null ? null : Math.max(0, 100 - candidateWeekly.usedPercent);
                        const candidateReset = codexMuxThreadFormatResetTime(candidateShort?.resetsAt);
                        const candidateWeeklyReset = codexMuxThreadFormatResetTime(candidateWeekly?.resetsAt);
                        const depletedCandidate = candidateRemaining === 0;
                        return (0, zE.jsxs)("button", {
                          type: "button",
                          role: "option",
                          "aria-selected": candidate.id === account.id,
                          disabled: depletedCandidate || switching,
                          onClick: () => switchAccount(candidate.id),
                          className: "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-token-foreground/5 disabled:cursor-not-allowed disabled:opacity-50",
                          children: [
                            (0, zE.jsxs)("span", {
                              className: "min-w-0",
                              children: [
                                (0, zE.jsx)("span", { className: "block truncate", children: `${candidate.label} · ${candidateRemaining == null ? "5h unavailable" : `${Math.round(candidateRemaining)}% 5h`} · ${candidateWeeklyRemaining == null ? "weekly unavailable" : `${Math.round(candidateWeeklyRemaining)}% weekly`}` }),
                                (0, zE.jsx)("span", { className: "block truncate text-xs text-token-text-tertiary", children: `5h resets ${candidateReset || "—"} · weekly resets ${candidateWeeklyReset || "—"}` }),
                              ],
                            }),
                            candidate.id === account.id ? (0, zE.jsx)("span", { className: "text-lg text-token-text-secondary", children: "✓" }) : null,
                          ],
                        }, candidate.id);
                      }),
                    })
                  : null,
              ],
            })
          : null,
        switching
          ? (0, zE.jsx)("div", {
              className: "text-xs text-token-text-tertiary",
              children: "Moving chat history…",
            })
          : null,
        error
          ? (0, zE.jsx)("div", {
              className: "text-xs text-red-500",
              children: error,
            })
          : null,
      ],
    }),
  });
}

function codexMuxThreadFiveHourWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  const exact = windows.find((window) => window.windowDurationMins === 300);
  if (exact) return exact;
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.length > 1 || (windows[0]?.windowDurationMins || 0) <= 300
    ? windows[0] || null
    : null;
}

function codexMuxThreadWeeklyWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}

function codexMuxThreadFormatResetTime(resetsAt) {
  if (resetsAt == null) return "";
  const date = new Date(resetsAt * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
