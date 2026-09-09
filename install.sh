#!/bin/bash

set -euo pipefail

readonly DEFAULT_REPOSITORY_URL="https://github.com/nurasss/codex-subscription-router.git"
# Fork publishers: point this default at your fork so one-command installs and
# source updates track the fork. It can also be overridden per-run via the
# CODEX_SUBSCRIPTION_ROUTER_REPOSITORY_URL environment variable.
readonly REPOSITORY_URL="${CODEX_SUBSCRIPTION_ROUTER_REPOSITORY_URL:-${DEFAULT_REPOSITORY_URL}}"
readonly DEFAULT_SOURCE_DIR="${HOME}/.codex-subscription-router/source"
readonly SOURCE_DIR="${CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR:-${DEFAULT_SOURCE_DIR}}"
readonly DESTINATION_APP="${HOME}/Applications/Codex Subscription Router.app"
readonly DESTINATION_HELPER="${HOME}/Applications/Codex Subscription Router Computer Use.app"

log() {
    printf '\n==> %s\n' "$1" >&2
}

fail() {
    printf '\nInstall failed: %s\n' "$1" >&2
    exit 1
}

require_prerequisites() {
    if [ "$(uname -s)" != "Darwin" ]; then
        fail "Codex Subscription Router supports macOS only."
    fi
    local machine_arch
    machine_arch="$(uname -m)"
    case "${machine_arch}" in
        arm64|x86_64)
            ;;
        *)
            fail "unsupported Mac architecture '${machine_arch}'. Codex Subscription Router supports arm64 and x86_64."
            ;;
    esac
    printf 'Detected architecture: %s\n' "${machine_arch}" >&2
    if [ ! -d "/Applications/ChatGPT.app" ]; then
        fail "install the official ChatGPT app in /Applications first."
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

# Enforce the capability-aware macOS floor reported by the patcher:
# tested Intel Core sources declare deployment targets down to macOS 13.5,
# while untested Intel sources and Full builds stay at 14.0. Apple-silicon
# keeps the historical upstream policy (no installer floor).
require_macos_for_plan() {
    local plan
    plan="$(python3 scripts/patch_app.py --print-install-plan)" || fail "could not inspect the official ChatGPT app."
    local plan_value
    plan_value() {
        printf '%s\n' "${plan}" | sed -n "s/^${1}=//p"
    }
    local architecture capability minimum source_version source_build
    architecture="$(plan_value architecture)"
    capability="$(plan_value capability)"
    minimum="$(plan_value minimum_macos)"
    source_version="$(plan_value source_version)"
    source_build="$(plan_value source_build)"
    local mode
    case "${capability}" in
        core) mode="Core" ;;
        full) mode="Full" ;;
        *) mode="Untested" ;;
    esac
    local macos_version
    macos_version="$(sw_vers -productVersion)"
    printf 'Architecture: %s\nMode: %s\nmacOS: %s\nMinimum supported: %s\nSource: ChatGPT %s (%s)\n' \
        "${architecture}" "${mode}" "${macos_version}" "${minimum}" \
        "${source_version}" "${source_build}" >&2
    if [ "${minimum}" = "none" ] || [ "${minimum}" = "unknown" ]; then
        return
    fi
    local minimum_major="${minimum%%.*}"
    local minimum_minor="${minimum#*.}"
    minimum_minor="${minimum_minor%%.*}"
    local macos_major="${macos_version%%.*}"
    local macos_rest="${macos_version#*.}"
    local macos_minor="${macos_rest%%.*}"
    if [ "${macos_major}" -lt "${minimum_major}" ] || { [ "${macos_major}" -eq "${minimum_major}" ] && [ "${macos_minor}" -lt "${minimum_minor}" ]; }; then
        fail "this ${mode} build requires macOS ${minimum} or newer; found ${macos_version}."
    fi
}

stop_bundle_processes() {    local bundle_path="$1"
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
    log "Checking this Mac"
    require_prerequisites

    local project_dir
    project_dir="$(resolve_source_dir)"
    cd "${project_dir}"

    log "Checking macOS compatibility"
    require_macos_for_plan

    log "Installing locked build tools"
    npm ci --ignore-scripts --no-audit --no-fund

    local patch_arguments=()
    if [ -d "${DESTINATION_APP}" ] || [ -d "${DESTINATION_HELPER}" ]; then
        log "Stopping the existing installation"
        stop_bundle_processes "${DESTINATION_APP}"
        stop_bundle_processes "${DESTINATION_HELPER}"
        patch_arguments+=("--force")
    fi

    log "Building and signing Codex Subscription Router"
    # The ${array[@]+"${array[@]}"} form keeps `set -u` working with empty
    # arrays on the macOS system bash (3.2).
    python3 scripts/patch_app.py ${patch_arguments[@]+"${patch_arguments[@]}"}

    log "Launching Codex Subscription Router"
    open "${DESTINATION_APP}"
    printf '\nInstalled successfully: %s\n' "${DESTINATION_APP}"
}

main "$@"
