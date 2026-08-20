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

const computerUseAppName = "Codex Computer Use.app"

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
	if err := syncSharedComputerUseClient(primaryCodexHome, isolatedCodexHome); err != nil {
		return err
	}
	return nil
}

// syncSharedComputerUseClient makes the desktop-managed Computer Use binaries
// available to an isolated CODEX_HOME while leaving config and session data
// inside that account's own computer-use directory.
func syncSharedComputerUseClient(primaryCodexHome, isolatedCodexHome string) error {
	computerUseApp := strings.TrimSpace(os.Getenv("SKY_CUA_SERVICE_PATH"))
	managedByDesktop := computerUseApp != ""
	if !managedByDesktop {
		computerUseApp = filepath.Join(
			primaryCodexHome,
			"computer-use",
			computerUseAppName,
		)
	}
	computerUseInfo, err := os.Stat(computerUseApp)
	if errors.Is(err, os.ErrNotExist) {
		if managedByDesktop {
			return fmt.Errorf(
				"desktop-managed Computer Use app is missing: %s",
				computerUseApp,
			)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("inspect desktop-managed Computer Use app: %w", err)
	}
	if !computerUseInfo.IsDir() {
		return fmt.Errorf(
			"desktop-managed Computer Use app is not a directory: %s",
			computerUseApp,
		)
	}

	isolatedRoot := filepath.Join(isolatedCodexHome, "computer-use")
	if err := os.MkdirAll(isolatedRoot, 0o700); err != nil {
		return fmt.Errorf("create isolated Computer Use state: %w", err)
	}
	if err := os.Chmod(isolatedRoot, 0o700); err != nil {
		return fmt.Errorf("secure isolated Computer Use state: %w", err)
	}

	sharedApp := filepath.Join(isolatedRoot, computerUseAppName)
	info, err := os.Lstat(sharedApp)
	if err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return fmt.Errorf(
				"isolated Computer Use app exists and is not a managed symlink: %s",
				sharedApp,
			)
		}
		target, readErr := os.Readlink(sharedApp)
		if readErr != nil {
			return fmt.Errorf("read isolated Computer Use app link: %w", readErr)
		}
		if samePath(target, computerUseApp) {
			return nil
		}
		if removeErr := os.Remove(sharedApp); removeErr != nil {
			return fmt.Errorf("replace isolated Computer Use app link: %w", removeErr)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect isolated Computer Use app link: %w", err)
	}
	if err := os.Symlink(computerUseApp, sharedApp); err != nil {
		return fmt.Errorf("share Computer Use app with isolated account: %w", err)
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
