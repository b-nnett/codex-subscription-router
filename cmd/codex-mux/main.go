package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/control"
	"github.com/b-nnett/codex-subscription-router/internal/mux"
	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

const defaultControlPort = 48123

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "routerctl" {
		return runRouterControl(args[1:])
	}
	realExecutable, err := resolveRealExecutable()
	if err != nil {
		return err
	}
	if !isInteractiveAppServer(args) {
		return passthrough(realExecutable, args)
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("resolve home directory: %w", err)
	}
	root := os.Getenv("CODEX_MUX_HOME")
	if root == "" {
		root = filepath.Join(home, ".codex-mux")
	}
	primaryCodexHome := os.Getenv("CODEX_HOME")
	if primaryCodexHome == "" {
		primaryCodexHome = filepath.Join(home, ".codex")
	}
	store, err := state.Open(root, primaryCodexHome)
	if err != nil {
		return err
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	multiplexer, err := mux.New(mux.Options{
		RealExecutable: realExecutable,
		RealArgs:       args,
		Environment:    os.Environ(),
		Store:          store,
		Output:         os.Stdout,
	})
	if err != nil {
		return err
	}
	if err := multiplexer.Start(ctx); err != nil {
		return err
	}
	defer multiplexer.Close()

	token, err := loadOrCreateToken(root)
	if err != nil {
		return err
	}
	port := defaultControlPort
	if value := os.Getenv("CODEX_MUX_CONTROL_PORT"); value != "" {
		if parsed, parseErr := strconv.Atoi(value); parseErr == nil && parsed > 0 && parsed <= 65535 {
			port = parsed
		}
	}
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: account UI unavailable: %v\n", err)
	} else {
		controlServer := control.New(
			listener.Addr().String(),
			token,
			multiplexer,
			os.Getenv("CODEX_MUX_UI_TESTS") == "1",
		)
		go func() {
			if serveErr := controlServer.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
				fmt.Fprintf(os.Stderr, "codex-mux: control server: %v\n", serveErr)
			}
		}()
		defer func() {
			shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer shutdownCancel()
			_ = controlServer.Shutdown(shutdownCtx)
		}()
	}

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 64*1024*1024)
	for scanner.Scan() {
		message, parseErr := protocol.Parse(scanner.Bytes())
		if parseErr != nil {
			fmt.Fprintf(os.Stderr, "codex-mux: ignore invalid client JSON: %v\n", parseErr)
			continue
		}
		multiplexer.HandleClient(message)
	}
	cancel()
	return scanner.Err()
}

func runRouterControl(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: routerctl list | add [label] | login <account-id> | enable <account-id> | disable <account-id>")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("resolve home directory: %w", err)
	}
	root := os.Getenv("CODEX_MUX_HOME")
	if root == "" {
		root = filepath.Join(home, ".codex-mux")
	}
	token, err := loadOrCreateToken(root)
	if err != nil {
		return err
	}
	port := defaultControlPort
	if value := os.Getenv("CODEX_MUX_CONTROL_PORT"); value != "" {
		parsed, parseErr := strconv.Atoi(value)
		if parseErr != nil || parsed <= 0 || parsed > 65535 {
			return fmt.Errorf("invalid CODEX_MUX_CONTROL_PORT %q", value)
		}
		port = parsed
	}
	baseURL := fmt.Sprintf("http://127.0.0.1:%d/v1", port)

	switch args[0] {
	case "list":
		if len(args) != 1 {
			return errors.New("usage: routerctl list")
		}
		response, err := controlRequest(http.MethodGet, baseURL+"/accounts", token, nil)
		if err != nil {
			return err
		}
		return printJSON(response)
	case "add":
		label := strings.TrimSpace(strings.Join(args[1:], " "))
		response, err := controlRequest(
			http.MethodPost,
			baseURL+"/accounts",
			token,
			map[string]string{"label": label},
		)
		if err != nil {
			return err
		}
		var created struct {
			Account struct {
				ID string `json:"id"`
			} `json:"account"`
		}
		if err := json.Unmarshal(response, &created); err != nil || created.Account.ID == "" {
			return errors.New("router returned an invalid account after creation")
		}
		fmt.Printf("Created account %s\n", created.Account.ID)
		return startDeviceLogin(baseURL, token, created.Account.ID)
	case "login":
		if len(args) != 2 {
			return errors.New("usage: routerctl login <account-id>")
		}
		return startDeviceLogin(baseURL, token, args[1])
	case "enable", "disable":
		if len(args) != 2 {
			return fmt.Errorf("usage: routerctl %s <account-id>", args[0])
		}
		enabled := args[0] == "enable"
		response, err := controlRequest(
			http.MethodPatch,
			baseURL+"/accounts/"+url.PathEscape(args[1]),
			token,
			map[string]bool{"enabled": enabled},
		)
		if err != nil {
			return err
		}
		return printJSON(response)
	default:
		return fmt.Errorf("unknown routerctl command %q", args[0])
	}
}

