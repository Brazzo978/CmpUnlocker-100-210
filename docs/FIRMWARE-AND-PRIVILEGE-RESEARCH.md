# ACR, PLM, Falcon, InfoROM and SPI: demonstrated boundaries on `1d84`

These experiments concern one controlled `10de:1d84` laboratory setup. They
establish specific privilege and flash-access capabilities, not an 80-SM,
Gen3, P2P or usable x16 unlock. Dates and statuses matter: later positive
tests supersede earlier papers that had only proposed the same operation.

## The local ACR path

Our volatile ACR dependency-map over-copy routes a selected write through
signed firmware during a temporary driver initialization window. In the
18 September scratch test, a two-write chain left distinct markers in two
PBUS scratch registers and returned through the normal firmware path. That
validates two writes to those scratch addresses and clean rejoin; it does not
validate arbitrary five-write payloads or every target register.

On 19 September, a *single* ACR write changed FECS PLM `0x409650` from `0x8f`
to `0xff`. Both the Falcon mailbox and host readback returned `0xff` while
Tensor control `0x409664` remained `0x888`. A later isolated experiment made
the missing host-access test:

| Stage | PLM | Host write of `0x999` to `0x409664` | Readback |
| --- | ---: | --- | ---: |
| Before ACR | `0x8f` | rejected | `0x888` |
| After ACR | `0xff` | accepted | `0x999` |

A scratch-register positive control established that the host MMIO path itself
worked. The experiment therefore demonstrated host-L0 permission for this one
control register in that isolated window. A full VM stop/start restored the
stock `0x8f`/`0x888` readings and both NVIDIA devices. PLM persistence during
same-boot NVIDIA initialization was **not** measured.

A separate ACR run opened PRI decode trap 20 on this `1d84` card. Its PLM read
back `0x0fff`; the host then re-aimed the trap toward the SPI engine. A bounded
read returned fresh JEDEC `EF 60 14` and physical IFR byte `0x42` at `0x214`.
Volatile trap and SPI staging state was restored. This proves in-band SPI
**read** at that lifecycle boundary, not by itself program/erase ability.

Later tests did demonstrate SPI **write** on the same SKU: a single-byte
program and then a separate sector erase/rewrite yielded complete 1 MiB reads
matching the intended image, including independent write-disabled readbacks
for the later candidate. The x16 GPU initialization subsequently failed.
The write path is described here as a measured capability; the writer and
operational flash sequence are distributed only as a private research release.

## What the firmware and InfoROM investigations found

The two local stock logical InfoROM dumps each held 880 bytes with the same
ROM/IMG/OBD/OEM/BBO object layout. The observed differences were serial
characters; no ordinary Gen3, width or device-identity option was identified.
This logical image does not cover every physical SPI byte or reserved region.
A signed write attempt to the PCI device-ID shadow did not persist to the
post-Falcon observation: shadow, XVE and PCI configuration identities were
unchanged. Rejection and later overwrite remain distinct possibilities.

Offline PMU Falcon comparison found coherent-looking secure PRE_OS/DEVINIT
disassembly for V100 images, but near-maximal entropy and thousands of invalid
linear disassembly lines for the corresponding CMP bodies. This is consistent
with protected or encoded content, without identifying its exact format from
that test alone. The readable bootloaders differ around a `$cauth` bit-set;
its semantics were not established. In a separate *live* test after successful
warm DEVINIT return, 16 selected PMU IMEM reads returned zero and eight DMEM
reads returned `0xdead5ec2`. The selected secure memory was not exposed by
that host PIO method at that time. No protected firmware dump was obtained.

The [duggasco project](https://github.com/duggasco/CMP100-210) documents a
different FWSECLIC/InfoROM overflow on `1df4`. Its published payload is not
drop-in compatible with the sampled `1d84` firmware; the `1d84` FWSECLIC body
could not be used to validate those gadget addresses. Our ACR path and that
upstream trigger are different bugs that can reach related privileged-write
effects. The upstream trigger's exploitability on our exact firmware remains
undetermined.

Source campaign identifiers in the private archive: `acr-multiwrite-live-2026-09-18`,
`fecs-plm-acr-live-2026-09-19`, `fecs-plm-host-l0-live-2026-09-19`,
`trap20-spi-read-live-2026-09-19`, `spi-ifr-width-baremetal-2026-09-20`,
`gv100-inforom-local-2026-09-09`, `falcon-readability-2026-09-12`, and
`pmu-falcon-readability-live-2026-09-12`. Raw firmware and per-card identifiers
remain private; researchers can request the archive via [SECURITY.md](../SECURITY.md).
