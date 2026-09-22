package mux

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/backend"
)

func requireUnloadedThread(ctx context.Context, target *backend.Child, threadID string) error {
	params := map[string]any{"limit": 100}
	seen := make(map[string]bool)
	for {
		encoded, _ := json.Marshal(params)
		response, err := target.Request(ctx, "thread/loaded/list", encoded)
		if err != nil {
			return fmt.Errorf("check target chat: %w", err)
		}
		var result struct {
			Data       []string `json:"data"`
			NextCursor string   `json:"nextCursor"`
		}
		if err := json.Unmarshal(response.Result, &result); err != nil {
			return err
		}
		for _, id := range result.Data {
			if id == threadID {
				return errors.New("target subscription still has this chat loaded; reopen the router before moving it back")
			}
		}
		if result.NextCursor == "" {
			return nil
		}
		if seen[result.NextCursor] {
			return errors.New("target loaded-chat list repeated a cursor")
		}
		seen[result.NextCursor] = true
		params["cursor"] = result.NextCursor
	}
}

// Copy only this thread's durable history, never another account's SQLite DB or
// credentials. Separate files keep the source's still-loaded writer isolated.
func copyThreadRollout(source, targetHome, threadID string) (string, error) {
	name := filepath.Base(source)
	if threadID == "" || strings.ContainsAny(threadID, `/\\`) || !strings.HasSuffix(name, "-"+threadID+".jsonl") {
		return "", errors.New("rollout filename does not match the requested thread")
	}
	timestamp := strings.TrimSuffix(strings.TrimPrefix(name, "rollout-"), "-"+threadID+".jsonl")
	date, err := time.Parse("2006-01-02T15-04-05", timestamp)
	if err != nil || !strings.HasPrefix(name, "rollout-") {
		return "", errors.New("unsupported rollout filename")
	}
	input, err := os.Open(source)
	if err != nil {
		return "", err
	}
	defer input.Close()
	info, err := input.Stat()
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", errors.New("rollout is not a regular file")
	}
	directory := filepath.Join(targetHome, "sessions", date.Format("2006/01/02"))
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return "", err
	}
	destination := filepath.Join(directory, name)
	if existing, err := os.Stat(destination); err == nil && os.SameFile(info, existing) {
		return "", errors.New("source and target must have separate rollout files")
	}
	output, err := os.CreateTemp(directory, ".transfer-*")
	if err != nil {
		return "", err
	}
	defer os.Remove(output.Name())
	defer output.Close()
	if _, err := io.Copy(output, input); err != nil {
		return "", err
	}
	if err := output.Sync(); err != nil {
		return "", err
	}
	if err := output.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(output.Name(), destination); err != nil {
		return "", err
	}
	return destination, nil
}
