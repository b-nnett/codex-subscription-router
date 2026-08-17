package mux

import (
	"context"
	"encoding/json"
	"sort"
	"sync"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func (m *Multiplexer) aggregateThreadList(request protocol.Message) {
	entries := m.childEntries()
	type result struct {
		accountID string
		threads   []map[string]any
	}
	results := make(chan result, len(entries))
	var wait sync.WaitGroup
	for _, entry := range entries {
		wait.Add(1)
		go func(entry childEntry) {
			defer wait.Done()
			results <- result{accountID: entry.account.ID, threads: m.listAllThreads(entry, request.Params)}
		}(entry)
	}
	wait.Wait()
	close(results)

	listings := make([]threadListing, 0, len(entries))
	for accountResult := range results {
		listings = append(listings, threadListing(accountResult))
	}
	controllerID := ""
	if controller, ok := m.store.Controller(); ok {
		controllerID = controller.ID
	}
	sort.SliceStable(listings, func(i, j int) bool {
		if (listings[i].accountID == controllerID) != (listings[j].accountID == controllerID) {
			return listings[i].accountID == controllerID
		}
		return listings[i].accountID < listings[j].accountID
	})
	threads, learned := mergeThreadListings(listings, m.store.ThreadOwner)
	for threadID, accountID := range learned {
		_ = m.store.SetThreadOwner(threadID, accountID)
	}
	sortThreads(threads)
	encoded, err := json.Marshal(map[string]any{"data": threads, "nextCursor": nil})
	if err != nil {
		m.write(protocol.Failure(request.ID, -32603, "failed to merge thread list"))
		return
	}
	m.write(protocol.Success(request.ID, encoded))
}

type threadListing struct {
	accountID string
	threads   []map[string]any
}

// mergeThreadListings combines per-account thread lists into one view. A
// thread that has been moved between subscriptions exists in more than one
// account's history, so only the assigned owner's copy is kept and existing
// assignments are never changed by a listing; threads without an assignment
// are attributed to the first account that lists them (the controller first).
func mergeThreadListings(
	listings []threadListing,
	owner func(threadID string) (string, bool),
) ([]map[string]any, map[string]string) {
	learned := make(map[string]string)
	position := make(map[string]int)
	threads := make([]map[string]any, 0)
	for _, listing := range listings {
		for _, thread := range listing.threads {
			threadID, ok := thread["id"].(string)
			if !ok || threadID == "" {
				threads = append(threads, thread)
				continue
			}
			assigned, known := owner(threadID)
			if !known {
				assigned, known = learned[threadID]
			}
			if !known {
				learned[threadID] = listing.accountID
				assigned = listing.accountID
			}
			index, seen := position[threadID]
			switch {
			case !seen:
				position[threadID] = len(threads)
				threads = append(threads, thread)
			case listing.accountID == assigned:
				threads[index] = thread
			}
		}
	}
	return threads, learned
}

func (m *Multiplexer) listAllThreads(entry childEntry, originalParams json.RawMessage) []map[string]any {
	var params map[string]any
	if json.Unmarshal(originalParams, &params) != nil {
		params = make(map[string]any)
	}
	params["limit"] = 500
	threads := make([]map[string]any, 0)
	seenCursors := make(map[string]struct{})
	var cursor string
	for {
		if cursor == "" {
			params["cursor"] = nil
		} else {
			params["cursor"] = cursor
		}
		encodedParams, _ := json.Marshal(params)
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		response, err := entry.child.Request(ctx, "thread/list", encodedParams)
		cancel()
		if err != nil {
			return threads
		}
		var decoded struct {
			Data       []map[string]any `json:"data"`
			NextCursor *string          `json:"nextCursor"`
		}
		if json.Unmarshal(response.Result, &decoded) != nil {
			return threads
		}
		threads = append(threads, decoded.Data...)
		if decoded.NextCursor == nil || *decoded.NextCursor == "" {
			return threads
		}
		cursor = *decoded.NextCursor
		if _, repeated := seenCursors[cursor]; repeated {
			return threads
		}
		seenCursors[cursor] = struct{}{}
	}
}
