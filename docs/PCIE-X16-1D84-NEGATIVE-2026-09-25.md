# CMP100 `10de:1d84` x16 experiments: physical link achieved, GPU init failed

**Status (25 September 2026): no working x16 unlock on our `1d84` card.**
After the additional PCIe lane coupling capacitors were fitted, two distinct
one-byte IFR edits each caused a real Gen1 x16 link between the endpoint and
an x16-capable root port. In both cases the GPU still enumerated, but NVIDIA
550.163.01 failed `RmInitAdapter (0x31:0xffff:2640)` and did not expose the
device to NVML/CUDA. No x16 copy or compute result exists.

This is a negative result for these two tested edits, not evidence that x16
is impossible on all CMP100 boards. It also does not imply that the two edits
failed internally at the same instruction.

## What changed, and what was verified

The stock 1 MiB SPI image has an IFR record at physical offset `0x214` that
read-modify-writes BAR0 register `0x08C040` to select x1. The tested variants
were separate; the second started from the original image, not the first:

| Experiment | Physical SPI byte | Intended effect | Measured outcome |
| --- | --- | --- | --- |
| Address edit | `0x214: 42 -> 02` | Retarget that IFR record from `0x08C040` to `0x08C000`, thereby avoiding its x1 assignment | Gen1 x16 link after hardware lane repair; NVIDIA init failed. The effect of writing `0x08C000` remains unknown. |
| Value edit | `0x21F: 08 -> 80` | Keep target `0x08C040` but set `LINK_SPECIFIER[31:27]` to 16 | Gen1 x16 link; NVIDIA init failed. The original `0x214` byte stayed `0x42`. |

The value edit required a 4 KiB sector-0 erase and rewrite: NOR page program
alone cannot turn `0x08` into `0x80`. Before the reboot, one writer readback
and two independent, write-disabled full-chip reads matched the pinned
candidate byte-for-byte. The only full-image difference from the original
was `0x21F`. These checks establish that the intended image was written;
they do not establish that the image is bootable as an NVIDIA GPU.

The second edit produced `LnkSta: Speed 2.5GT/s, Width x16` at both the
endpoint and root port. This is **Gen1 x16**, not Gen3 x16. The other GPUs
on the host remained usable; the modified GPU was absent from `nvidia-smi`.
Our earlier [PCIe results](PCIE-RESULTS.md) and the
[Gen3 investigation](GEN3-LIMIT.md) concern distinct questions.

**Research capability and release scope:** On this one `10de:1d84` card, we
demonstrated in-band SPI erase/program through our privileged-write
vulnerability path: the complete image was read back three times and matched
the intended edit. The code and operational material for that capability are
available **only as a private research release**, shared directly with
collaborators; they are not published in this repository. The demonstrated
write capability must not be confused with a working x16 GPU unlock.

## Driver and register observations

One target-only Nouveau bind identified GV100 and BIOS `88.00.9d.00.00`,
then reported `M0203E not matched`, 0 MiB of unknown memory, PRIVRING faults,
BAR timeouts and an Oops in `gp102_acr_wpr_patch`. Nouveau did not initialize
the card. Its own compatibility limitations mean this test cannot, on its
own, identify the cause of NVIDIA's failure. It was not repeated after the
host recovered.

A read-only comparison between the modified target and a working stock CMP
in the same boot found different final states:

| BAR0 offset | Modified target | Working control |
| --- | ---: | ---: |
| `0x00158C` | `0` | `0` |
| `0x00171C` | `0` | `1` |
| `0x020350` | `0` | `0x02000000` |
| `0x02240C` | `0` | `2` |

A corrected 31-register read-only capture found 18 raw differences. Evaluating
45 distinct ordinary DEVINIT condition records against these **post-failure**
snapshots yielded three different truth values: condition #25 on
`0x08C040[19:18]`, #35 on `0x120064[6]`, and #43 on `0x0205E4[6:2]`.
The original proposed 28-register list missed three ordinary-condition
registers; the corrected comparison is in
[`results/x16-2026-09-25/devinit-live-comparison.json`](../results/x16-2026-09-25/devinit-live-comparison.json).

All eight engine poll-address reads on the modified target returned
`0xBADF3000`, an invalid/poison MMIO value. The working control returned
ordinary values satisfying the masks. We therefore **cannot** identify a
specific timed-out poll from this snapshot. Likewise, `0x02240C[1]` being
zero after failure does not prove that the VBIOS interpreter stopped just
before the record that sets it: initialization or reset could have cleared
the register later. The compared boards may also have different VBIOS data.

## Where this leaves the investigation

The strongest missing experiment is a stock-versus-edited capture on the
**same physical card**, with full functional validation after each boot.
An execution trace through the undecoded DEVINIT calls would further
separate an early branch, a failed wait, and a later reset. Neither has been
completed. Until then, the two tested IFR edits must be described as
**x16-link-only, GPU-initialization failures**.

No firmware image, InfoROM, serial number, host address, full kernel log or
prebuilt payload is included in this public result. The private laboratory
retains the original captures and exact-image hashes for audit. The upstream
[duggasco GV100 work](https://github.com/duggasco/CMP100-210) documents a
different SKU (`1df4`) and is not a demonstrated `1d84` x16 solution.

## Continue the investigation

If you want to help investigate why the `1d84` card trains x16 but fails GPU
initialization, please contact me privately using the route in
[SECURITY.md](../SECURITY.md). I can share access to the private research
repository with interested collaborators; it contains the fuller test history
and artifacts. Please do not post firmware images, card identifiers, private
logs or new privileged-write details in a public issue.
