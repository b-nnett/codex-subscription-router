package mux

import "testing"

func TestMergeThreadListingsKeepsAssignedOwnerCopy(t *testing.T) {
	owners := map[string]string{"moved": "secondary"}
	listings := []threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 1.0, "source": "primary"},
			{"id": "own", "updatedAt": 2.0},
		}},
		{accountID: "secondary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 3.0, "source": "secondary"},
		}},
	}
	threads, learned := mergeThreadListings(listings, func(id string) (string, bool) {
		owner, ok := owners[id]
		return owner, ok
	})
	if len(threads) != 2 {
		t.Fatalf("expected the moved thread to be listed once, got %d threads", len(threads))
	}
	for _, thread := range threads {
		if thread["id"] == "moved" && thread["source"] != "secondary" {
			t.Fatalf("expected the owner's copy of the moved thread, got %#v", thread)
		}
	}
	if _, relearned := learned["moved"]; relearned {
		t.Fatal("an assigned thread must not be reassigned by a listing")
	}
	if learned["own"] != "primary" {
		t.Fatalf("expected the unassigned thread to be learned as primary, got %q", learned["own"])
	}
}

func TestMergeThreadListingsAttributesUnknownThreadOnce(t *testing.T) {
	listings := []threadListing{
		{accountID: "secondary", threads: []map[string]any{{"id": "shared", "source": "secondary"}}},
		{accountID: "primary", threads: []map[string]any{{"id": "shared", "source": "primary"}}},
	}
	threads, learned := mergeThreadListings(listings, func(string) (string, bool) { return "", false })
	if len(threads) != 1 || threads[0]["source"] != "secondary" {
		t.Fatalf("expected the first listing to win for an unknown thread, got %#v", threads)
	}
	if learned["shared"] != "secondary" {
		t.Fatalf("expected the unknown thread to be learned from its first listing, got %q", learned["shared"])
	}
}
