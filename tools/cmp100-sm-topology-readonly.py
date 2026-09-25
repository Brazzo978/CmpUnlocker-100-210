#!/usr/bin/env python3
"""Targeted read-only GV100 topology capture; does not unlock or reset a GPU.

Linux, Python 3 standard library. Run on the physical host where possible.
Example: sudo python3 cmp100-sm-topology-readonly.py 0000:0b:00.0 > gpu0.json
No unbind, PCI config writes, driver loads, power-state changes or fuse writes.
0x21760 is a reported candidate, not a verified alias of CTRL/STATUS.
"""
import argparse
import datetime
import json
import mmap
import os
from pathlib import Path
import re
import struct
import sys


def summarize(gpc_disable, tpc_status):
    if gpc_disable == 0xffffffff or any(v == 0xffffffff for v in tpc_status):
        return {"valid": False, "reason": "all-ones MMIO response; no topology inferred"}
    if len(tpc_status) != 6:
        raise ValueError("GV100 capture needs six physical GPC entries")
    enabled = [(~value & 0x7f) if not (gpc_disable & (1 << gpc)) else 0
               for gpc, value in enumerate(tpc_status)]
    tpcs = sum(mask.bit_count() for mask in enabled)
    return {"valid": True, "assumption": "GV100: 6 GPC, 7 TPC/GPC, 2 SM/TPC",
            "enabled_tpc_masks": [f"0x{x:02x}" for x in enabled],
            "enabled_tpc_count": tpcs, "inferred_sm_count": tpcs * 2,
            "derived_tensor_count": tpcs * 16,
            "note": "Counts describe status masks, not demonstrated execution or recoverability."}


def self_test():
    assert summarize(0, [0] * 6)["inferred_sm_count"] == 84
    assert summarize(0, [3, 3, 1, 1, 1, 1])["inferred_sm_count"] == 68
    assert summarize(0, [1, 1, 0, 0, 0, 0])["inferred_sm_count"] == 80
    assert summarize(1, [0] * 6)["inferred_sm_count"] == 70
    assert not summarize(0, [0xffffffff] + [0] * 5)["valid"]
    print("PASS: 84/80/68 SM, disabled GPC, invalid MMIO")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bdf", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.bdf or not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-1][0-9a-fA-F]\.[0-7]", args.bdf):
        parser.error("supply a full PCI BDF, e.g. 0000:0b:00.0")
    dev = Path("/sys/bus/pci/devices") / args.bdf.lower()
    def read(name):
        return (dev / name).read_text().strip()
    vendor, device = read("vendor"), read("device")
    if vendor != "0x10de" or device not in {"0x1d84", "0x1df4", "0x1db4", "0x1dc1", "0x1d81"}:
        raise RuntimeError(f"unexpected adapter {vendor}:{device}")
    driver = (dev / "driver").resolve().name if (dev / "driver").exists() else None
    if driver not in {None, "nvidia", "nouveau"}:
        raise RuntimeError(f"driver {driver}: refuse capture through VFIO or an unknown owner")
    runtime = read("power/runtime_status")
    if runtime != "active":
        raise RuntimeError(f"runtime_status={runtime}; refusing to change power state")
    with (dev / "config").open("rb", buffering=0) as config:
        header = config.read(8)
    if len(header) < 8 or not (struct.unpack_from("<H", header, 4)[0] & 2):
        raise RuntimeError("PCI memory decoding disabled/unreadable; no config write attempted")
    regs = {"gpc_disable_status": 0x21c1c,
            "feature_override_disable_unresolved_scope": 0x213f0,
            "gr_topology": 0x409604,
            "compute_pipe_control": 0x409664}
    for gpc in range(6):
        regs[f"gpc{gpc}_tpc_status"] = 0x21c38 + 4*gpc
        regs[f"gpc{gpc}_tpc_control"] = 0x21838 + 4*gpc
        regs[f"gpc{gpc}_reported_21760_candidate"] = 0x21760 + 4*gpc
        regs[f"gr_gpc{gpc}_available_tpcs"] = 0x502608 + 0x8000*gpc
    values = {}
    fd = os.open(dev / "resource0", os.O_RDONLY)
    try:
        for label, offset in regs.items():
            page = offset // mmap.PAGESIZE * mmap.PAGESIZE
            with mmap.mmap(fd, mmap.PAGESIZE, flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ, offset=page) as window:
                values[label] = struct.unpack_from("<I", window, offset-page)[0]
    finally:
        os.close(fd)
    result = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "bdf": args.bdf, "vendor": vendor, "device": device,
              "driver": driver, "runtime_status": runtime,
              "registers": {k: {"offset": f"0x{regs[k]:06x}", "value": f"0x{v:08x}"}
                            for k, v in values.items()},
              "topology": summarize(values["gpc_disable_status"],
                                    [values[f"gpc{i}_tpc_status"] for i in range(6)])}
    gr_gpcs = values["gr_topology"] & 0x1f
    gr_counts = [values[f"gr_gpc{i}_available_tpcs"] & 0x1f
                 for i in range(min(gr_gpcs, 6))]
    gr_valid = (1 <= gr_gpcs <= 6 and all(0 < n <= 7 for n in gr_counts)
                and values["gr_topology"] != 0xffffffff)
    result["gr_topology"] = {"valid": gr_valid, "gpc_count": gr_gpcs,
                             "tpcs_per_gpc": gr_counts,
                             "note": "GR GPC indexing must not be assumed identical to physical fuse indexing."}
    if gr_valid:
        result["gr_topology"]["inferred_sm_count"] = 2 * sum(gr_counts)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        sys.exit(1)
