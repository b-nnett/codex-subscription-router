package mux

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

type requestLane int

const (
	chatGPTSubscriptionLane requestLane = iota
	externalProviderLane
)

func bypassesChatGPTQuota(params json.RawMessage) bool {
	lane, explicit := classifyParamsLane(params)
	return explicit && lane == externalProviderLane
}

func accountBypassesChatGPTQuota(params json.RawMessage, account state.Account) bool {
	return classifyRequestLane(params, account) == externalProviderLane
}

func classifyRequestLane(params json.RawMessage, account state.Account) requestLane {
	if lane, explicit := classifyParamsLane(params); explicit {
		return lane
	}
	return classifyCodexHomeLane(account.CodexHome)
}

func classifyParamsLane(params json.RawMessage) (requestLane, bool) {
	if len(params) == 0 {
		return chatGPTSubscriptionLane, false
	}
	var decoded struct {
		Model         string `json:"model"`
		ModelProvider string `json:"modelProvider"`
	}
	if json.Unmarshal(params, &decoded) != nil {
		return chatGPTSubscriptionLane, false
	}
	if provider := strings.TrimSpace(decoded.ModelProvider); provider != "" {
		return laneForProvider(provider), true
	}
	return laneForModel(decoded.Model)
}

func classifyCodexHomeLane(codexHome string) requestLane {
	config, err := os.ReadFile(filepath.Join(codexHome, "config.toml"))
	if err != nil {
		return chatGPTSubscriptionLane
	}
	if provider := state.TopLevelConfigValue(config, "model_provider"); strings.TrimSpace(provider) != "" {
		if lane := laneForProvider(provider); lane == externalProviderLane {
			return lane
		}
	}
	if baseURL := state.TopLevelConfigValue(config, "openai_base_url"); strings.TrimSpace(baseURL) != "" && !isOfficialOpenAIBaseURL(baseURL) {
		return externalProviderLane
	}
	if lane, explicit := laneForModel(state.TopLevelConfigValue(config, "model")); explicit {
		return lane
	}
	return chatGPTSubscriptionLane
}

func laneForProvider(provider string) requestLane {
	if isOfficialProvider(provider) {
		return chatGPTSubscriptionLane
	}
	return externalProviderLane
}

func laneForModel(model string) (requestLane, bool) {
	model = strings.TrimSpace(model)
	if model == "" {
		return chatGPTSubscriptionLane, false
	}
	provider, _, hasProvider := strings.Cut(model, "/")
	if hasProvider && strings.TrimSpace(provider) != "" {
		return laneForProvider(provider), true
	}
	if isOfficialModel(model) {
		return chatGPTSubscriptionLane, true
	}
	return chatGPTSubscriptionLane, false
}

func isOfficialProvider(provider string) bool {
	switch strings.ToLower(strings.TrimSpace(provider)) {
	case "", "openai", "chatgpt", "codex":
		return true
	default:
		return false
	}
}

func isOfficialModel(model string) bool {
	lower := strings.ToLower(strings.TrimSpace(model))
	if lower == "gpt" || strings.HasPrefix(lower, "gpt-") {
		return true
	}
	if lower == "codex" || strings.HasPrefix(lower, "codex-") {
		return true
	}
	if lower == "chatgpt" || strings.HasPrefix(lower, "chatgpt-") {
		return true
	}
	return len(lower) >= 2 && lower[0] == 'o' && lower[1] >= '0' && lower[1] <= '9'
}

func isOfficialOpenAIBaseURL(value string) bool {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil {
		return true
	}
	host := strings.ToLower(parsed.Hostname())
	return host == "" ||
		host == "openai.com" ||
		host == "chatgpt.com" ||
		strings.HasSuffix(host, ".openai.com") ||
		strings.HasSuffix(host, ".chatgpt.com")
}
