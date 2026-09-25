# CMP100-210 research status (25 September 2026)

This is a public account of the experiments on `10de:1d84` (PG500 SKU 110).
"Working" means observed on the named hardware and software baseline, not on
every board sold as CMP100-210. The detailed reports linked below separate
measurement, external reports, and hypotheses. A PCIe link, a writable register,
and a usable CUDA device are separate outcomes.

| Area | Directly observed on our `1d84` cards | Limit |
| --- | --- | --- |
| Tensor compute | Two cards ran numerically checked FP16 GEMM at 74.179/75.040 TFLOP/s in the published benchmark. The control register changes `0x999 -> 0x888`. | Still 68 SM per card; this is not an 80-SM or V100 identity conversion. |
| FP64 | Two-card post-unlock 4096-square medians were 5.620/5.619 TFLOP/s with sampled CPU-reference checking. | No same-run stock-to-unlocked FP64 ratio. |
| PCIe Gen2 | Both tested endpoints and root ports trained Gen2 x1. Pinned host transfers were about 417–419 MB/s in the measured setup. | Width stayed x1 in the supported volatile path. |
| HBM clock | Headless NVML offset `138` reproduced 877 MHz versus stock 810 MHz. Verified device-copy bandwidth increased from 711.381 to 766.503/764.268 GB/s. | Clock, copy bandwidth and model throughput are distinct claims; no LLM gain was measured in that comparison. |
| Extra SM/TPC | The signed CTRL experiment could *exclude one more* active TPC and restore it. Both tested cards otherwise stayed at 34 TPC/68 SM, including after warm forced POST. | No method to remove their base exclusions or reach 80 SM was demonstrated. |
| Gen3 | Software policy requested Gen3, but the endpoint advertised only Gen1/Gen2 and immediately clamped the target back to Gen2. | The origin of the capability image is unknown; "fused" is a hypothesis, not a verified OTP reading. |
| x16 width | With added physical lane capacitors, two separate one-byte IFR edits each trained Gen1 x16. Full-chip write readback matched the intended image. | NVIDIA initialization failed on both edits; no usable x16 CUDA device or x16 bandwidth result. |
| FECS privilege | ACR changed the FECS PLM from `0x8f` to `0xff`; in that isolated window an otherwise rejected host write to the Tensor control register was accepted. | Persistence across NVIDIA RM initialization and broader register access remain unproved. |
| Trap and SPI | ACR opened trap 20 and a bounded in-band SPI read returned JEDEC `EF 60 14` and the expected IFR byte. Later in-band program and sector rewrite were independently read back. | The write capability is real; the x16 GPU-init failure shows why write readback alone is not an unlock. The dangerous writer is private. |
| InfoROM / identity | Both local logical InfoROM images were 880 bytes with the same layout. A tested live device-ID-shadow write did not persist to the post-run snapshot. | No ordinary InfoROM flag or volatile ID spoof achieved Gen3, x16 or V100 identity. |
| Falcon contents | Sampled CMP secure PMU bodies did not yield a coherent V100-like disassembly; post-DEVINIT host PIO returned zero IMEM and poison DMEM at selected words. | This is not a general proof that Falcon data can never be extracted. |
| CMP-to-CMP P2P | Our earlier local baseline had `cudaDeviceCanAccessPeer=false`; the later external mesh report found 0/72 correct CMP-to-CMP pairs despite capability `OK`. | No byte-exact CMP-to-CMP P2P was demonstrated in our lab. External V100-to-CMP success is separately attributed. |
| Video engines | No local NVENC/NVDEC re-enablement or video workload was validated. | An upstream `1df4` compute result does not prove video support on `1d84`. |
| Telemetry and live metrics | NVML HBM/temperature monitoring and the optional legacy-CUPTI llama.cpp probe are documented in this repo. | Metrics instrumentation is not itself a GPU unlock or a P2P test. |

## Where to read the evidence

- [Tensor measurements](TENSOR-RESULTS.md), [PCIe Gen2](PCIE-RESULTS.md),
  [NVML/HBM](NVML-TELEMETRY-AND-CLOCKS.md), and [methodology](METHODOLOGY.md).
- [Topology and SM investigation](TOPOLOGY-AND-SM-RESEARCH.md).
- [Gen3 gate and x16 tests](PCIE-GEN3-AND-WIDTH-RESEARCH.md), plus the
  [x16 negative-result report](PCIE-X16-1D84-NEGATIVE-2026-09-25.md) and its
  [derived 31-register comparison](../results/x16-2026-09-25/devinit-live-comparison.json).
- [ACR, PLM, Falcon, InfoROM and SPI findings](FIRMWARE-AND-PRIVILEGE-RESEARCH.md).
- [P2P evidence and open mapping question](P2P-RESEARCH-STATUS.md).
- [Variant and upstream-claim matrix](VARIANTS-AND-EXTERNAL-EVIDENCE.md).

All numerical results are tied to dated captures in the private laboratory
archive. This public tree contains reviewed summaries and selected derived data,
not raw proprietary ROMs, unique card identifiers, credentials or the
experimental flash writer. Researchers can request the private evidence through
[the contact route](../SECURITY.md).
