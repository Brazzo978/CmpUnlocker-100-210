#!/usr/bin/env python3
"""Classify a bounded GV100 PMU readability capture against known PRE_OS IMEM."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

VALUE = re.compile(
    r"cmp100_pmu_readability VALUE space=([ID]) secure=([01]) "
    r"address=0x([0-9a-fA-F]+) value=0x([0-9a-fA-F]+)"
)
HEAD = re.compile(
    r"cmp100_pmu_readability BEGIN .* captures=(\d+) samples=(\d+) "
    r"invalid=(\d+) nmissed=(\d+)"
)
SELECTORS = re.compile(
    r"saved_imemc=0x([0-9a-fA-F]+) restored_imemc=0x([0-9a-fA-F]+) "
    r"saved_dmemc=0x([0-9a-fA-F]+) restored_dmemc=0x([0-9a-fA-F]+)"
)
EXPECTED_IMEM_SHA256 = "82a2c8abf38ef5e7fea658bf25c52a74b8d21377c54c37072b3a39d223955a8c"


def analyze(text: str, imem: bytes) -> dict:
    digest = hashlib.sha256(imem).hexdigest()
    if digest != EXPECTED_IMEM_SHA256:
        raise ValueError(f"unexpected CMP PRE_OS IMEM SHA-256: {digest}")
    head = HEAD.search(text)
    selectors = SELECTORS.search(text)
    values = []
    seen = set()
    for match in VALUE.finditer(text):
        space, secure_s, address_s, value_s = match.groups()
        item = {
            "space": space,
            "secure": bool(int(secure_s)),
            "address": int(address_s, 16),
            "value": int(value_s, 16),
        }
        key = (item["space"], item["secure"], item["address"])
        if key in seen:
            raise ValueError(f"duplicate sample {key}")
        seen.add(key)
        if space == "I" and item["address"] + 4 <= len(imem):
            expected = struct.unpack_from("<I", imem, item["address"])[0]
            item["expected"] = expected
            item["matches_expected"] = item["value"] == expected
        values.append(item)

    errors = []
    if not head:
        errors.append("missing BEGIN record")
        captures = samples = invalid = nmissed = None
    else:
        captures, samples, invalid, nmissed = map(int, head.groups())
        if captures != 1:
            errors.append(f"captures={captures}, expected 1")
        if samples != 24 or len(values) != 24:
            errors.append(f"samples={samples}, parsed={len(values)}, expected 24")
        if invalid:
            errors.append(f"observer invalid={invalid}")
        if nmissed:
            errors.append(f"probe nmissed={nmissed}")
    selector_restored = False
    if not selectors:
        errors.append("missing selector record")
    else:
        a, b, c, d = (int(v, 16) for v in selectors.groups())
        selector_restored = a == b and c == d
        if not selector_restored:
            errors.append("PIO selectors were not restored exactly")

    secure = [v for v in values if v["space"] == "I" and v["secure"]]
    nonsecure = [v for v in values if v["space"] == "I" and not v["secure"]]
    dmem = [v for v in values if v["space"] == "D"]
    secure_body = [v for v in secure if v["address"] >= 0x200]
    secure_matches = sum(bool(v.get("matches_expected")) for v in secure_body)
    known_matches = sum(
        bool(v.get("matches_expected")) and bool(v.get("expected")) for v in secure
    )
    degenerate = {v["value"] for v in secure_body} <= {0, 0xFFFFFFFF, 0xBADF1100}
    dmem_uniform_value = (
        dmem[0]["value"] if dmem and len({v["value"] for v in dmem}) == 1 else None
    )
    dmem_uniform_poison = dmem_uniform_value in {0, 0xFFFFFFFF, 0xBADF1100, 0xDEAD5EC2}

    if errors:
        verdict = "invalid_capture"
    elif secure_body and secure_matches == len(secure_body) and not degenerate:
        verdict = "selected_secure_imem_words_match_loaded_pre_os"
    elif secure_matches:
        verdict = "partial_secure_imem_match_inconclusive"
    else:
        verdict = "secure_imem_not_shown_readable"

    return {
        "valid": not errors,
        "errors": errors,
        "verdict": verdict,
        "scope": "selected words only; no authentication-bypass or arbitrary-dump claim",
        "expected_imem_sha256": digest,
        "selector_restored": selector_restored,
        "secure_imem_known_matches": known_matches,
        "secure_body_matches": secure_matches,
        "secure_body_degenerate": degenerate,
        "dmem_uniform_value": dmem_uniform_value,
        "dmem_uniform_poison": dmem_uniform_poison,
        "secure_nonsecure_pairs_equal": all(
            next((n["value"] for n in nonsecure if n["address"] == s["address"]), None)
            == s["value"] for s in secure
        ),
        "values": values,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("cmp_pre_os_imem", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(
        args.capture.read_text(encoding="utf-8", errors="replace"),
        args.cmp_pre_os_imem.read_bytes(),
    )
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
