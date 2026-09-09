#!/usr/bin/env python3
"""Reject files outside the reviewed public Tensor/Gen2 release boundary."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPROVED_FILES = {
    ".github/workflows/public-boundary.yml",
    ".gitattributes",
    ".gitignore",
    "DISCLOSURE.md",
    "LICENSE",
    "PUBLICATION_BOUNDARY.md",
    "README.md",
    "SECURITY.md",
    "install-pcie-guest.sh",
    "install.sh",
    "docs/DEBIAN13-PCIE-GEN2-ONESHOT.md",
    "docs/DEBIAN13-HBM-V100-CLOCK-ONESHOT.md",
    "docs/DEBIAN13-TENSOR-ONESHOT.md",
    "docs/GEN3-LIMIT.md",
    "docs/METHODOLOGY.md",
    "docs/PCIE-RESULTS.md",
    "docs/TENSOR-RESULTS.md",
    "results/SHA256SUMS",
    "results/tensor-benchmark.png",
    "results/tensor-benchmark.txt",
    "scripts/cmp100-pcie-gen2",
    "scripts/cmp100-hbm-v100-clock",
    "scripts/cmp100-tensor-unlock",
    "src/Makefile",
    "src/gv100_nouveau_acr_hook.c",
    "systemd/cmp100-pcie-gen2.service",
    "systemd/cmp100-tensor-unlock.service",
    "tools/benchmark_tensor.py",
    "tools/build_payloads.py",
    "tools/check_public_boundary.py",
    "tools/collect_state.sh",
}
DENIED_SUFFIXES = {
    ".bin",
    ".efi",
    ".fw",
    ".ko",
    ".rom",
}
SENSITIVE_PATTERNS = {
    "private IPv4 address": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "credential assignment": re.compile(
        r"(?i)\b(?:password|passwd|token|api[_-]?key)\s*[:=]"
    ),
    "private RM symbol": re.compile(r"\b_nv\d+rm\b"),
}


def candidate_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    failures: list[str] = []
    files = candidate_files()

    for relative in files:
        path = ROOT / relative
        suffix = path.suffix.lower()

        if relative not in APPROVED_FILES:
            failures.append(f"file is not allowlisted: {relative}")

        if suffix in DENIED_SUFFIXES:
            failures.append(f"forbidden binary/payload type: {relative}")

        if relative == "tools/check_public_boundary.py":
            continue

        if suffix != ".png":
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in SENSITIVE_PATTERNS.items():
                if pattern.search(text):
                    failures.append(f"{label} found in {relative}")

    if failures:
        print("PUBLICATION BOUNDARY: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"PUBLICATION BOUNDARY: PASS ({len(files)} reviewed files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
