#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $EUID == 0 && $# == 2 && $1 =~ ^[0-9]+$ ]] || {
    echo "Usage: sudo ./install-pcie-host.sh VMID mapping.json" >&2; exit 2;
}
command -v qm >/dev/null
command -v python3 >/dev/null
VMID=$1
CONFIG=$(realpath -- "$2")
python3 - "$ROOT/scripts/cmp100-pcie-host" "$CONFIG" "$VMID" <<'PY'
import json, runpy, sys
module = runpy.run_path(sys.argv[1], run_name="cmp100_host_validation")
module["validate_config"](json.load(open(sys.argv[2])), int(sys.argv[3]))
PY
install -d -m 0755 /etc/cmp100-pcie
install -d -m 0700 /var/lib/cmp100-pcie-host
install -m 0755 "$ROOT/scripts/cmp100-pcie-host" /usr/local/sbin/cmp100-pcie-host
if [[ $CONFIG != "/etc/cmp100-pcie/$VMID.json" ]]; then
    if [[ -e /etc/cmp100-pcie/$VMID.json ]]; then
        cp -a "/etc/cmp100-pcie/$VMID.json" "/etc/cmp100-pcie/$VMID.json.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    install -m 0600 "$CONFIG" "/etc/cmp100-pcie/$VMID.json"
fi
install -m 0644 "$ROOT/systemd/cmp100-pcie-host@.service" "$ROOT/systemd/cmp100-pcie-host@.timer" /etc/systemd/system/
systemctl daemon-reload
echo "Installed. First validate with: /usr/local/sbin/cmp100-pcie-host $VMID --check"
echo "Apply once with: systemctl start cmp100-pcie-host@$VMID.service"
echo "After validation enable: systemctl enable --now cmp100-pcie-host@$VMID.timer"
