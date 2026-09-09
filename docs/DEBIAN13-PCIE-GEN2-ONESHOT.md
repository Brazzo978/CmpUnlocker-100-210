# Debian 13: one-time PCIe Gen2 unlock

This guide applies the tested volatile PCIe Gen2 policy and performs one
physical retrain. It does not enable boot automation. The tested cards reached
**Gen2 x1**; this method does not claim Gen3 or a wider link.

Complete the [one-time Tensor guide](DEBIAN13-TENSOR-ONESHOT.md) first. The
Gen2 path uses the same Debian 13 kernel, proprietary NVIDIA `550.163.01`
driver, exact GV100 firmware and signed-write helper.

The operation briefly detaches the NVIDIA driver and retrains the link. Use a
local, out-of-band, VM or hypervisor console. Stop every workload first.

## 1. Record the starting state

```bash
nvidia-smi --query-gpu=index,pci.bus_id,name,driver_version,pcie.link.gen.current,pcie.link.gen.max --format=csv
lspci -Dnn | grep -i '10de:1d84'
sudo lspci -vv -s 0000:01:00.0 | grep -E 'LnkCap:|LnkSta:'
```

Replace `0000:01:00.0` with the full BDF from your machine. On an untouched
CMP100-210 the NVIDIA query normally reports generation 1.

## 2. Install the Gen2 helper

From the same repository checkout used for Tensor:

```bash
cd CmpUnlocker-100-210
sudo apt install -y build-essential linux-headers-$(uname -r) python3 busybox kmod pciutils psmisc
sudo bash ./install-pcie-guest.sh
```

Installation does not start or enable the operation.

## 3. Set an explicit card allow-list

Gen2 intentionally requires explicit BDFs. Copy the addresses from
`lspci -Dnn`; do not reuse the examples blindly:

```bash
sudo tee /etc/default/cmp100-pcie >/dev/null <<'EOF'
CMP100_PCIE_BDFS="0000:01:00.0 0000:02:00.0"
EOF
```

For an application managed by systemd, the helper can stop and later restart
specified services:

```text
CMP100_PCIE_STOP_SERVICES="my-inference.service"
```

It is safer on the first run to stop workloads yourself and verify that no
process holds NVIDIA devices:

```bash
nvidia-smi
sudo fuser -v /dev/nvidia* 2>/dev/null || true
```

Stop Docker, inference and monitoring processes if they appear. Leave
`nvidia-persistenced` running if it was active: the helper records, stops and
restores it itself. The helper refuses to continue while a CUDA client remains.

## 4. Apply Gen2 once

```bash
sudo systemctl start cmp100-pcie-gen2.service
sudo journalctl -u cmp100-pcie-gen2.service -b --no-pager
```

Require the final message:

```text
PASS; cleanup completed
```

A successful signed-write message alone is not enough: the final PASS also
means the driver was restored and the reported link state passed validation.

Do **not** run `systemctl enable cmp100-pcie-gen2.service` for a one-time setup.

## 5. Verify both the driver and PCIe state

```bash
nvidia-smi --query-gpu=index,pci.bus_id,name,driver_version,pcie.link.gen.current,pcie.link.gen.max --format=csv
sudo lspci -vv -s 0000:01:00.0 | grep -E 'LnkCap:|LnkSta:'
```

The validated result is current/max generation `2/2` and `LnkSta` speed
`5GT/s`. Width is expected to remain `x1` on the currently tested boards.

In a passthrough VM, also verify the physical endpoint and its upstream port
from the hypervisor. A guest-visible success should not be substituted for
physical-link evidence. This simple one-time guide does not install the
optional Proxmox monitoring/recovery coordinator used in the research lab.

## Recovery and re-running

On failure, inspect the complete unit journal before retrying. If the GPU did
not return to NVIDIA, reboot the guest from the hypervisor console or reboot
the bare-metal host. A reset or power cycle restores the stock volatile state.

The oneshot unit remains `active (exited)` after success. To perform another
carefully controlled attempt in the same boot, stop all GPU clients and use:

```bash
sudo systemctl restart cmp100-pcie-gen2.service
```

Do not repeatedly retrain a failing link. Confirm the exact firmware hashes,
driver version, BDF allow-list, upstream Gen2 capability and absence of GPU
clients before one deliberate retry.
