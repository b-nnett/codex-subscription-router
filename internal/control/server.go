package control

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/mux"
)

type Server struct {
	token   string
	mux     *mux.Multiplexer
	uiTests bool
	http    *http.Server
}

func New(address, token string, multiplexer *mux.Multiplexer, uiTests bool) *Server {
	server := &Server{token: token, mux: multiplexer, uiTests: uiTests}
	router := http.NewServeMux()
	router.HandleFunc("/v1/health", server.health)
	router.HandleFunc("/v1/accounts", server.accounts)
	router.HandleFunc("/v1/accounts/", server.accountAction)
	router.HandleFunc("/v1/thread-account", server.threadAccount)
	router.HandleFunc("/v1/profile/combined", server.combinedProfile)
	router.HandleFunc("/v1/events", server.events)
	if uiTests {
		router.HandleFunc("/v1/test/rate-limits", server.rateLimitPreview)
		router.HandleFunc("/v1/test/rate-limit-resets", server.resetCreditsPreview)
	}
	server.http = &http.Server{
		Addr:              address,
		Handler:           server.securityHeaders(router),
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 * 1024,
	}
	return server
}

func (s *Server) combinedProfile(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	if request.Method != http.MethodGet {
		methodNotAllowed(response)
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
	defer cancel()
	accountID := strings.TrimSpace(request.URL.Query().Get("accountId"))
	var profile mux.CombinedProfile
	var err error
	if accountID == "" {
		profile, err = s.mux.CombinedProfile(ctx)
	} else {
		profile, err = s.mux.AccountProfile(ctx, accountID)
	}
	if err != nil {
		writeJSON(response, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusOK, profile)
}

func (s *Server) resetCreditsPreview(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	if request.Method != http.MethodPost {
		methodNotAllowed(response)
		return
	}
	var preview mux.ResetCreditsPreview
	if err := decodeJSON(request, &preview); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := s.mux.SetResetCreditsPreview(preview); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) rateLimitPreview(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	if request.Method != http.MethodPost {
		methodNotAllowed(response)
		return
	}
	var preview mux.RateLimitPreview
	if err := decodeJSON(request, &preview); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
	defer cancel()
	if err := s.mux.SetRateLimitPreview(ctx, preview); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) threadAccount(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	threadID := strings.TrimSpace(request.URL.Query().Get("threadId"))
	accountID := ""
	if request.Method == http.MethodPost {
		var input struct {
			ThreadID  string `json:"threadId"`
			AccountID string `json:"accountId"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		threadID = strings.TrimSpace(input.ThreadID)
		accountID = strings.TrimSpace(input.AccountID)
	} else if request.Method != http.MethodGet {
		methodNotAllowed(response)
		return
	}
	if threadID == "" {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": "threadId is required"})
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 20*time.Second)
	defer cancel()
	var account mux.AccountSnapshot
	var err error
	if request.Method == http.MethodPost {
		account, err = s.mux.SwitchThreadAccount(ctx, threadID, accountID)
	} else {
		account, err = s.mux.ThreadAccount(ctx, threadID)
	}
	if err != nil {
		status := http.StatusBadRequest
		if request.Method == http.MethodGet {
			status = http.StatusNotFound
		}
		writeJSON(response, status, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"account": account})
}

func (s *Server) Serve(listener net.Listener) error {
	return s.http.Serve(listener)
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.http.Shutdown(ctx)
}

func (s *Server) health(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(response)
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) accounts(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	switch request.Method {
	case http.MethodGet:
		ctx, cancel := context.WithTimeout(request.Context(), 20*time.Second)
		defer cancel()
		writeJSON(response, http.StatusOK, map[string]any{"accounts": s.mux.Accounts(ctx)})
	case http.MethodPost:
		var input struct {
			Label string `json:"label"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), 20*time.Second)
		defer cancel()
		account, err := s.mux.AddAccount(ctx, input.Label)
		if err != nil {
			writeJSON(response, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(response, http.StatusCreated, map[string]any{"account": account})
	default:
		methodNotAllowed(response)
	}
}

func (s *Server) accountAction(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	remainder := strings.TrimPrefix(request.URL.Path, "/v1/accounts/")
	parts := strings.Split(strings.Trim(remainder, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(response, request)
		return
	}
	accountID := parts[0]
	ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
	defer cancel()

	if len(parts) == 1 && request.Method == http.MethodPatch {
		var input struct {
			Label   *string `json:"label"`
			Enabled *bool   `json:"enabled"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		account, err := s.mux.UpdateAccount(ctx, accountID, input.Label, input.Enabled)
		if err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"account": account})
		return
	}
	if len(parts) == 2 && parts[1] == "rate-limit-resets" && request.Method == http.MethodGet {
		result, err := s.mux.RateLimitResetCredits(ctx, accountID)
		if err != nil {
			writeJSON(response, http.StatusBadGateway, map[string]any{"error": err.Error()})
			return
		}
		writeRawJSON(response, http.StatusOK, result)
		return
	}
	if len(parts) == 3 && parts[1] == "rate-limit-resets" && parts[2] == "consume" && request.Method == http.MethodPost {
		var input struct {
			CreditID        *string `json:"creditId"`
			RedeemRequestID string  `json:"redeemRequestId"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		result, err := s.mux.ConsumeRateLimitResetCredit(ctx, accountID, input.CreditID, input.RedeemRequestID)
		if err != nil {
			writeJSON(response, http.StatusBadGateway, map[string]any{"error": err.Error()})
			return
		}
		writeRawJSON(response, http.StatusOK, result)
		return
	}
	if len(parts) != 2 || request.Method != http.MethodPost {
		http.NotFound(response, request)
		return
	}
	switch parts[1] {
	case "login":
		var input struct {
			Mode string `json:"mode"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		result, err := s.mux.StartLogin(ctx, accountID, input.Mode)
		if err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		var login any
		if json.Unmarshal(result, &login) != nil {
			login = map[string]any{}
		}
		writeJSON(response, http.StatusOK, map[string]any{"login": login})
	case "logout":
		if err := s.mux.Logout(ctx, accountID); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"ok": true})
	default:
		http.NotFound(response, request)
	}
}

func (s *Server) events(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(response)
		return
	}
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	flusher, ok := response.(http.Flusher)
	if !ok {
		writeJSON(response, http.StatusInternalServerError, map[string]any{"error": "streaming unavailable"})
		return
	}
	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache")
	response.Header().Set("Connection", "keep-alive")
	events, unsubscribe := s.mux.SubscribeEvents()
	defer unsubscribe()
	_, _ = fmt.Fprint(response, ": connected\n\n")
	flusher.Flush()
	for {
		select {
		case <-request.Context().Done():
			return
		case event, open := <-events:
			if !open {
				return
			}
			encoded, _ := json.Marshal(event)
			_, _ = fmt.Fprintf(response, "data: %s\n\n", encoded)
			flusher.Flush()
		}
	}
}

func (s *Server) authorized(request *http.Request) bool {
	provided := request.Header.Get("X-Codex-Mux-Token")
	if provided == "" {
		provided = request.URL.Query().Get("token")
	}
	return len(provided) == len(s.token) && subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) == 1
}

func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Origin") == "app://-" {
			response.Header().Set("Access-Control-Allow-Origin", "app://-")
			response.Header().Set("Vary", "Origin")
		}
		response.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Codex-Mux-Token")
		response.Header().Set("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
		response.Header().Set("Cache-Control", "no-store")
		response.Header().Set("Referrer-Policy", "no-referrer")
		response.Header().Set("X-Content-Type-Options", "nosniff")
		if request.Method == http.MethodOptions {
			response.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(response, request)
	})
}

func decodeJSON(request *http.Request, target any) error {
	decoder := json.NewDecoder(io.LimitReader(request.Body, 64*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	return nil
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

func writeRawJSON(response http.ResponseWriter, status int, value json.RawMessage) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_, _ = response.Write(value)
}

func methodNotAllowed(response http.ResponseWriter) {
	writeJSON(response, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
}
