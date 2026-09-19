# Opt-in CMP100 Gen2 automation

The Proxmox host component in this guide is needed only when a physical GPU is
passed through to a Debian virtual machine. Install `install-pcie-host.sh` on
the Proxmox host and the normal guest helper inside that VM. On bare metal, or
when there is no host above the Debian system, do **not** install the Proxmox
coordinator; use only the normal local/guest Gen2 helper.

This is the published GV100 Gen2 sequence, separate from the Tensor installer.
The validated target is CMP100-210 (`10de:1d84`) in a Debian 13 guest with
NVIDIA `550.163.01` on Proxmox passthrough. The validated link result is
**Gen2 x1**; neither Gen3 nor x16 is claimed. All identity, payload and original
firmware checks remain mandatory.

## What runs where

- In the guest, `cmp100-pcie-gen2.service` runs after the Tensor unlock and
  before Docker, SSH and interactive user sessions. SSH becomes
  available after the bounded unlock attempt, so a login-time GPU monitor
  cannot race the driver handoff. The helper checks explicit GPU BDFs, payload
  and module hashes, the running kernel, original firmware, and GPU clients.
  It writes `0x88610=1` and `0x8872c=6` through the signed ACR primitive with
  an NVIDIA handoff between writes. It then unbinds NVIDIA, changes only
  `0x8c040[19:18]` to `1`, issues `CHANGE_SPEED`, verifies Gen2, and rebinds NVIDIA.
- On Proxmox, `cmp100-pcie-host@VMID.timer` checks about once a minute. It
  validates the configured physical devices against the VM's current `hostpci`
  assignment, vendor/device ID, VFIO ownership and upstream ports. Already
  settled Gen2 links return without a guest command or register write.
- When recovery is necessary, the host waits for the guest agent and Tensor
  service, calls the guest helper through QGA, and verifies **both physical
  ends** plus new uncorrectable AER status. Root-port target speed must already
  allow Gen2; the tested roots remain configured for Gen3. The coordinator
  performs no host BAR0 or root configuration writes and stores no credentials.
- A failed attempt is latched for the guest boot. The timer does not repeatedly
  detach a workload after that failure. Inspect the logs before using `--retry`.

## Guest installation

First complete the manual validation in the
[Tensor one-time guide](DEBIAN13-TENSOR-ONESHOT.md). Stop GPU workloads for the
first PCIe test. Configure optional boot enablement only after both manual
procedures pass, using the [coordinated boot guide](DEBIAN13-BOOT-PROFILE.md).
Install from this checkout inside the guest:

```bash
sudo apt install build-essential linux-headers-$(uname -r) python3 busybox kmod pciutils
sudo ./install-pcie-guest.sh
sudoedit /etc/default/cmp100-pcie
```

Set the **guest** BDFs after checking `lspci -Dnn` and `nvidia-smi`. Example:

```bash
CMP100_PCIE_BDFS="0000:01:00.0 0000:02:00.0"
CMP100_PCIE_STOP_SERVICES="my-inference.service"
```

`CMP100_PCIE_STOP_SERVICES` is optional. Configured units are stopped only if
active and restarted asynchronously if this invocation stopped them. Any
remaining CUDA client causes a failure before the driver handoff. Restarting
an inference service does not guarantee that its previously loaded model is
reloaded: configure that application's own startup behavior if required.
For another workload service, add an appropriate `Before=` ordering override
to the guest PCIe unit before enabling it at boot.

```bash
sudo systemctl start cmp100-pcie-gen2.service
sudo journalctl -u cmp100-pcie-gen2.service -b --no-pager
nvidia-smi --query-gpu=pci.bus_id,pcie.link.gen.current,pcie.link.gen.max --format=csv
sudo systemctl enable cmp100-pcie-gen2.service
```

Require the final `PASS; cleanup completed`, not just an ACR return code.
The installer builds the hook for the running guest kernel. Re-run it for a
new kernel before relying on automation; a vermagic mismatch fails closed.
It leaves the existing Tensor installation unchanged and uses separate state
under `/var/lib/cmp100-pcie` and payloads under `/usr/lib/cmp100-pcie`.

## Proxmox installation

Enable QEMU Guest Agent for the VM and verify that it responds. Install the
guest helper first. In a checkout on the physical Proxmox node, copy and edit
the example mapping; these addresses and VMID are examples, not autodetection:

```bash
cp systemd/cmp100-pcie-mapping.example.json mapping.json
editor mapping.json
sudo ./install-pcie-host.sh 101 mapping.json
sudo /usr/local/sbin/cmp100-pcie-host 101 --check
sudo systemctl start cmp100-pcie-host@101.service
sudo journalctl -u cmp100-pcie-host@101.service --no-pager
sudo systemctl enable --now cmp100-pcie-host@101.timer
```

The mapping contains only a VMID and explicit host/guest BDF pairs. Verify it
against `qm config VMID --current 1`, physical sysfs topology and guest PCI
inventory. The helper does not alter passthrough or VM startup settings.
The timer skips stopped VMs and checks again later; it does not start them.

## Failure and recovery

Guest logs: `/var/log/cmp100-pcie/run-*.log` and the unit journal.
Host logs: the `cmp100-pcie-host@VMID.service` journal.
Host state: `/var/lib/cmp100-pcie-host/VMID.json`, mode 0600.
Guest firmware originals and hash manifest are kept in `/var/lib/cmp100-pcie`.
The helper restores firmware contents through resolved paths, preserving the
distribution's `bl.bin` symlink, and attempts to restore drivers and modules.
Recovery failures return nonzero and retain the temporary backup for inspection.
A monitor can hold `/dev/nvidia-uvm` without appearing in NVML's compute-app
list. Module removal therefore allows ten short attempts for transient users,
then reports GPU holder PIDs, names, cgroups and device paths before failing.
It does not force module removal or kill unconfigured processes. A live/manual
invocation still requires existing interactive GPU clients to be stopped.
A forced kill or power loss cannot run a shell cleanup trap; inspect firmware
hashes and driver state before retrying after such an interruption.

```bash
# Stop periodic recovery while investigating (host):
sudo systemctl disable --now cmp100-pcie-host@101.timer
# After diagnosis, allow one new attempt in this guest boot:
sudo /usr/local/sbin/cmp100-pcie-host 101 --retry
# Disable boot reapplication (guest):
sudo systemctl disable cmp100-pcie-gen2.service
```

Disabling a service does not undo the current volatile registers. A device
reset or power cycle returns the hardware to its stock policy. No VBIOS flash
or fuse change is performed. The separate bare-metal C diagnostic is not the
host coordinator and must not be used on a VFIO-owned device.

## Validated behavior

- Offline coordinator tests cover explicit mappings, invalid or duplicate
  devices, settled-link no-op behavior, asynchronous QGA exit checking and
  atomic state replacement.
- Guest reboot validation established the order Tensor unlock, local Gen2
  retrain, NVIDIA handoff and workload startup.
- The host coordinator detected a deliberately degraded single link, invoked
  the guest helper only for that endpoint, and verified both physical link ends
  at Gen2 with no new uncorrectable AER status.
- The unit ordering keeps SSH, interactive sessions and common container
  workloads behind the bounded driver handoff, preventing a login-time GPU
  monitor from racing the procedure.

These checks establish guest reboot reapplication and the Proxmox live-recovery
path on the validated baseline. They do not claim Gen3 or a wider PCIe link.
