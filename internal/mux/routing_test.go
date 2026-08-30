package mux

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
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

func TestUsageLimitNotificationRecognizesTerminalAsyncError(t *testing.T) {
	params := json.RawMessage(`{
		"threadId":"thread-1",
		"willRetry":false,
		"error":{"message":"usage limit reached","codexErrorInfo":"usage_limit_exceeded"}
	}`)
	if !isTerminalUsageLimitNotification("error", params) {
		t.Fatal("expected terminal asynchronous usage error to trigger failover")
	}
	if got := notificationThreadID(params); got != "thread-1" {
		t.Fatalf("notification thread ID = %q", got)
	}
}

func TestUsageLimitNotificationIgnoresRetryableError(t *testing.T) {
	params := json.RawMessage(`{"threadId":"thread-1","willRetry":true,"error":{"message":"rate limit"}}`)
	if isTerminalUsageLimitNotification("error", params) {
		t.Fatal("router must let Codex finish its own retry first")
	}
}

func TestContinuationParamsKeepTurnSettingsAndReplaceInput(t *testing.T) {
	original := json.RawMessage(`{
		"threadId":"thread-1",
		"model":"gpt-test",
		"effort":"high",
		"input":[{"type":"text","text":"original request"}]
	}`)
	params, err := automaticContinuationParams(original)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(params, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["threadId"] != "thread-1" || decoded["model"] != "gpt-test" || decoded["effort"] != "high" {
		t.Fatalf("turn settings were not preserved: %#v", decoded)
	}
	input, ok := decoded["input"].([]any)
	if !ok || len(input) != 1 || input[0].(map[string]any)["text"] == "original request" {
		t.Fatalf("original input was not replaced safely: %#v", decoded["input"])
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
