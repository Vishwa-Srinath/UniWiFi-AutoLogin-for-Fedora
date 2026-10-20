#!/usr/bin/env bash
# UniWiFi Reconnaissance Tool for Fedora
# Run this script while connected to your university Wi-Fi to diagnose
# network type (Type A vs Type B) and capture captive portal parameters.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "========================================================"
echo "    UniWiFi AutoLogin: Campus Network Reconnaissance    "
echo "========================================================"

if ! command -v nmcli &>/dev/null; then
    echo "[!] Error: NetworkManager CLI (nmcli) is not installed."
    exit 1
fi

echo -e "\n[1/3] Inspecting active Wi-Fi connection via nmcli..."
ACTIVE_WIFI=$(nmcli -t -f TYPE,NAME,DEVICE,UUID connection show --active | grep "^802-11-wireless:" | head -n 1 || true)

if [ -z "$ACTIVE_WIFI" ]; then
    echo "[-] No active Wi-Fi connection found."
    echo "    Connect to your campus Wi-Fi network first, then re-run this script."
    exit 0
fi

IFS=':' read -r _ SSID DEVICE UUID <<< "$ACTIVE_WIFI"
echo "    SSID:         $SSID"
echo "    Device:       $DEVICE"
echo "    UUID:         $UUID"

KEY_MGMT=$(nmcli -s connection show "$UUID" | grep -E "key-mgmt:" | awk '{print $2}' || true)
FLAGS=$(nmcli -s connection show "$UUID" | grep -E "802-1x.password-flags:" | awk '{print $2}' || true)

echo "    Security:     ${KEY_MGMT:-none}"

if [[ "$KEY_MGMT" == "wpa-eap" || "$KEY_MGMT" == "802-1x" ]]; then
    echo -e "\n[*] CLASSIFICATION: Type A (802.1X / WPA-Enterprise)"
    echo "    Your network authenticates inside Wi-Fi association (before IP assignment)."
    echo "    You likely DO NOT need a background captive portal daemon!"
    echo "    Try configuring NetworkManager to store credentials natively:"
    echo ""
    echo "    nmcli connection modify \"$SSID\" \\"
    echo "      802-1x.identity \"your_username\" \\"
    echo "      802-1x.password \"your_password\" \\"
    echo "      802-1x.password-flags 0 \\"
    echo "      connection.autoconnect yes"
    echo "    nmcli connection up \"$SSID\""
    echo ""
    echo "    If this keeps you connected across sleep/wake, you are all done!"
    exit 0
fi

echo -e "\n[*] CLASSIFICATION: Type B (Web Captive Portal)"
echo "    Your network requires web browser form authentication."

PROBE_URL="${1:-http://fedoraproject.org/static/hotspot.txt}"
echo -e "\n[2/3] Probing captive portal endpoint: $PROBE_URL..."

DUMP_FILE="/tmp/uniwifi_portal_dump.html"
HTTP_CODE=$(curl -s -L -w "%{http_code}" -o "$DUMP_FILE" "$PROBE_URL" || true)

echo "    Probe HTTP Status: $HTTP_CODE"
echo "    Saved Landing HTML to: $DUMP_FILE"

echo -e "\n[3/3] Running Python form analyzer..."
if [ -f ".venv/bin/uniwifi" ]; then
    .venv/bin/uniwifi recon "$SSID"
else
    python3 -m uniwifi recon "$SSID" || true
fi

echo -e "\n========================================================"
echo "Reconnaissance complete! See docs/recon_notes.md for guide."
echo "========================================================"
