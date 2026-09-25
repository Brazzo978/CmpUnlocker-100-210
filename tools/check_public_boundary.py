#!/usr/bin/env python3
"""Reject files outside the reviewed public research/release boundary."""

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
    "install-monitoring.sh",
    "install-pcie-host.sh",
    "install.sh",
    "docs/DEBIAN13-PCIE-GEN2-ONESHOT.md",
    "docs/DEBIAN13-BOOT-PROFILE.md",
    "docs/PCIE-AUTOMATION.md",
    "docs/NVML-TELEMETRY-AND-CLOCKS.md",
    "docs/LLAMA-CUPTI-LIVE-METRICS.md",
    "docs/DEBIAN13-TENSOR-ONESHOT.md",
    "docs/GEN3-LIMIT.md",
    "docs/GEMM-STRESS.md",
    "docs/PCIE-X16-1D84-NEGATIVE-2026-09-25.md",
    "docs/RESEARCH-STATUS-2026-09.md",
    "docs/TOPOLOGY-AND-SM-RESEARCH.md",
    "docs/PCIE-GEN3-AND-WIDTH-RESEARCH.md",
    "docs/FIRMWARE-AND-PRIVILEGE-RESEARCH.md",
    "docs/P2P-RESEARCH-STATUS.md",
    "docs/VARIANTS-AND-EXTERNAL-EVIDENCE.md",
    "docs/READ-ONLY-RESEARCH-TOOLS.md",
    "docs/METHODOLOGY.md",
    "docs/PCIE-RESULTS.md",
    "docs/TENSOR-RESULTS.md",
    "results/SHA256SUMS",
    "results/x16-2026-09-25/devinit-live-comparison.json",
    "results/tensor-benchmark.png",
    "results/tensor-benchmark.txt",
    "patches/llama.cpp/0001-server-add-optional-legacy-CUPTI-metric-collector.patch",
    "scripts/cmp100-pcie-gen2",
    "scripts/cmp100-pcie-host",
    "scripts/cmp100-hbm-877-boot",
    "scripts/cmp100-tensor-unlock",
    "src/Makefile",
    "src/gv100_nouveau_acr_hook.c",
    "systemd/cmp100-pcie-gen2.service",
    "systemd/cmp100-pcie-host@.service",
    "systemd/cmp100-pcie-host@.timer",
    "systemd/cmp100-pcie-mapping.example.json",
    "systemd/cmp100-hbm-877.service",
    "systemd/cmp100-tensor-unlock.service",
    "tools/benchmark_tensor.py",
    "tools/analyze_inforom.py",
    "tools/decode_gv100_top.py",
    "tools/test_decode_gv100_top.py",
    "tools/analyze_pmu_readability_probe.py",
    "tools/test_analyze_pmu_readability_probe.py",
    "tools/cmp100-sm-topology-readonly.py",
    "tools/audit_devinit_conditions.py",
    "tools/analyze_devinit_gpc_copy.py",
    "tools/analyze_gr_register_packs.py",
    "tools/analyze_devinit_tpc_path.py",
    "tools/analyze_devinit_width_path.py",
    "tools/analyze_v100_tpc_request.py",
    "tools/analyze_topology_stages.py",
    "tools/analyze_gv100_plm_baseline.py",
    "tools/decode_gv100_stages.py",
    "tools/inventory_gr_mmio_calls.py",
    "tools/compare_gv100_init_firmware.py",
    "tools/test_decode_gv100_stages.py",
    "tools/test_v100_tpc_request.py",
    "tools/test_inventory_gr_mmio_calls.py",
    "tools/build_payloads.py",
    "tools/check_public_boundary.py",
    "tools/collect_state.sh",
    "tools/cmp100-nvml-clock-v2.rs",
    "tools/cupti_legacy_probe.c",
    "tools/gpumon_v3_llama.c",
    "tests/test_gemm_stress.py",
    "tools/gemm_stress.py",
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
        r"(?i)\b(?:password|passwd|api[_-]?key)\s*[:=]|"
        r"\btoken\s*[:=]\s*['\"]"
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


def staged_deletions() -> set[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--diff-filter=D", "--name-only", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {item.decode("utf-8") for item in completed.stdout.split(b"\0") if item}


def main() -> int:
    failures: list[str] = []
    files = candidate_files()
    deletions = staged_deletions()

    for relative in files:
        path = ROOT / relative
        if relative in deletions:
            continue
        if path.is_symlink() or not path.is_file():
            failures.append(f"tracked path is not a regular file: {relative}")
            continue
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
