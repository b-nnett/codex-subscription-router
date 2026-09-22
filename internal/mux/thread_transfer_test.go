package mux

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/backend"
	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

const transferThreadID = "019f5020-0000-7000-8000-000000000001"
const transferFilename = "rollout-2026-07-10T00-00-00-" + transferThreadID + ".jsonl"

func TestCopyThreadRollout(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, transferFilename)
	history := []byte("synthetic durable history\n")
	if err := os.WriteFile(source, history, 0o600); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "target")
	path, err := copyThreadRollout(source, target, transferThreadID)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(target, "sessions", "2026", "07", "10", transferFilename)
	if path != want {
		t.Fatalf("path = %q, want %q", path, want)
	}
	if data, err := os.ReadFile(path); err != nil || !bytes.Equal(data, history) {
		t.Fatalf("copy = %q, %v", data, err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("permissions = %v", info.Mode())
	}
	// A later handoff replaces an older snapshot without sharing the live writer.
	if err := os.WriteFile(source, []byte("new history\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := copyThreadRollout(source, target, transferThreadID); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("target writes\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(source); string(data) != "new history\n" {
		t.Fatal("target modified source history")
	}
	for _, id := range []string{"", "../escape", "different-thread"} {
		if _, err := copyThreadRollout(source, target, id); err == nil {
			t.Fatalf("accepted invalid thread %q", id)
		}
	}
	if _, err := copyThreadRollout(filepath.Join(root, "missing", transferFilename), target, transferThreadID); err == nil {
		t.Fatal("accepted missing history")
	}
	if data, _ := os.ReadFile(path); string(data) != "target writes\n" {
		t.Fatal("failed copy changed destination")
	}
	if _, err := copyThreadRollout(path, target, transferThreadID); err == nil {
		t.Fatal("accepted a shared source and target rollout")
	}
}

// Run against the installed app-server, without credentials or model requests:
// CODEX_MUX_TEST_CODEX=/path/to/codex.real go test ./internal/mux -run TestTransferRealCodex -v
func TestTransferRealCodex(t *testing.T) {
	executable := os.Getenv("CODEX_MUX_TEST_CODEX")
	if executable == "" {
		t.Skip("set CODEX_MUX_TEST_CODEX to run the real app-server regression")
	}
	for _, mode := range []string{"legacy", "paginated"} {
		t.Run(mode, func(t *testing.T) {
			root, err := filepath.EvalSymlinks(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			sourceHome := filepath.Join(root, "source")
			store, err := state.Open(filepath.Join(root, "state"), sourceHome)
			if err != nil {
				t.Fatal(err)
			}
			target, err := store.AddAccount("target")
			if err != nil {
				t.Fatal(err)
			}
			var output bytes.Buffer
			m, err := New(Options{RealExecutable: executable, Store: store, Output: &output})
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			start := func(id, home string) *backend.Child {
				t.Helper()
				if err := os.MkdirAll(home, 0o700); err != nil {
					t.Fatal(err)
				}
				child, err := backend.Start(id, home, executable, []string{"app-server", "-c", `model="gpt-5.4"`, "-c", "features.plugins=false"}, os.Environ(), m.inbound)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_ = child.Close()
					select {
					case <-child.Done():
					case <-time.After(10 * time.Second):
						t.Error("app-server did not stop")
					}
				})
				// Drain internal notifications without changing routing ownership during this test.
				m.children[id] = child
				params := json.RawMessage(`{"clientInfo":{"name":"router-regression","version":"1"},"capabilities":{"experimentalApi":true}}`)
				if _, err := child.Request(ctx, "initialize", params); err != nil {
					t.Fatal(err)
				}
				return child
			}
			go func() {
				for {
					select {
					case <-ctx.Done():
						return
					case <-m.inbound:
					}
				}
			}()
			source := start("primary", sourceHome)
			destination := start(target.ID, target.CodexHome)
			path := filepath.Join(sourceHome, "sessions", "2026", "07", "10", transferFilename)
			if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
				t.Fatal(err)
			}
			meta, _ := json.Marshal(map[string]any{
				"timestamp": "2026-07-10T00:00:00Z", "ordinal": 0, "type": "session_meta",
				"payload": map[string]any{"id": transferThreadID, "session_id": transferThreadID,
					"timestamp": "2026-07-10T00:00:00Z", "cwd": root, "originator": "test", "cli_version": "test",
					"source": "cli", "model_provider": "openai", "history_mode": mode},
			})
			preview, _ := json.Marshal(map[string]any{
				"timestamp": "2026-07-10T00:00:01Z", "ordinal": 1, "type": "event_msg",
				"payload": map[string]any{"type": "user_message", "message": "synthetic chat preview", "kind": "plain"},
			})
			fixture := append(append(append(meta, '\n'), preview...), '\n')
			if err := os.WriteFile(path, fixture, 0o600); err != nil {
				t.Fatal(err)
			}
			resume, _ := json.Marshal(map[string]any{"threadId": transferThreadID, "path": path, "cwd": root, "model": "gpt-5.4"})
			if mode == "paginated" {
				if _, err := destination.Request(ctx, "thread/resume", resume); err == nil || !strings.Contains(err.Error(), "no rollout found") {
					t.Fatalf("expected original bug, got %v", err)
				}
			}
			if _, err := source.Request(ctx, "thread/resume", resume); err != nil {
				t.Fatal(err)
			}
			injection, _ := json.Marshal(map[string]any{"threadId": transferThreadID, "items": []any{map[string]any{"type": "message", "role": "user", "content": []any{map[string]any{"type": "input_text", "text": "synthetic transfer marker"}}}}})
			if _, err := source.Request(ctx, "thread/inject_items", injection); err != nil {
				t.Fatal(err)
			}
			if err := m.resumeThreadOnAccount(ctx, transferThreadID, "primary", target.ID); err != nil {
				t.Fatal(err)
			}
			copied := filepath.Join(target.CodexHome, "sessions", "2026", "07", "10", transferFilename)
			history, err := os.ReadFile(copied)
			if err != nil || !bytes.Contains(history, []byte("synthetic transfer marker")) {
				t.Fatalf("missing durable history: %v", err)
			}
			if err := requireUnloadedThread(ctx, destination, transferThreadID); err == nil {
				t.Fatal("target was not resumed")
			}
			if err := store.SetThreadOwner(transferThreadID, target.ID); err != nil {
				t.Fatal(err)
			}
			m.aggregateThreadList(protocol.Request("thread/list", json.RawMessage(`1`), json.RawMessage(`{"sourceKinds":["cli"]}`)))
			var listed struct {
				Result struct {
					Data []struct {
						ID string `json:"id"`
					} `json:"data"`
				} `json:"result"`
			}
			if err := json.Unmarshal(output.Bytes(), &listed); err != nil {
				t.Fatal(err)
			}
			if len(listed.Result.Data) != 1 || listed.Result.Data[0].ID != transferThreadID {
				t.Fatalf("duplicate or missing transferred thread: %s", output.Bytes())
			}
			if owner, _ := store.ThreadOwner(transferThreadID); owner != target.ID {
				t.Fatal("thread/list reverted ownership")
			}
			if err := m.resumeThreadOnAccount(ctx, transferThreadID, target.ID, "primary"); err == nil || !strings.Contains(err.Error(), "still has this chat loaded") {
				t.Fatalf("unsafe reverse handoff: %v", err)
			}
			injection = bytes.ReplaceAll(injection, []byte("synthetic transfer marker"), []byte("new target history marker"))
			if _, err := destination.Request(ctx, "thread/inject_items", injection); err != nil {
				t.Fatal(err)
			}
			original, _ := os.ReadFile(path)
			if bytes.Contains(original, []byte("new target history marker")) {
				t.Fatal("target changed source's live rollout")
			}
			if err := source.Close(); err != nil {
				t.Fatal(err)
			}
			select {
			case <-source.Done():
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
			start("primary", sourceHome)
			if err := m.resumeThreadOnAccount(ctx, transferThreadID, target.ID, "primary"); err != nil {
				t.Fatal(err)
			}
			returned, _ := os.ReadFile(path)
			if !bytes.Contains(returned, []byte("new target history marker")) {
				t.Fatal("return handoff lost latest history")
			}
		})
	}
}

func TestTransferredThreadIgnoresOldAccountNotifications(t *testing.T) {
	store, err := state.Open(t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetThreadOwner(transferThreadID, "target"); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	m, err := New(Options{RealExecutable: "unused", Store: store, Output: &output})
	if err != nil {
		t.Fatal(err)
	}
	for _, message := range []protocol.Message{
		{Method: "thread/started", Params: json.RawMessage(`{"thread":{"id":"` + transferThreadID + `"}}`)},
		{Method: "thread/closed", Params: json.RawMessage(`{"threadId":"` + transferThreadID + `"}`)},
	} {
		raw, _ := protocol.Encode(message)
		m.handleInbound(backend.Inbound{AccountID: "primary", Message: message, Raw: raw})
		if output.Len() != 0 {
			t.Fatal("forwarded stale source notification")
		}
		if owner, _ := store.ThreadOwner(transferThreadID); owner != "target" {
			t.Fatal("notification reverted ownership")
		}
		m.handleInbound(backend.Inbound{AccountID: "target", Message: message, Raw: raw})
		if output.Len() == 0 {
			t.Fatal("dropped owner notification")
		}
		output.Reset()
	}
}
