#!/usr/bin/env bash
# Read-only collector: prints standard system, NVIDIA and PCIe state to stdout.
set -euo pipefail

for command in date uname lspci nvidia-smi sed; do
    command -v "$command" >/dev/null || {
        echo "ERROR: missing command: $command" >&2
        exit 1
    }
done

echo "== timestamp =="
date -u --iso-8601=seconds

echo "== kernel =="
uname -a

echo "== operating system =="
if [[ -r /etc/os-release ]]; then
    sed -n -E '/^(PRETTY_NAME|VERSION_ID|VERSION_CODENAME)=/p' /etc/os-release
fi

echo "== NVIDIA inventory =="
nvidia-smi --query-gpu=index,name,pci.bus_id,driver_version,pstate,persistence_mode,power.draw,temperature.gpu --format=csv

echo "== NVIDIA PCIe state =="
nvidia-smi -q -d PCI

echo "== PCIe devices =="
lspci -Dnn

echo "== NVIDIA PCIe capabilities =="
while IFS= read -r raw_bdf; do
    bdf=${raw_bdf#00000000:}
    bdf="0000:${bdf}"
    echo "-- $bdf --"
    lspci -Dvv -s "$bdf" | sed -n -E '/^[^[:space:]]|LnkCap:|LnkSta:|LnkCap2:|LnkCtl2:|DevSta:|UESta:|CESta:/p'
done < <(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader)
