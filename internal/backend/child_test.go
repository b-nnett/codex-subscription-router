package backend

import "testing"

func TestChildEnvironmentIsolatesCredentialsAndSharesThreadIndex(t *testing.T) {
	environment := childEnvironment(
		[]string{"PATH=/usr/bin", "CODEX_HOME=old", "CODEX_SQLITE_HOME=old"},
		"/isolated/account",
		"/primary/codex",
	)
	want := map[string]string{
		"CODEX_HOME":        "/isolated/account",
		"CODEX_SQLITE_HOME": "/primary/codex",
	}
	for key, value := range want {
		found := false
		for _, entry := range environment {
			if entry == key+"="+value {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("missing %s=%s in %#v", key, value, environment)
		}
	}
}
