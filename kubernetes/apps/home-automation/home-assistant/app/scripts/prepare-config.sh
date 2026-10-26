#!/usr/bin/env bash
# Prepares a Home Assistant config directory: makes sure the UI-owned include
# files exist, and installs the pinned custom integrations and frontend cards.
#
# Runs as the home-assistant init container against the /config PVC, and in
# .github/workflows/home-assistant.yaml against a scratch copy of config/ so
# check_config sees the same integrations. Versions come from the environment
# (pinned, with Renovate comments, in helmrelease.yaml):
#   HASS_OIDC_AUTH_VERSION        christiaangoossens/hass-oidc-auth
#   FRIGATE_INTEGRATION_VERSION   blakeblackshear/frigate-hass-integration
#   ADVANCED_CAMERA_CARD_VERSION  dermotduffy/advanced-camera-card
#
# Each install records its version in a .tnwks-version file and is skipped
# when that already matches, so a plain restart needs no network.
set -euo pipefail

config="${1:-/config}"

# The UI automation/script/scene editors write these; configuration.yaml
# includes them, so they must exist (as empty lists/maps) on a fresh volume.
[[ -e "${config}/automations.yaml" ]] || echo "[]" >"${config}/automations.yaml"
[[ -e "${config}/scripts.yaml" ]] || echo "{}" >"${config}/scripts.yaml"
[[ -e "${config}/scenes.yaml" ]] || echo "[]" >"${config}/scenes.yaml"

# install <name> <version> <destination dir> <url> <subpath in archive>
# Downloads <url> (.zip or .tar.gz), and replaces <destination dir> with the
# archive's <subpath> ("." for the archive root).
install() {
    local name="$1" version="$2" dest="$3" url="$4" subpath="$5"
    if [[ "$(cat "${dest}/.tnwks-version" 2>/dev/null)" == "${version}" ]]; then
        echo "${name} ${version}: already installed"
        return
    fi
    echo "${name} ${version}: installing from ${url}"
    local work
    work="$(mktemp -d)"
    curl -fsSL --retry 3 --retry-all-errors -o "${work}/archive" "${url}"
    mkdir "${work}/x"
    case "${url}" in
        *.zip) unzip -q "${work}/archive" -d "${work}/x" ;;
        *.tar.gz) tar -xzf "${work}/archive" -C "${work}/x" ;;
        *) echo "unsupported archive: ${url}" >&2; return 1 ;;
    esac
    # GitHub source tarballs wrap everything in <repo>-<version>/.
    local root="${work}/x"
    if [[ "${url}" == */archive/refs/tags/* ]]; then
        root="$(find "${work}/x" -mindepth 1 -maxdepth 1 -type d)"
    fi
    [[ -d "${root}/${subpath}" ]] || { echo "${name}: ${subpath} not in archive" >&2; return 1; }
    rm -rf "${dest}"
    mkdir -p "$(dirname "${dest}")"
    cp -r "${root}/${subpath}" "${dest}"
    echo "${version}" >"${dest}/.tnwks-version"
    rm -rf "${work}"
}

install auth_oidc "${HASS_OIDC_AUTH_VERSION}" \
    "${config}/custom_components/auth_oidc" \
    "https://github.com/christiaangoossens/hass-oidc-auth/releases/download/${HASS_OIDC_AUTH_VERSION}/hass-oidc-auth.zip" \
    .

install frigate "${FRIGATE_INTEGRATION_VERSION}" \
    "${config}/custom_components/frigate" \
    "https://github.com/blakeblackshear/frigate-hass-integration/archive/refs/tags/${FRIGATE_INTEGRATION_VERSION}.tar.gz" \
    custom_components/frigate

install advanced-camera-card "${ADVANCED_CAMERA_CARD_VERSION}" \
    "${config}/www/advanced-camera-card" \
    "https://github.com/dermotduffy/advanced-camera-card/releases/download/${ADVANCED_CAMERA_CARD_VERSION}/advanced-camera-card.zip" \
    dist