func startDeviceLogin(baseURL, token, accountID string) error {
	response, err := controlRequest(
		http.MethodPost,
		baseURL+"/accounts/"+url.PathEscape(accountID)+"/login",
		token,
		map[string]string{"mode": "chatgptDeviceCode"},
	)
	if err != nil {
		return err
	}
	return printJSON(response)
}

func controlRequest(method, endpoint, token string, payload any) ([]byte, error) {
	var requestBody io.Reader
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return nil, fmt.Errorf("encode request: %w", err)
		}
		requestBody = bytes.NewReader(encoded)
	}
	request, err := http.NewRequest(method, endpoint, requestBody)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	request.Header.Set("X-Codex-Mux-Token", token)
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	client := &http.Client{Timeout: 35 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("connect to the running router: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return nil, fmt.Errorf("read router response: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("router returned %s: %s", response.Status, strings.TrimSpace(string(body)))
	}
	return body, nil
}

func printJSON(data []byte) error {
	var value any
	if err := json.Unmarshal(data, &value); err != nil {
		return fmt.Errorf("decode router response: %w", err)
	}
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("format router response: %w", err)
	}
	fmt.Println(string(encoded))
	return nil
}

func resolveRealExecutable() (string, error) {
	if configured := os.Getenv("CODEX_MUX_REAL_CODEX"); configured != "" {
		return configured, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolve wrapper executable: %w", err)
	}
	realExecutable := filepath.Join(filepath.Dir(executable), "codex.real")
	if _, err := os.Stat(realExecutable); err != nil {
		return "", fmt.Errorf("find bundled codex.real: %w", err)
	}
	return realExecutable, nil
}

func isInteractiveAppServer(args []string) bool {
	for index, argument := range args {
		if argument != "app-server" {
			continue
		}
		if index+1 < len(args) {
			switch args[index+1] {
			case "daemon", "proxy", "generate-ts", "generate-json-schema", "help":
				return false
			}
		}
		return true
	}
	return false
}

func passthrough(realExecutable string, args []string) error {
	command := exec.Command(realExecutable, args...)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	command.Env = os.Environ()
	if err := command.Run(); err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			os.Exit(exitError.ExitCode())
		}
		return err
	}
	return nil
}

func loadOrCreateToken(root string) (string, error) {
	if configured := os.Getenv("CODEX_MUX_CONTROL_TOKEN"); configured != "" {
		return validateControlToken(configured)
	}
	path := filepath.Join(root, "control-token")
	if data, err := os.ReadFile(path); err == nil {
		token, validateErr := validateControlToken(string(data))
		if validateErr != nil {
			return "", fmt.Errorf("read control token: %w", validateErr)
		}
		if chmodErr := os.Chmod(path, 0o600); chmodErr != nil {
			return "", fmt.Errorf("secure control token: %w", chmodErr)
		}
		return token, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("read control token: %w", err)
	}
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate control token: %w", err)
	}
	token := hex.EncodeToString(bytes)
	if err := os.WriteFile(path, []byte(token), 0o600); err != nil {
		return "", fmt.Errorf("write control token: %w", err)
	}
	return token, nil
}

func validateControlToken(value string) (string, error) {
	token := strings.TrimSpace(value)
	decoded, err := hex.DecodeString(token)
	if err != nil || len(decoded) != 32 {
		return "", errors.New("control token must be exactly 32 random bytes encoded as hexadecimal")
	}
	return token, nil
}
