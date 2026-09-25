# Published read-only research tools

The following scripts are included so the non-mutating parts of the
investigation can be repeated. They do not ship any NVIDIA firmware. Output
from a real InfoROM image can contain a unique serial number: review and
redact it before sharing.

| Tool | Input and purpose |
| --- | --- |
| [`analyze_inforom.py`](../tools/analyze_inforom.py) | Decode and compare locally held logical InfoROM files; no GPU access. |
| [`decode_gv100_top.py`](../tools/decode_gv100_top.py) | Decode saved TOP-register snapshots, validating their structure before deriving GR reset selectors. |
| [`analyze_pmu_readability_probe.py`](../tools/analyze_pmu_readability_probe.py) | Classify a saved PMU PIO capture against an exact-hash, locally held PRE_OS image. The firmware input is not distributed. |
| [`audit_devinit_conditions.py`](../tools/audit_devinit_conditions.py) | Offline condition-table audit for the pinned original 1 MiB image and a local DEVINIT IR CSV; no flash access. |
| [`cmp100-sm-topology-readonly.py`](../tools/cmp100-sm-topology-readonly.py) | Guarded Linux sysfs/BAR0 read-only topology capture on a specified PCI device. It has a hardware-free `--self-test`. |
| [`decode_gv100_stages.py`](../tools/decode_gv100_stages.py) and [`analyze_topology_stages.py`](../tools/analyze_topology_stages.py) | Decode saved stage captures and compare their topology state. |
| [`analyze_devinit_tpc_path.py`](../tools/analyze_devinit_tpc_path.py), [`analyze_devinit_gpc_copy.py`](../tools/analyze_devinit_gpc_copy.py) and [`analyze_v100_tpc_request.py`](../tools/analyze_v100_tpc_request.py) | Offline, exact-input VBIOS/DEVINIT topology analysis. |
| [`analyze_devinit_width_path.py`](../tools/analyze_devinit_width_path.py) and [`compare_gv100_init_firmware.py`](../tools/compare_gv100_init_firmware.py) | Offline width-path and CMP/V100 ROM comparisons. |
| [`analyze_gr_register_packs.py`](../tools/analyze_gr_register_packs.py), [`inventory_gr_mmio_calls.py`](../tools/inventory_gr_mmio_calls.py) and [`analyze_gv100_plm_baseline.py`](../tools/analyze_gv100_plm_baseline.py) | Offline GR and PLM inventory/validation. |

The included unit tests use synthetic data; the complete `tools/test_*.py`
suite currently has 60 passing tests. The x16
[derived comparison](../results/x16-2026-09-25/devinit-live-comparison.json)
is published without the raw card identifiers and firmware image. The
experimental ACR, trap, SPI-program, Gen3 write and retrain scripts remain
private because they can modify or wedge hardware; their observed results are
described in the public reports.
