package mux

import (
	"sync"
	"time"
)

// routingSnapshotMaxAge bounds how old a cached account snapshot may be when
// it decides where a turn goes. Children publish account/rateLimits/updated
// after every turn, so entries normally stay current well inside this window.
const routingSnapshotMaxAge = 2 * time.Minute

type snapshotCacheEntry struct {
	snapshot  AccountSnapshot
	updatedAt time.Time
}

// snapshotCache remembers the last account snapshot observed per account so
// routing decisions do not need a round-trip to every child app-server.
type snapshotCache struct {
	mu      sync.Mutex
	entries map[string]snapshotCacheEntry
}

func newSnapshotCache() *snapshotCache {
	return &snapshotCache{entries: make(map[string]snapshotCacheEntry)}
}

func (c *snapshotCache) put(snapshot AccountSnapshot, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.entries[snapshot.ID] = snapshotCacheEntry{snapshot: snapshot, updatedAt: now}
}

func (c *snapshotCache) get(accountID string, now time.Time, maxAge time.Duration) (AccountSnapshot, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[accountID]
	if !ok || now.Sub(entry.updatedAt) > maxAge {
		return AccountSnapshot{}, false
	}
	return entry.snapshot, true
}

// updateRateLimits refreshes only the rate limits of a cached snapshot and
// reports whether an entry existed to update.
func (c *snapshotCache) updateRateLimits(accountID string, limits RateLimits, now time.Time) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[accountID]
	if !ok {
		return false
	}
	entry.snapshot.RateLimits = &limits
	entry.updatedAt = now
	c.entries[accountID] = entry
	return true
}

func (c *snapshotCache) forget(accountID string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.entries, accountID)
}
