package mux

import (
	"testing"
	"time"
)

func TestSnapshotCacheReturnsFreshEntries(t *testing.T) {
	cache := newSnapshotCache()
	now := time.Date(2026, time.August, 17, 12, 0, 0, 0, time.UTC)
	cache.put(AccountSnapshot{ID: "primary", Connected: true}, now)

	snapshot, ok := cache.get("primary", now.Add(routingSnapshotMaxAge), routingSnapshotMaxAge)
	if !ok || !snapshot.Connected {
		t.Fatalf("expected a fresh cached snapshot, got %#v ok=%v", snapshot, ok)
	}
	if _, ok := cache.get("primary", now.Add(routingSnapshotMaxAge+time.Second), routingSnapshotMaxAge); ok {
		t.Fatal("expected an expired snapshot to be ignored")
	}
	if _, ok := cache.get("missing", now, routingSnapshotMaxAge); ok {
		t.Fatal("expected an unknown account to miss")
	}
}

func TestSnapshotCacheUpdateRateLimitsRefreshesEntry(t *testing.T) {
	cache := newSnapshotCache()
	now := time.Date(2026, time.August, 17, 12, 0, 0, 0, time.UTC)
	if cache.updateRateLimits("primary", RateLimits{}, now) {
		t.Fatal("expected no update for an unknown account")
	}
	cache.put(AccountSnapshot{ID: "primary", Connected: true, AuthType: "chatgpt"}, now)

	later := now.Add(routingSnapshotMaxAge)
	limits := RateLimits{Primary: &RateLimitWindow{UsedPercent: 100}}
	if !cache.updateRateLimits("primary", limits, later) {
		t.Fatal("expected the cached entry to be updated")
	}
	snapshot, ok := cache.get("primary", later.Add(routingSnapshotMaxAge), routingSnapshotMaxAge)
	if !ok {
		t.Fatal("expected the refreshed entry to remain fresh")
	}
	if snapshot.RateLimits == nil || snapshot.RateLimits.Primary.UsedPercent != 100 {
		t.Fatalf("expected refreshed rate limits, got %#v", snapshot.RateLimits)
	}
	if !snapshot.Connected || snapshot.AuthType != "chatgpt" {
		t.Fatalf("expected the rest of the snapshot to be preserved, got %#v", snapshot)
	}
}

func TestSnapshotCacheForget(t *testing.T) {
	cache := newSnapshotCache()
	now := time.Date(2026, time.August, 17, 12, 0, 0, 0, time.UTC)
	cache.put(AccountSnapshot{ID: "primary"}, now)
	cache.forget("primary")
	if _, ok := cache.get("primary", now, routingSnapshotMaxAge); ok {
		t.Fatal("expected a forgotten account to miss")
	}
}
