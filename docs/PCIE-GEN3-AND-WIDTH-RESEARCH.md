# PCIe Gen3 and lane-width investigations on `10de:1d84`

Speed and width are separate constraints. The supported volatile procedure
achieved **Gen2 x1** on two boards, doubling measured host-transfer rate from
the stock Gen1 x1 baseline. An early bare-metal Gen2 failure was superseded:
it used the pre-TU102 `0x8c040[19:18]` encoding incorrectly. With the corrected
value, both cards trained Gen2 x1. Proxmox was not the fundamental obstacle.

## The Gen3 gate

In the controlled Gen3 test, the endpoint's XVE capability image showed
`BAR0[0x88084]=0x02454c12` (maximum Gen2),
`BAR0[0x880a4]=0x00000006` (Gen1 and Gen2 supported, Gen3 absent), and
`BAR0[0x880a8]=2` (Gen2 target). Forcing the higher-level RM policy did make
the driver request target speed 3, but a direct BAR0 write/read returned 2
within about 1.9 microseconds. No Gen3 equalization phase was observed.
Trying to change the advertised capability fields through the tested signed
writer did not make them writable. Thus a driver policy change or a repeated
retrain cannot by itself produce Gen3 on this observed endpoint state.

The evidence localizes the remaining gate to the endpoint capability image;
it does **not** identify its source as OTP, strap, firmware, or hardwired SKU
logic. The older shorthand "99% fused" overstates what these measurements
prove. There is no demonstrated Gen3 link or throughput result.

## x16 physical link versus GPU initialization

After lane capacitors were added, two independent IFR edits were tested from
the original 1 MiB image. An address edit at physical `0x214` (`42 -> 02`)
avoided the x1 assignment by retargeting a register write. A later value edit
at `0x21f` (`08 -> 80`) left the register address at `0x8c040` while selecting
16 lanes. Both trained a real **Gen1 x16** link on the x16-capable root port.
In both cases NVIDIA 550.163.01 failed adapter initialization, so the card was
not available to NVML or CUDA. The second image was independently read back
twice after programming, with exactly that one byte changed; flash correctness
did not imply driver correctness. The [full public x16 report](PCIE-X16-1D84-NEGATIVE-2026-09-25.md)
has the post-failure register comparison and Nouveau result.

The post-failure 31-register comparison found 18 raw differences and three
different ordinary DEVINIT predicate truth values. All eight sampled engine
poll addresses on the modified target yielded poison MMIO rather than valid
status. These are *post-failure snapshots*, not an instruction trace, so they
cannot identify the exact first failed branch or wait. A stock-versus-edited
comparison on the same physical card is still missing.

The in-band SPI path could write and verify a candidate image. It did not
remove the x16 initialization blocker. The experimental write implementation
is kept in the private research release; this report discloses what it did and
what the hardware returned.

Source campaign identifiers: `gen2-gv100-field-encoding-2026-09-06`,
`gen3-xve-capability-gate-2026-09-08`,
`spi-ifr-width-baremetal-2026-09-20`, and
`ifr43-x16-negative-2026-09-25` in the private archive.
