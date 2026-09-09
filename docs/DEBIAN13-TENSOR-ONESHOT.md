# Debian 13: one-time Tensor unlock

This guide applies the tested volatile Tensor unlock once. It does not enable
the boot service and it does not enable PCIe Gen2. A reset or power cycle
returns the card to its stock state.

The supported baseline is deliberately narrow:

- Debian GNU/Linux 13 (Trixie), amd64;
- the stock Debian 13 kernel 6.12 series;
- the proprietary Debian NVIDIA driver `550.163.01`;
- NVIDIA CMP100-210 (`10de:1d84`, GV100);
- the exact GV100 firmware hashes checked below.

Do not follow this guide remotely unless you also have an out-of-band, VM or
hypervisor console. The operation temporarily detaches the NVIDIA driver. A
failed run can require a reboot.

## 1. Install Debian's tested driver

Make sure the Debian sources for `trixie`, `trixie-security` and
`trixie-updates` include `main contrib non-free non-free-firmware`. On a normal
Debian 13 installation this is configured in `/etc/apt/sources.list.d/debian.sources`.

Install the stock kernel headers, proprietary driver, GV100 firmware and the
small set of build/runtime dependencies:

```bash
sudo apt update
sudo apt install -y \
  linux-headers-$(uname -r) \
  nvidia-driver=550.163.01-2 nvidia-kernel-dkms=550.163.01-2 \
  firmware-nvidia-graphics=20250410-2 \
  build-essential python3 busybox kmod git pciutils
sudo reboot
```

Do not select `nvidia-open-kernel-dkms`: Volta is not supported by NVIDIA's
open kernel-module flavor. Do not use a Trixie-backports kernel for this tested
path. Secure Boot is acceptable only if the locally built DKMS and helper
modules are enrolled and can actually load.

Driver 550 is retained here only because it is the exact validated research
baseline. It is no longer a maintained branch; use this setup only on an
isolated, trusted system, not as a general-purpose internet-facing server.

After reboot, require all of these checks to succeed:

```bash
uname -r
dpkg-query -W nvidia-driver nvidia-kernel-dkms firmware-nvidia-graphics
nvidia-smi
lspci -Dnn | grep -i '10de:1d84'
```

`nvidia-smi` must report driver `550.163.01`. Stop if it reports another
version or if the card is not bound to NVIDIA.

## 2. Verify the exact firmware

```bash
sha256sum \
  /lib/firmware/nvidia/gv100/gr/fecs_sig.bin \
  /lib/firmware/nvidia/gv100/acr/bl.bin \
  /lib/firmware/nvidia/gv100/acr/ucode_load.bin
```

Expected SHA-256 values:

```text
8b636da582662995aa5ed3b8f530299a8aca801934657c85e6a031fb8fd1eab1  fecs_sig.bin
01326cc997716bebb0eab9fe1f463fffa0ee586079e4ca0e5bb096ab6fcfedab  bl.bin
b349f0355548716770531eb9082544bb84de059f17bbf3952fb09dd27d8673d4  ucode_load.bin
```

Stop if any value differs. The installer intentionally refuses unknown
firmware; bypassing that check is unsupported and can make the GPU unavailable.

## 3. Build and install the helper

```bash
git clone https://github.com/Brazzo978/CmpUnlocker-100-210.git
cd CmpUnlocker-100-210
sudo bash ./install.sh
```

Installation does not run or enable the unlock. It derives the payload from
the locally installed, hash-checked firmware and builds the narrow kernel
helper for the running kernel.

If you want to target specific cards, first obtain their full BDFs:

```bash
lspci -Dnn | grep -i '10de:1d84'
```

Then create `/etc/default/cmp100-unlocker`, for example:

```bash
sudo tee /etc/default/cmp100-unlocker >/dev/null <<'EOF'
CMP100_BDFS="0000:01:00.0 0000:02:00.0"
EOF
```

Replace the example addresses with the values on your machine. If the variable
is omitted, the helper detects every visible `10de:1d84` function.

## 4. Apply it once

Stop every CUDA, inference, display, container and monitoring process using the
target GPUs. In particular, stop Docker or application services that reopen
`/dev/nvidia*` automatically. Then run:

```bash
sudo systemctl start cmp100-tensor-unlock.service
sudo journalctl -u cmp100-tensor-unlock.service -b --no-pager
nvidia-smi
```

Leave `nvidia-persistenced` running if it was already active: the helper records
that state, stops it at the correct moment and restores it during cleanup. A
successful log ends in `PASS` and contains, for every selected GPU:

```text
function_rc=0
tensor_409664=0x00000888
unlock and NVIDIA handoff PASS
```

`nvidia-smi` must list every card again. Do **not** run
`systemctl enable cmp100-tensor-unlock.service` for a one-time setup.

## 5. Optional Tensor verification

The repository benchmark needs a CUDA-enabled PyTorch environment. The
published result used PyTorch `2.6.0+cu124`:

```bash
sudo apt install -y python3-venv
python3 -m venv ~/cmp100-venv
~/cmp100-venv/bin/pip install --upgrade pip
~/cmp100-venv/bin/pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
~/cmp100-venv/bin/python tools/benchmark_tensor.py --n 8192 --warmup 8 --repeats 15
```

The two reference cards measured approximately 74–75 TFLOPS median. Cooling,
clocks and power limits affect the exact number.

## Recovery and re-running

If `nvidia-smi` no longer sees a passed-through card, reboot the guest from the
hypervisor console. On bare metal, reboot or power-cycle the host. The method
does not flash the VBIOS or program eFuses.

The oneshot unit remains `active (exited)` after success. To run it again in
the same boot, stop all GPU users and use:

```bash
sudo systemctl restart cmp100-tensor-unlock.service
```

## Distribution references

- [Debian 13 `nvidia-driver` package](https://packages.debian.org/trixie/nvidia-driver)
- [Debian 13 `nvidia-kernel-dkms` package](https://packages.debian.org/trixie/nvidia-kernel-dkms)
- [Debian 13 `firmware-nvidia-graphics` package](https://packages.debian.org/trixie/firmware-nvidia-graphics)
