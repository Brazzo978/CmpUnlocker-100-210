#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
STATE_DIR=/var/lib/cmp100-unlocker
LIB_DIR=/usr/lib/cmp100-unlocker
FW_FECS=/lib/firmware/nvidia/gv100/gr/fecs_sig.bin
FW_BL=/lib/firmware/nvidia/gv100/acr/bl.bin
FW_UCODE=/lib/firmware/nvidia/gv100/acr/ucode_load.bin
TMPDIR_CMP=$(mktemp -d)

cleanup() { rm -rf -- "$TMPDIR_CMP"; }
trap cleanup EXIT

[[ $EUID -eq 0 ]] || { echo 'Run install.sh as root.' >&2; exit 1; }
for command in python3 make sha256sum install systemctl; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
[[ -d /lib/modules/$(uname -r)/build ]] || {
    echo "Missing kernel headers for $(uname -r)." >&2
    exit 1
}
for firmware in "$FW_FECS" "$FW_BL" "$FW_UCODE"; do
    [[ -f $firmware ]] || { echo "Missing firmware: $firmware" >&2; exit 1; }
done

install -d -m 0755 "$STATE_DIR/stock" "$LIB_DIR/payloads"
if [[ ! -f $STATE_DIR/stock/fecs_sig.bin ]]; then
    install -m 0644 "$FW_FECS" "$STATE_DIR/stock/fecs_sig.bin"
    install -m 0644 "$FW_BL" "$STATE_DIR/stock/bl.bin"
    install -m 0644 "$FW_UCODE" "$STATE_DIR/stock/ucode_load.bin"
fi

python3 "$ROOT/tools/build_payloads.py" \
    --fecs "$STATE_DIR/stock/fecs_sig.bin" \
    --bl "$STATE_DIR/stock/bl.bin" \
    --ucode "$STATE_DIR/stock/ucode_load.bin" \
    --output "$TMPDIR_CMP/payloads"

make -C "$ROOT/src" clean
make -C "$ROOT/src"

install -m 0644 "$TMPDIR_CMP/payloads/fecs_sig.candidate-c-504.bin" "$LIB_DIR/payloads/"
install -m 0644 "$TMPDIR_CMP/payloads/bl.load-candidate-c.bin" "$LIB_DIR/payloads/"
install -m 0644 "$TMPDIR_CMP/payloads/ucode_load.tensor-success-lsb-restore.bin" "$LIB_DIR/payloads/"
install -m 0644 "$ROOT/src/gv100_nouveau_acr_hook.ko" "$LIB_DIR/"
install -m 0755 "$ROOT/scripts/cmp100-tensor-unlock" /usr/local/sbin/
install -m 0644 "$ROOT/systemd/cmp100-tensor-unlock.service" /etc/systemd/system/

cat > "$STATE_DIR/manifest.env" <<EOF
STOCK_FECS_SHA=$(sha256sum "$STATE_DIR/stock/fecs_sig.bin" | awk '{print $1}')
STOCK_BL_SHA=$(sha256sum "$STATE_DIR/stock/bl.bin" | awk '{print $1}')
STOCK_UCODE_SHA=$(sha256sum "$STATE_DIR/stock/ucode_load.bin" | awk '{print $1}')
CUSTOM_FECS_SHA=$(sha256sum "$LIB_DIR/payloads/fecs_sig.candidate-c-504.bin" | awk '{print $1}')
CUSTOM_BL_SHA=$(sha256sum "$LIB_DIR/payloads/bl.load-candidate-c.bin" | awk '{print $1}')
CUSTOM_UCODE_SHA=$(sha256sum "$LIB_DIR/payloads/ucode_load.tensor-success-lsb-restore.bin" | awk '{print $1}')
HOOK_SHA=$(sha256sum "$LIB_DIR/gv100_nouveau_acr_hook.ko" | awk '{print $1}')
EOF
chmod 0600 "$STATE_DIR/manifest.env"

if [[ ! -e /etc/default/cmp100-unlocker ]]; then
    cat > /etc/default/cmp100-unlocker <<'EOF'
# Optional space-separated BDF allow-list. Empty means auto-detect 10de:1d84.
# CMP100_BDFS="0000:01:00.0 0000:02:00.0"

# Optional workload services to stop before driver handoff.
# CMP100_STOP_SERVICES="my-inference.service my-monitor.service"
EOF
fi

systemctl daemon-reload

echo
echo 'Install complete. Stop every process using the CMP100 GPUs, then run:'
echo '  sudo systemctl start cmp100-tensor-unlock.service'
echo '  sudo journalctl -u cmp100-tensor-unlock.service -b --no-pager'
echo
echo 'Enable at boot only after the manual run passes:'
echo '  sudo systemctl enable cmp100-tensor-unlock.service'
