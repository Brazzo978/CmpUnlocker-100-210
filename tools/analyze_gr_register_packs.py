#!/usr/bin/env python3
"""Parse the four already-materialized public GV100 GR artifacts offline.

The wire formats are taken directly from the pinned Nouveau source:
  gk20a_fw_av  = little-endian { u32 addr; u32 data; }
  gk20a_fw_aiv = little-endian { u32 addr; u32 index; u32 data; }

This script intentionally reports only a bounded target-address audit.  It
does not infer register semantics, firmware control flow, or hardware state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


GPC_BASE = 0x500000
GPC_STRIDE = 0x8000
GPC_COUNT = 6


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hex32(value: int) -> str:
    return f"0x{value:08x}"


def target_sets() -> dict[str, list[int]]:
    return {
        "gpc_tpc_count_0x2608": [GPC_BASE + i * GPC_STRIDE + 0x2608 for i in range(GPC_COUNT)],
        "gpc_ppc0_tpc_mask_0x0c30": [GPC_BASE + i * GPC_STRIDE + 0x0C30 for i in range(GPC_COUNT)],
        "gpc_ppc1_tpc_mask_0x0c34": [GPC_BASE + i * GPC_STRIDE + 0x0C34 for i in range(GPC_COUNT)],
        "gpc_ppc2_tpc_mask_0x0c38": [GPC_BASE + i * GPC_STRIDE + 0x0C38 for i in range(GPC_COUNT)],
        "gr_gpc_count_0x409604": [0x409604],
        "physical_raw_0x021760_stride4_slots0_to5": [0x021760 + i * 4 for i in range(GPC_COUNT)],
        "physical_status_0x021c38_stride4_slots0_to5": [0x021C38 + i * 4 for i in range(GPC_COUNT)],
        "physical_ctrl_0x021838_stride4_slots0_to5": [0x021838 + i * 4 for i in range(GPC_COUNT)],
    }


def parse_pack(data: bytes, fmt: str, filename: str) -> dict:
    if fmt == "av":
        size = 8
        decoder = lambda chunk: (*struct.unpack("<II", chunk), None)
        source_format = "gk20a_fw_av: {u32 addr, u32 data}"
    elif fmt == "aiv":
        size = 12
        decoder = lambda chunk: struct.unpack("<III", chunk)
        source_format = "gk20a_fw_aiv: {u32 addr, u32 index, u32 data}"
    else:
        raise ValueError(fmt)

    if len(data) % size:
        raise ValueError(f"{filename}: {len(data)} is not a multiple of {size}")

    targets = target_sets()
    by_address = {addr: label for label, values in targets.items() for addr in values}
    matches = {label: [] for label in targets}

    for record_index, offset in enumerate(range(0, len(data), size)):
        addr, second, third = decoder(data[offset : offset + size])
        if fmt == "av":
            value = second
            ignored_index = None
        else:
            ignored_index = second
            value = third
        label = by_address.get(addr)
        if label:
            matches[label].append(
                {
                    "record_index": record_index,
                    "file_offset": hex32(offset),
                    "address": hex32(addr),
                    "raw_value": hex32(value),
                    "raw_index_ignored_by_gk20a_gr_aiv_to_init": (
                        None if ignored_index is None else hex32(ignored_index)
                    ),
                }
            )

    return {
        "filename": filename,
        "sha256": sha256(data),
        "bytes": len(data),
        "record_format": source_format,
        "record_size": size,
        "record_count": len(data) // size,
        "loader_write_classification": "raw nvkm_wr32 direct write; no mask field in this format",
        "loader_expansion": "count=1, pitch=1 for every record under the pinned GV100 gk20a loader",
        "target_matches": matches,
    }


def raw_occurrences(data: bytes, filename: str) -> dict:
    targets = target_sets()
    matches = {label: [] for label in targets}
    for label, values in targets.items():
        for value in values:
            needle = struct.pack("<I", value)
            start = 0
            while True:
                offset = data.find(needle, start)
                if offset < 0:
                    break
                matches[label].append(
                    {
                        "file_offset": hex32(offset),
                        "little_endian_u32": hex32(value),
                        "aligned_u32": offset % 4 == 0,
                    }
                )
                start = offset + 1
    return {
        "filename": filename,
        "sha256": sha256(data),
        "bytes": len(data),
        "classification": "opaque Falcon data image in this audit; not passed to gk20a_gr_load_sw AV/AIV register-pack decoders",
        "raw_little_endian_target_occurrences": matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.firmware_dir
    nonctx = (root / "sw_nonctx.bin").read_bytes()
    ctx = (root / "sw_ctx.bin").read_bytes()
    fecs_data = (root / "fecs_data.bin").read_bytes()
    gpccs_data = (root / "gpccs_data.bin").read_bytes()

    report = {
        "schema": "gv100-gr-pack-audit/v1",
        "source_contract": {
            "linux_source_commit": "5225b8eec4c9bb21aecff6295fab6346a3c3738e",
            "loader": "gk20a_gr_load_sw: sw_nonctx -> gk20a_gr_av_to_init; sw_ctx -> gk20a_gr_aiv_to_init",
            "gpc_address_formula": "GPC_UNIT(gpc, r) = 0x500000 + gpc * 0x8000 + r",
            "gpc_instances_scanned": list(range(GPC_COUNT)),
        },
        "targets": {
            label: [hex32(address) for address in values]
            for label, values in target_sets().items()
        },
        "packs": [
            parse_pack(nonctx, "av", "sw_nonctx.bin"),
            parse_pack(ctx, "aiv", "sw_ctx.bin"),
        ],
        "data_images": [
            raw_occurrences(fecs_data, "fecs_data.bin"),
            raw_occurrences(gpccs_data, "gpccs_data.bin"),
        ],
        "limits": [
            "An absent target record is limited to these two public register packs and raw word occurrences in these two data images.",
            "This audit does not establish that a missing register cannot be written by another firmware stage, generated code, a computed address, or hardware initialization.",
            "No device, driver, SSH, secure memory, decryption, or protection-bypass action was performed.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
