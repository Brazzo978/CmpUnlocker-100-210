# Debian 13: one-time V100-like HBM clock

This optional procedure raises the tested CMP100-210 HBM2 clock from 810 MHz
to 877 MHz, matching the normal Tesla V100 PCIe memory frequency. It is a
software overclock of the CMP, **not** a stock CMP operating point, a VBIOS
crossflash or a conversion into a Tesla V100.

The setting is volatile. It remains active only while the helper's headless
Xorg process is running and is removed by the reset command or a reboot. This
procedure does not require the Tensor or PCIe unlock.

## Tested baseline

- Debian GNU/Linux 13;
- NVIDIA proprietary driver `550.163.01`;
- CMP100-210 PCI device `10de:1d84`;
- subsystem `10de:12b9`;
- VBIOS `88.00.9D.00.00`;
- Samsung 16 GB HBM2 on the two tested boards.

The helper fails closed when the device, product name, VBIOS or driver differs.
Do not remove those checks and assume another CMP PCB or HBM bin is equivalent.

## Safety

Overclocking can cause incorrect calculations, memory corruption, an XID,
application failure or a GPU reset. A short successful test does not guarantee
long-term stability. Run it only on hardware you own, without valuable jobs or
irreplaceable data in GPU memory.

This method starts a local no-scanout Xorg instance. It does not listen on TCP,
but it is intended for a trusted, single-user compute host rather than a shared
multi-user server.

## 1. Install the required packages

Start from the same NVIDIA 550 baseline described by the other Debian 13
guides, then install the X/NV-CONTROL components:

```bash
sudo apt update
sudo apt install -y xserver-xorg-core nvidia-settings
```

Confirm the exact tested baseline:

```bash
nvidia-smi --query-gpu=index,pci.bus_id,pci.device_id,name,vbios_version,driver_version \
  --format=csv,noheader
```

For every target the important fields must be `0x1D8410DE`,
`NVIDIA CMP 100-210`, `88.00.9D.00.00` and `550.163.01`.

## 2. Apply 877 MHz once

From the repository root:

```bash
sudo ./scripts/cmp100-hbm-v100-clock start
```

The helper detects all matching CMP100-210 GPUs, starts an isolated headless
NV-CONTROL display on `:99`, applies the smallest tested transfer-rate offset
that produces the 877 MHz PLL step and verifies the clock on every card.

A successful run prints one line like this per GPU:

```text
PASS: GPU 0 HBM clock is 877 MHz
```

Check it at any time with:

```bash
sudo ./scripts/cmp100-hbm-v100-clock status
```

`nvidia-smi` may continue to show 810 MHz as the advertised maximum or default;
use `clocks.current.memory` and the helper's NV-CONTROL readback to verify the
active clock.

## 3. Validate before real work

If the CUDA demo suite is installed, a first non-destructive bandwidth check is:

```bash
/usr/local/cuda/extras/demo_suite/bandwidthTest \
  --device=0 --memory=pinned --mode=shmoo --dtod
```

The two-card test observed approximately 733 GB/s in repeated 32 MiB D2D sample
runs at 877 MHz versus approximately 676 GB/s on the 810 MHz control card. The
CUDA sample reported `PASS`, with no XID in the short test. These measurements
are evidence for a real bandwidth increase, not a stability guarantee.

Check the kernel log after testing:

```bash
sudo dmesg | grep -Ei 'NVRM|Xid|ECC' | tail -50
```

Use a proper memory-integrity workload before trusting long calculations.

## Reset

Restore the stock offset and stop the temporary Xorg process with:

```bash
sudo ./scripts/cmp100-hbm-v100-clock reset
```

A reboot also restores the 810 MHz stock CMP clock. Do not enable this manual
one-shot procedure automatically until the card has passed sustained validation.

## Why `nvidia-smi` alone does not work

On the tested CMP, NVML advertises a zero memory VF-offset range and both
`nvidia-smi -lmc 876` and `nvidia-smi -ac 876,...` are rejected. With Xorg
`Coolbits=8`, NV-CONTROL instead reports the memory transfer rate as editable
and exposes an offset range of `0..1100`. The tested `+138 MT/s` offset selects
877 MHz actual HBM clock on this exact baseline.

The control is a DDR transfer-rate offset. Its units and PLL steps must not be
confused with a direct HBM clock offset or blindly reused on another VBIOS.
