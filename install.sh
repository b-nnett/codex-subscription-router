#!/bin/bash

set -euo pipefail

readonly REPOSITORY_URL="https://github.com/b-nnett/codex-subscription-router.git"
readonly DEFAULT_SOURCE_DIR="${HOME}/.codex-subscription-router/source"
readonly SOURCE_DIR="${CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR:-${DEFAULT_SOURCE_DIR}}"
readonly DESTINATION_APP="${HOME}/Applications/Codex Subscription Router.app"
readonly DESTINATION_HELPER="${HOME}/Applications/Codex Subscription Router Computer Use.app"
readonly CONTROL_CLI="${HOME}/.local/bin/codex-subscription-router"

resolve_source_app() {
    if [ -n "${CODEX_SUBSCRIPTION_ROUTER_SOURCE_APP:-}" ]; then
        printf '%s\n' "${CODEX_SUBSCRIPTION_ROUTER_SOURCE_APP}"
    elif [ -d "/Applications/ChatGPT.app" ]; then
        printf '%s\n' "/Applications/ChatGPT.app"
    elif [ -d "/Applications/Codex.app" ]; then
        printf '%s\n' "/Applications/Codex.app"
    else
        fail "install the official ChatGPT or Codex app in /Applications first."
    fi
}

log() {
    printf '\n==> %s\n' "$1" >&2
}

fail() {
    printf '\nInstall failed: %s\n' "$1" >&2
    exit 1
}

require_prerequisites() {
    local source_app="$1"
    if [ "$(uname -s)" != "Darwin" ]; then
        fail "Codex Subscription Router supports macOS only."
    fi
    if [ "$(uname -m)" != "arm64" ]; then
        fail "Codex Subscription Router currently requires Apple silicon."
    fi
    if [ ! -f "${source_app}/Contents/Resources/app.asar" ]; then
        fail "${source_app} is not a supported ChatGPT or Codex app bundle."
    fi

    local missing=()
    local command_name
    for command_name in git go node npm python3 security xcrun; do
        if ! command -v "${command_name}" >/dev/null 2>&1; then
            missing+=("${command_name}")
        fi
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        fail "missing prerequisites: ${missing[*]}. Install Xcode Command Line Tools, Go 1.26+, and Node.js 22.12+, then rerun this command."
    fi

    local node_major
    local node_minor
    node_major="$(node -p 'process.versions.node.split(".")[0]')"
    node_minor="$(node -p 'process.versions.node.split(".")[1]')"
    if [ "${node_major}" -lt 22 ] || { [ "${node_major}" -eq 22 ] && [ "${node_minor}" -lt 12 ]; }; then
        fail "Node.js 22.12 or newer is required; found $(node --version)."
    fi

    local go_version
    local go_major
    local go_minor
    go_version="$(go env GOVERSION | sed 's/^go//')"
    go_major="${go_version%%.*}"
    go_minor="${go_version#*.}"
    go_minor="${go_minor%%.*}"
    if [ "${go_major}" -lt 1 ] || { [ "${go_major}" -eq 1 ] && [ "${go_minor}" -lt 26 ]; }; then
        fail "Go 1.26 or newer is required; found go${go_version}."
    fi
}

resolve_source_dir() {
    local script_source="${BASH_SOURCE[0]:-}"
    local script_dir=""
    if [ -n "${script_source}" ] && [ -f "${script_source}" ]; then
        script_dir="$(CDPATH= cd -- "$(dirname -- "${script_source}")" && pwd)"
    fi
    if [ -n "${script_dir}" ] && [ -f "${script_dir}/scripts/patch_app.py" ]; then
        printf '%s\n' "${script_dir}"
        return
    fi

    if [ -d "${SOURCE_DIR}/.git" ]; then
        if [ -n "$(git -C "${SOURCE_DIR}" status --porcelain)" ]; then
            fail "${SOURCE_DIR} has local changes; preserve or commit them before updating."
        fi
        if [ "$(git -C "${SOURCE_DIR}" branch --show-current)" != "main" ]; then
            fail "${SOURCE_DIR} is not on main; switch branches or set CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR."
        fi
        log "Updating source"
        git -C "${SOURCE_DIR}" pull --ff-only origin main >&2
    elif [ -e "${SOURCE_DIR}" ]; then
        fail "${SOURCE_DIR} exists but is not a Git repository."
    else
        log "Downloading source"
        mkdir -p "$(dirname -- "${SOURCE_DIR}")"
        git clone --depth 1 --branch main "${REPOSITORY_URL}" "${SOURCE_DIR}" >&2
    fi
    printf '%s\n' "${SOURCE_DIR}"
}

stop_bundle_processes() {
    local bundle_path="$1"
    local process_id
    local command_line
    local attempt

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        local found_process="false"
        for process_id in $(pgrep -f "${bundle_path}/Contents/" 2>/dev/null || true); do
            command_line="$(ps -p "${process_id}" -o command= 2>/dev/null || true)"
            case "${command_line}" in
                "${bundle_path}/Contents/"*)
                    found_process="true"
                    kill "${process_id}" 2>/dev/null || true
                    ;;
            esac
        done
        if [ "${found_process}" = "false" ]; then
            return
        fi
        sleep 1
    done
    fail "could not stop processes belonging to ${bundle_path}."
}

main() {
    local source_app
    source_app="$(resolve_source_app)"
    log "Checking this Mac"
    require_prerequisites "${source_app}"

    local project_dir
    project_dir="$(resolve_source_dir)"
    cd "${project_dir}"

    log "Installing locked build tools"
    npm ci --ignore-scripts --no-audit --no-fund

    local patch_arguments=("--source" "${source_app}")
    if [ "${CODEX_MUX_ALLOW_ADHOC_SIGNING:-0}" = "1" ]; then
        patch_arguments+=("--allow-adhoc-signing")
    fi
    if [ -d "${DESTINATION_APP}" ] || [ -d "${DESTINATION_HELPER}" ]; then
        log "Stopping the existing installation"
        stop_bundle_processes "${DESTINATION_APP}"
        stop_bundle_processes "${DESTINATION_HELPER}"
        patch_arguments+=("--force")
    fi

    log "Building and signing Codex Subscription Router"
    python3 scripts/patch_app.py "${patch_arguments[@]}"

    if [ -e "${CONTROL_CLI}" ] && [ ! -L "${CONTROL_CLI}" ]; then
        fail "${CONTROL_CLI} already exists and is not a symlink."
    fi
    mkdir -p "$(dirname -- "${CONTROL_CLI}")"
    ln -sfn "${DESTINATION_APP}/Contents/Resources/codex" "${CONTROL_CLI}"

    log "Launching Codex Subscription Router"
    open "${DESTINATION_APP}"
    printf '\nInstalled successfully: %s\n' "${DESTINATION_APP}"
}

main "$@"
