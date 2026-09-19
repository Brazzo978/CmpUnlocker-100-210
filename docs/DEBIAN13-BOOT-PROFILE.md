# Debian 13: optional CMP100 boot profile

This guide enables the three independently validated volatile changes in a
strict boot order: Tensor unlock, PCIe Gen2, then the headless NVML `hbm-877`
profile.

This is **not** the default installation mode. Enabling it can leave the GPU
driver unavailable, prevent GPU-dependent workloads from starting, or require
recovery from an out-of-band console. A failed driver handoff can require a
guest or host reboot. The procedure does not flash a VBIOS or alter eFuses, but
that does not make unattended boot-time execution risk-free.

Do not enable it on a remote-only machine. Keep a hypervisor, serial, IPMI or
physical console available and prove every component manually after a cold
boot before enabling automation.

## Validated HBM scope

The published profile was validated on two CMP100-210 cards with NVIDIA driver
`550.163.01` and VBIOS `88.00.9D.00.00`. Their SKU reported an 810 MHz stock
maximum; a raw HBM offset of `+138` repeatedly read back as 877 MHz.

GV100 products may share silicon or memory technology, but that does not prove
equivalent board power, cooling, memory binning or stability. The repository
does not claim that 877 MHz is safe for every card. Test each GPU under sustained
load, watch HBM temperature and errors, and accept the risk before automating
the profile. Frequencies beyond the named `hbm-877` profile are deliberately
not supported by the boot service.

## 1. Install without enabling

Follow the one-time Tensor and PCIe guides first, then install the monitoring
and HBM components:

```bash
sudo ./install.sh
sudo ./install-pcie-guest.sh
sudo ./install-monitoring.sh
```

None of these installers starts or enables an unlock or overclock.

## 2. Configure explicit allow-lists

Put the physical BDFs reported by `lspci -D` in both driver-handoff defaults
files. Do not copy example addresses without checking the local topology.

`/etc/default/cmp100-unlocker`:

```bash
CMP100_BDFS="0000:01:00.0 0000:02:00.0"
CMP100_STOP_SERVICES="your-inference.service"
```

`/etc/default/cmp100-pcie`:

```bash
CMP100_PCIE_BDFS="0000:01:00.0 0000:02:00.0"
CMP100_PCIE_STOP_SERVICES="your-inference.service"
```

Use the immutable UUIDs printed by `cmp100-nvml-clock list` for HBM.

`/etc/default/cmp100-hbm-877`:

```bash
CMP100_HBM_UUIDS="GPU-... GPU-..."
# Optional: apply the same NVML power limit to every listed GPU.
# CMP100_POWER_LIMIT_WATTS=150
```

The HBM service rejects a missing, malformed or duplicate UUID, a non-CMP100
device, an untested driver/VBIOS baseline, or a final readback other than
offset `+138` and 877 MHz. The NVML offset is absolute, so the service reapplies
`+138` even if another offset is already present; it does not add 138 to the
existing value.

`CMP100_POWER_LIMIT_WATTS` is optional and remains disabled when omitted or
commented. When set, the same boot stage applies that limit through the Rust
NVML binary to every allow-listed UUID and verifies the final value. NVML also
checks the device-specific minimum and maximum before writing. For example,
uncommenting `CMP100_POWER_LIMIT_WATTS=150` requests 150 W; it does not invoke
`nvidia-smi`.

## 3. Prove every stage manually

Stop every GPU workload. Run each stage separately and inspect its journal
before continuing:

```bash
sudo systemctl start cmp100-tensor-unlock.service
sudo journalctl -u cmp100-tensor-unlock.service -b --no-pager

sudo systemctl start cmp100-pcie-gen2.service
sudo journalctl -u cmp100-pcie-gen2.service -b --no-pager

sudo systemctl start cmp100-hbm-877.service
sudo journalctl -u cmp100-hbm-877.service -b --no-pager
```

Require all of these final checks:

```bash
nvidia-smi
nvidia-smi --query-gpu=uuid,pci.bus_id,pcie.link.gen.current,clocks.current.memory,temperature.memory --format=csv
cmp100-nvml-clock status --json
systemctl --failed
```

Every selected card must be back on NVIDIA, report PCIe Gen2, HBM offset `+138`
and an effective 877 MHz memory clock. If the optional power limit is configured,
the JSON must also report that exact limit on every selected UUID. A successful
service exit without these end-state checks is not sufficient.

## 4. Gate the workload on the HBM service

Create a drop-in for the actual inference or container service. Replace the
example name with the real unit:

```bash
sudo systemctl edit your-inference.service
```

```ini
[Unit]
Requires=cmp100-hbm-877.service
After=cmp100-hbm-877.service
```

This prevents that workload from starting when the final HBM stage fails. Do
not add this dependency to SSH, the console or the rescue target: the recovery
path must remain usable.

## 5. Enable only after the manual proof

```bash
sudo systemctl enable cmp100-tensor-unlock.service
sudo systemctl enable cmp100-pcie-gen2.service
sudo systemctl enable cmp100-hbm-877.service
sudo systemd-analyze verify \
  /etc/systemd/system/cmp100-tensor-unlock.service \
  /etc/systemd/system/cmp100-pcie-gen2.service \
  /etc/systemd/system/cmp100-hbm-877.service
```

Reboot once from the recovery console. After boot, repeat every end-state check
from section 3 and also verify application health, model load and one real
inference request.

## Driver reloads after boot

These settings are volatile. A later NVIDIA driver unload/rebind can clear the
HBM offset even while a oneshot service still appears `active (exited)`. After
any runtime driver handoff, re-check the actual clock. If Tensor and Gen2 remain
valid but HBM returned to 810 MHz, use:

```bash
sudo systemctl restart cmp100-hbm-877.service
```

Do not restart Tensor or Gen2 while GPU workloads are active.

## Recovery

At the boot loader, append these kernel command-line options for one recovery
boot:

```text
systemd.mask=cmp100-tensor-unlock.service
systemd.mask=cmp100-pcie-gen2.service
systemd.mask=cmp100-hbm-877.service
```

Then disable the services and remove or correct the workload drop-in:

```bash
sudo systemctl disable --now cmp100-hbm-877.service cmp100-pcie-gen2.service cmp100-tensor-unlock.service
sudo systemctl revert your-inference.service
sudo systemctl daemon-reload
sudo reboot
```

A power cycle restores the volatile GPU state. It does not repair an unrelated
driver, kernel, firmware or filesystem problem; retain ordinary system backups.
