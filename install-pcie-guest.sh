#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Install, but do not enable or execute, the volatile CMP100 Gen2 helper.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
STATE_DIR=/var/lib/cmp100-pcie
LIB_DIR=/usr/lib/cmp100-pcie
FW_FECS=/lib/firmware/nvidia/gv100/gr/fecs_sig.bin
FW_BL=/lib/firmware/nvidia/gv100/acr/bl.bin
FW_UCODE=/lib/firmware/nvidia/gv100/acr/ucode_load.bin
TMPDIR_CMP=$(mktemp -d)

cleanup() { rm -rf -- "$TMPDIR_CMP"; }
trap cleanup EXIT

[[ $EUID -eq 0 ]] || { echo 'Run install-pcie-guest.sh as root.' >&2; exit 1; }
for command in python3 make sha256sum install systemctl modinfo flock busybox; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
[[ -d /lib/modules/$(uname -r)/build ]] || {
    echo "Missing kernel headers for $(uname -r)." >&2
    exit 1
}
for firmware in "$FW_FECS" "$FW_BL" "$FW_UCODE"; do
    [[ -f $firmware ]] || { echo "Missing firmware: $firmware" >&2; exit 1; }
done

# This state is deliberately separate from /var/lib/cmp100-unlocker.  It takes
# a checked copy of the currently installed originals, but never changes the
# active firmware during installation.
install -d -o root -g root -m 0755 "$STATE_DIR/stock" "$LIB_DIR/payloads"
if [[ ! -f $STATE_DIR/stock/fecs_sig.bin ]]; then
    install -o root -g root -m 0644 "$FW_FECS" "$STATE_DIR/stock/fecs_sig.bin"
    install -o root -g root -m 0644 "$FW_BL" "$STATE_DIR/stock/bl.bin"
    install -o root -g root -m 0644 "$FW_UCODE" "$STATE_DIR/stock/ucode_load.bin"
fi

python3 "$ROOT/tools/build_payloads.py" \
    --fecs "$STATE_DIR/stock/fecs_sig.bin" \
    --bl "$STATE_DIR/stock/bl.bin" \
    --ucode "$STATE_DIR/stock/ucode_load.bin" \
    --output "$TMPDIR_CMP/payloads"

# Build the same source hook used by the Tensor helper, for this running
# kernel.  The copy below belongs only to the PCIe helper's isolated libdir.
make -C "$ROOT/src"
modinfo -F vermagic "$ROOT/src/gv100_nouveau_acr_hook.ko" | grep -Fq "$(uname -r)" || {
    echo 'Built hook vermagic does not match the running kernel.' >&2
    exit 1
}

install -o root -g root -m 0644 "$TMPDIR_CMP/payloads/fecs_sig.candidate-c-504.bin" "$LIB_DIR/payloads/"
install -o root -g root -m 0644 "$TMPDIR_CMP/payloads/bl.load-candidate-c.bin" "$LIB_DIR/payloads/"
install -o root -g root -m 0644 "$TMPDIR_CMP/payloads/ucode_load.pcie-policy-88610-1.bin" "$LIB_DIR/payloads/"
install -o root -g root -m 0644 "$TMPDIR_CMP/payloads/ucode_load.pcie-vector-8872c-6.bin" "$LIB_DIR/payloads/"
install -o root -g root -m 0644 "$ROOT/src/gv100_nouveau_acr_hook.ko" "$LIB_DIR/"
install -o root -g root -m 0755 "$ROOT/scripts/cmp100-pcie-gen2" /usr/local/sbin/
install -o root -g root -m 0644 "$ROOT/systemd/cmp100-pcie-gen2.service" /etc/systemd/system/

cat > "$STATE_DIR/manifest.env" <<EOF
STOCK_FECS_SHA=$(sha256sum "$STATE_DIR/stock/fecs_sig.bin" | awk '{print $1}')
STOCK_BL_SHA=$(sha256sum "$STATE_DIR/stock/bl.bin" | awk '{print $1}')
STOCK_UCODE_SHA=$(sha256sum "$STATE_DIR/stock/ucode_load.bin" | awk '{print $1}')
CUSTOM_FECS_SHA=$(sha256sum "$LIB_DIR/payloads/fecs_sig.candidate-c-504.bin" | awk '{print $1}')
CUSTOM_BL_SHA=$(sha256sum "$LIB_DIR/payloads/bl.load-candidate-c.bin" | awk '{print $1}')
PCIE_POLICY_UCODE_SHA=$(sha256sum "$LIB_DIR/payloads/ucode_load.pcie-policy-88610-1.bin" | awk '{print $1}')
PCIE_VECTOR_UCODE_SHA=$(sha256sum "$LIB_DIR/payloads/ucode_load.pcie-vector-8872c-6.bin" | awk '{print $1}')
HOOK_SHA=$(sha256sum "$LIB_DIR/gv100_nouveau_acr_hook.ko" | awk '{print $1}')
EOF
chown root:root "$STATE_DIR/manifest.env"
chmod 0600 "$STATE_DIR/manifest.env"

if [[ ! -e /etc/default/cmp100-pcie ]]; then
    cat > /etc/default/cmp100-pcie <<'EOF'
# Required explicit space-separated BDF allow-list (or provided by the host).
# CMP100_PCIE_BDFS="0000:01:00.0 0000:02:00.0"

# Optional workload units to stop before the transient driver handoff.
# They are restarted asynchronously only when they were active before the run.
# CMP100_PCIE_STOP_SERVICES="my-inference.service my-monitor.service"
EOF
    chown root:root /etc/default/cmp100-pcie
    chmod 0644 /etc/default/cmp100-pcie
fi

systemctl daemon-reload

echo
echo 'PCIe Gen2 helper installed but not enabled or started.'
echo 'After stopping GPU workloads, run:'
echo '  sudo systemctl start cmp100-pcie-gen2.service'
echo '  sudo journalctl -u cmp100-pcie-gen2.service -b --no-pager'
echo
echo 'Enable only after a successful manual run:'
echo '  sudo systemctl enable cmp100-pcie-gen2.service'
