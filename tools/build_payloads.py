#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derive the tested CMP100 Tensor artifacts from local NVIDIA firmware."""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


STOCK_FECS_SHA = "8b636da582662995aa5ed3b8f530299a8aca801934657c85e6a031fb8fd1eab1"
STOCK_BL_SHA = "01326cc997716bebb0eab9fe1f463fffa0ee586079e4ca0e5bb096ab6fcfedab"
STOCK_UCODE_SHA = "b349f0355548716770531eb9082544bb84de059f17bbf3952fb09dd27d8673d4"

CUSTOM_FECS_SHA = "0c6916d57cd46780261bdf4e9dd5c81691f3c1685d1b1e5ea8b266e14d2ea95e"
CUSTOM_BL_SHA = "7ede4b56759a5e4276604c7bd7e7d6c82016192c019d6d9244e6b2002dbcaa9c"
CUSTOM_UCODE_SHA = "5bb3828d1b3e8782749b3a9bfcbfc746e88f4ff3ca041c87eea8b4a1eb003b33"

PCIE_POLICY_UCODE_SHA = "f5827fe768a62289fc8080877eb01eacb271b030dbf15c1b43e702f17dacf524"
PCIE_VECTOR_UCODE_SHA = "4c658410b734a022d603e766429b1779647850dbd04e9fde3963e378e024d4e2"


def sha256(blob: bytes | bytearray) -> str:
    return hashlib.sha256(blob).hexdigest()


def checked_read(path: Path, expected_hash: str, expected_size: int) -> bytes:
    blob = path.read_bytes()
    if len(blob) != expected_size:
        raise SystemExit(f"{path}: size {len(blob):#x}, expected {expected_size:#x}")
    digest = sha256(blob)
    if digest != expected_hash:
        raise SystemExit(f"{path}: SHA-256 {digest}, expected {expected_hash}")
    return blob


def put_u32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", blob, offset, value & 0xFFFFFFFF)


def build_fecs(stock: bytes) -> bytes:
    out = bytearray(stock)
    if struct.unpack_from("<I", out, 0x48)[0] != 2:
        raise SystemExit("fecs_sig.bin: expected Falcon ID 2")
    put_u32(out, 0x4C, 1)
    put_u32(out, 0x54, 0x504)
    return bytes(out)


def build_bl(stock: bytes) -> bytes:
    cave_offset = 0x370
    cave = bytes.fromhex("df de c0 de c0 09 40 f7 9f 00 f8 02")
    if any(stock[cave_offset : cave_offset + len(cave)]):
        raise SystemExit("bl.bin: selected code cave is not zero-filled")
    out = bytearray(stock)
    out[cave_offset : cave_offset + len(cave)] = cave
    return bytes(out)


def build_ucode_write(stock: bytes, value: int, target: int) -> bytes:
    if struct.unpack_from("<I", stock, 0x08)[0] != 0x4900:
        raise SystemExit("ucode_load.bin: unexpected bin_size")
    if struct.unpack_from("<I", stock, 0x10)[0] != 0x200:
        raise SystemExit("ucode_load.bin: unexpected data_offset")
    if struct.unpack_from("<I", stock, 0x14)[0] != 0x4700:
        raise SystemExit("ucode_load.bin: unexpected data_size")

    out = bytearray(stock)
    out.extend(bytes(0x8E00 - len(out)))
    put_u32(out, 0x08, 0x8E00)
    put_u32(out, 0x14, 0x8C00)

    # Candidate-C live-frame BAR0 writer. File offsets are the SEC2 DMEM
    # source words after the 0x3100-byte outer-container prefix.  The two
    # variable words are deliberately isolated: the signed parser receives
    # exactly one 32-bit value and exactly one BAR0 destination.
    initial_words = {
        0x8C70: 0x00000002,
        0x8C74: 0x00000300,
        0x8C78: 0x00000001,
        0x8C84: 0x00000002,
        0x8C88: 0x00000400,
        0x8C8C: value,
        0x8C90: target,
        0x8C94: 0x000033C8,
        0x8C98: 0x00001000,
        0x8C9C: 0x00000002,
        0x8CA0: 0x000033C8,
        0x8CA4: 0x00000C00,
        0x8CB8: 0x00002DF0,
        0x8CC4: 0x00000176,
    }
    for offset, value in initial_words.items():
        put_u32(out, offset, value)

    # Restore the verifier return and live LSB, clear depmap_count, compensate
    # the exact stack use and rejoin the signed parser. This chain returned rc=0.
    restore_words = {
        0x8CBC: 0x00005BB8,
        0x8CC0: 0x00000A09,
        0x8CC4: 0x00001FF9,
        0x8CC8: 0x00005B8C,
        0x8CCC: 0x000033C8,
        0x8CD0: 0x00000000,
        0x8CD4: 0x00000000,
        0x8CD8: 0x00001FF9,
        0x8CDC: 0x00000000,
        0x8CE0: 0x00005BE4,
        0x8CE4: 0x00000000,
        0x8CE8: 0x00000000,
        0x8CEC: 0x00000573,
        0x8CF0: 0x00000000,
        0x8CF4: 0x00000000,
        0x8CF8: 0x00005C38,
        0x8CFC: 0x00000B2A,
        0x8D00: 0xFFFFFFB4,
        0x8D04: 0x000008E6,
    }
    for offset, value in restore_words.items():
        put_u32(out, offset, value)
    return bytes(out)


def build_ucode(stock: bytes) -> bytes:
    """Build the existing Tensor payload without changing its byte stream."""
    return build_ucode_write(stock, 0x00000888, 0x00409664)


def write_checked(path: Path, blob: bytes, expected_hash: str) -> None:
    digest = sha256(blob)
    if digest != expected_hash:
        raise SystemExit(f"internal mismatch for {path.name}: {digest}, expected {expected_hash}")
    path.write_bytes(blob)
    print(f"{digest}  {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecs", type=Path, required=True)
    parser.add_argument("--bl", type=Path, required=True)
    parser.add_argument("--ucode", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    stock_fecs = checked_read(args.fecs, STOCK_FECS_SHA, 0xC0)
    stock_bl = checked_read(args.bl, STOCK_BL_SHA, 0x500)
    stock_ucode = checked_read(args.ucode, STOCK_UCODE_SHA, 0x4900)
    args.output.mkdir(parents=True, exist_ok=True)

    write_checked(args.output / "fecs_sig.candidate-c-504.bin", build_fecs(stock_fecs), CUSTOM_FECS_SHA)
    write_checked(args.output / "bl.load-candidate-c.bin", build_bl(stock_bl), CUSTOM_BL_SHA)
    write_checked(
        args.output / "ucode_load.tensor-success-lsb-restore.bin",
        build_ucode(stock_ucode),
        CUSTOM_UCODE_SHA,
    )
    write_checked(
        args.output / "ucode_load.pcie-policy-88610-1.bin",
        build_ucode_write(stock_ucode, 0x00000001, 0x00088610),
        PCIE_POLICY_UCODE_SHA,
    )
    write_checked(
        args.output / "ucode_load.pcie-vector-8872c-6.bin",
        build_ucode_write(stock_ucode, 0x00000006, 0x0008872C),
        PCIE_VECTOR_UCODE_SHA,
    )


if __name__ == "__main__":
    main()
