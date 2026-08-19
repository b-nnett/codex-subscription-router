package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const isolatedCredentialConfig = `cli_auth_credentials_store = "file"
mcp_oauth_credentials_store = "file"`

// syncIsolatedConfig shares desktop-managed settings and MCP servers with an
// isolated subscription while keeping its credentials and project trust local.
func syncIsolatedConfig(primaryCodexHome, isolatedCodexHome string) error {
	if isolatedCodexHome == "" {
		return errors.New("isolated Codex home is required")
	}
	if err := os.MkdirAll(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("create isolated Codex home: %w", err)
	}
	if err := os.Chmod(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("secure isolated Codex home: %w", err)
	}

	primaryConfig, err := readConfig(filepath.Join(primaryCodexHome, "config.toml"))
	if err != nil {
		return fmt.Errorf("read primary config: %w", err)
	}
	configPath := filepath.Join(isolatedCodexHome, "config.toml")
	isolatedConfig, err := readConfig(configPath)
	if err != nil {
		return fmt.Errorf("read isolated config: %w", err)
	}

	managed := filterConfig(primaryConfig, func(section string) bool {
		return !isProjectSection(section)
	})
	managed = removeTopLevelCredentialSettings(managed)
	projects := filterConfig(isolatedConfig, isProjectSection)

	parts := []string{isolatedCredentialConfig}
	if managed = strings.TrimSpace(managed); managed != "" {
		parts = append(parts, managed)
	}
	if projects = strings.TrimSpace(projects); projects != "" {
		parts = append(parts, projects)
	}
	contents := []byte(strings.Join(parts, "\n\n") + "\n")
	temporaryPath := configPath + ".tmp"
	if err := os.WriteFile(temporaryPath, contents, 0o600); err != nil {
		return fmt.Errorf("write temporary config: %w", err)
	}
	if err := os.Chmod(temporaryPath, 0o600); err != nil {
		return fmt.Errorf("secure temporary config: %w", err)
	}
	if err := os.Rename(temporaryPath, configPath); err != nil {
		return fmt.Errorf("commit config: %w", err)
	}
	return nil
}

func readConfig(path string) ([]byte, error) {
	contents, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return contents, err
}

func TopLevelConfigValue(contents []byte, key string) string {
	for _, line := range strings.Split(string(contents), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			return ""
		}
		name, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(name) != key {
			continue
		}
		value = strings.TrimSpace(strings.SplitN(value, "#", 2)[0])
		return strings.Trim(strings.TrimSpace(value), `"'`)
	}
	return ""
}

func filterConfig(contents []byte, keep func(section string) bool) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(string(contents), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if keep(section) {
			builder.WriteString(line)
			builder.WriteByte('\n')
		}
	}
	return builder.String()
}

func removeTopLevelCredentialSettings(contents string) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if section == "" && (strings.HasPrefix(trimmed, "cli_auth_credentials_store =") ||
			strings.HasPrefix(trimmed, "mcp_oauth_credentials_store =")) {
			continue
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String()
}

func isProjectSection(section string) bool {
	return section == "projects" || strings.HasPrefix(section, "projects.")
}

func samePath(left, right string) bool {
	if left == "" || right == "" {
		return false
	}
	leftAbsolute, leftErr := filepath.Abs(left)
	rightAbsolute, rightErr := filepath.Abs(right)
	if leftErr != nil || rightErr != nil {
		return filepath.Clean(left) == filepath.Clean(right)
	}
	return filepath.Clean(leftAbsolute) == filepath.Clean(rightAbsolute)
}
