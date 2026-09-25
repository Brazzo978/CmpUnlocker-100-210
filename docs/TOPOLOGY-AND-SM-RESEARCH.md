# GV100 topology: why our two `1d84` cards still expose 68 SM

The compute unlock changes a throughput policy, not the discovered topology.
On the Debian 13 / NVIDIA 550.163.01 / CUDA 12.4 baseline, both tested cards
reported 34 active TPC and 68 CUDA SM after the unlock. A full GV100 design has
42 TPC/84 SM; an 80-SM V100-like target would need six more TPC from this state.

## Measured exclusion state

| Read-only observation | Card A | Card B |
| --- | ---: | ---: |
| GPC exclusion status `0x21c1c` | `0x1` | `0x0` |
| Physical-bank TPC status `0x21c38 + 4*g` | `7f,0,0,0,0,1` | `1,1,1,3,1,3` |
| TPC CTRL `0x21838 + 4*g` | all zero | all zero |
| Feature override disable `0x213f0` | zero | zero |
| GR available GPC count | 5 | 6 |
| Total available TPC / CUDA SM | 34 / 68 | 34 / 68 |

The disabled GPC on card A must not be counted twice with its seven excluded
TPC. The status-bank layout and GR logical numbering need not have identical
GPC indices. Read-only masks explain the reported 68 SM but neither prove
physical defects nor identify which input produced the base exclusion mask.

A signed ACR control experiment on card B changed one TPC CTRL value from zero
to three. Its effective STATUS changed `1 -> 3`, and the corresponding GR GPC
count fell `6 -> 5`; a separate signed restore to zero returned STATUS to one
and the GR count to six. A host CPU BAR0 write of the same value had been
ignored. This proves that the signed control can *add* an exclusion at the
tested lifecycle boundary. It did not remove the original `STATUS=1` bit.
The post-restore same-session NVIDIA handoff failed and required a platform
reset; register restoration alone is not a safe handoff proof. No CUDA run at
the temporary lower GR count was claimed.

The stock Nouveau `NvForcePost=1` warm experiment completed its DEVINIT path,
but 335 compared stage registers and the final 34-TPC/68-SM state matched the
ordinary warm attach. This only addresses the tested warm, stock firmware
path, not an unseen cold option-ROM path. Firmware and GR/FECS/GPCCS source
traces found consumers and numbering maps for *available* TPCs, but no verified
operation that creates six new available TPCs. One unresolved GPCCS/TPCCS
initialization write is not evidence of an unlock.

The post-unlock two-card validation measured FP16 8192-square medians
73.802/75.626 TFLOP/s and FP64 4096-square medians 5.620/5.619 TFLOP/s.
It checked finite output and an independent CPU-double 8x8 output block.
These FP64 numbers are post-unlock only; no stock-to-unlocked ratio follows.

Source campaign identifiers in the private archive:
`topology-compute-runtime-2026-09-12`,
`tpc-control-acr-positive-2026-09-12`,
`nouveau-force-post-2026-09-12`,
`gr-availability-boundary-2026-09-12`, and the FECS/GPCCS consumer traces.
