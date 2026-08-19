package mux

import (
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func TestIsUsageLimitResponseRecognizesStructuredError(t *testing.T) {
	message := protocol.Message{Error: &protocol.RPCError{
		Code:    -32000,
		Message: "turn failed",
		Data:    json.RawMessage(`{"codexErrorInfo":"usage_limit_exceeded"}`),
	}}
	if !isUsageLimitResponse(message) {
		t.Fatal("expected usage-limit error to be recognized")
	}
}

func TestIsUsageLimitResponseIgnoresUnrelatedError(t *testing.T) {
	message := protocol.Message{Error: &protocol.RPCError{
		Code:    -32000,
		Message: "workspace folder is unavailable",
	}}
	if isUsageLimitResponse(message) {
		t.Fatal("unrelated error was misclassified as a usage limit")
	}
}

func TestBypassesChatGPTQuotaForExternalModels(t *testing.T) {
	cases := []json.RawMessage{
		json.RawMessage(`{"model":"moonshot/kimi-k3"}`),
		json.RawMessage(`{"model":"ollama/llama3.1"}`),
		json.RawMessage(`{"modelProvider":"kimi-local"}`),
		json.RawMessage(`{"model":"kimi-k3","modelProvider":"openrouter"}`),
	}
	for _, params := range cases {
		if !bypassesChatGPTQuota(params) {
			t.Fatalf("expected quota bypass for %s", params)
		}
	}
}

func TestBypassesChatGPTQuotaRejectsChatGPTModels(t *testing.T) {
	cases := []json.RawMessage{
		nil,
		json.RawMessage(`{}`),
		json.RawMessage(`{"model":"gpt-5.6-sol"}`),
		json.RawMessage(`{"model":"openai/gpt-5"}`),
		json.RawMessage(`{"model":"o3"}`),
		json.RawMessage(`{"model":"kimi-k3"}`),
		json.RawMessage(`{"model":"codex-mini-latest","modelProvider":"openai"}`),
		json.RawMessage(`{"modelProvider":"chatgpt"}`),
		json.RawMessage(`{"modelProvider":"codex"}`),
		json.RawMessage(`{"model":42,"modelProvider":false}`),
	}
	for _, params := range cases {
		if bypassesChatGPTQuota(params) {
			t.Fatalf("unexpected quota bypass for %s", params)
		}
	}
}

func TestAccountBypassesChatGPTQuotaFromConfig(t *testing.T) {
	codexHome := t.TempDir()
	if err := os.WriteFile(codexHome+"/config.toml", []byte("model = \"moonshot/kimi-k3\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	account := state.Account{CodexHome: codexHome}
	if !accountBypassesChatGPTQuota(json.RawMessage(`{}`), account) {
		t.Fatal("expected account config to bypass ChatGPT quota")
	}
}

func TestAccountBypassesChatGPTQuotaFromCustomBaseURL(t *testing.T) {
	codexHome := t.TempDir()
	if err := os.WriteFile(codexHome+"/config.toml", []byte("model = \"kimi-k3\"\nopenai_base_url = \"http://127.0.0.1:10100/v1\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	account := state.Account{CodexHome: codexHome}
	if !accountBypassesChatGPTQuota(json.RawMessage(`{"model":"kimi-k3"}`), account) {
		t.Fatal("expected custom base URL to bypass ChatGPT quota")
	}
}

func TestExplicitChatGPTParamsOverrideExternalConfig(t *testing.T) {
	codexHome := t.TempDir()
	if err := os.WriteFile(codexHome+"/config.toml", []byte("model = \"moonshot/kimi-k3\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	account := state.Account{CodexHome: codexHome}
	if accountBypassesChatGPTQuota(json.RawMessage(`{"model":"gpt-5.6-sol"}`), account) {
		t.Fatal("explicit ChatGPT model should not bypass quota")
	}
}

func TestAccountBypassesChatGPTQuotaFromProviderConfig(t *testing.T) {
	codexHome := t.TempDir()
	if err := os.WriteFile(codexHome+"/config.toml", []byte("model = \"kimi-k3\"\nmodel_provider = \"kimi-local\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	account := state.Account{CodexHome: codexHome}
	if !accountBypassesChatGPTQuota(nil, account) {
		t.Fatal("expected provider config to bypass ChatGPT quota")
	}
}

func TestAllSubscriptionsDepletedUsesActionableMessage(t *testing.T) {
	message := allSubscriptionsDepleted(json.RawMessage(`7`), nil)
	if message.Error == nil || message.Error.Code != -32026 {
		t.Fatalf("unexpected error response: %#v", message)
	}
	if message.Error.Message != "All connected subscriptions are depleted. Add another subscription or wait for usage to reset." {
		t.Fatalf("unexpected depletion message: %q", message.Error.Message)
	}
}

func TestAllSubscriptionsDepletedShowsKnownResetTime(t *testing.T) {
	reset := time.Date(2026, time.August, 16, 10, 30, 0, 0, time.Local).Unix()
	message := allSubscriptionsDepleted(json.RawMessage(`7`), &reset)
	if message.Error == nil {
		t.Fatal("expected an error response")
	}
	want := "All connected subscriptions are depleted. Usage resets on Sunday, 16 August at 10:30 AM."
	if message.Error.Message != want {
		t.Fatalf("unexpected reset message: %q", message.Error.Message)
	}
}
