# GEMM stress test (`tools/gemm_stress.py`)

## What it is

A sustained GEMM hammer for Tensor-core paths. Where
[`tools/benchmark_tensor.py`](../tools/benchmark_tensor.py) is a short FP16
validation (fixed 8k square, 15 iterations), this tool throws large matrix
multiplications at the GPUs for as long as you ask:

- size sweeps (`--sizes`) and explicit MxNxK shapes (`--shapes`);
- fixed-iteration mode (`--repeats`) or sustained timed mode (`--seconds`);
- correctness against an FP32 reference with full or corner-block checking;
- throughput gates (`--min-tflops`) and a Tensor-engagement check
  (`--tensor-ratio`, FP16 vs FP32 baseline);
- per-config power/thermal telemetry and drift (throttle) detection;
- multi-GPU runs, sequential or `--parallel`, with `--json`/`--csv` output.

## Quick start

Sustained 60 s hammering of 8k and 16k FP16 GEMMs on all GPUs:

```console
python3 tools/gemm_stress.py --sizes 8192 16384 --seconds 60
```

Prove the Tensor path is engaged (FP16 must beat FP32 by 3x), with telemetry:

```console
python3 tools/gemm_stress.py --sizes 8192 --tensor-ratio 3 --telemetry
```

Soak two cards in parallel with machine-readable output:

```console
python3 tools/gemm_stress.py --devices 1 2 --sizes 16384 --seconds 3600 \
    --parallel --json soak.json --csv soak.csv
```

Cap power draw by testing at most 2 cards at once, batching the rest:
```console
python3 tools/gemm_stress.py --devices 0 1 2 3 4 5 6 7 --sizes 8192 \
    --seconds 300 --max-parallel 2 --tensor-ratio 3 --telemetry
```

## Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--devices` | all | GPU indices to stress |
| `--sizes` | 8192 | square NxNxN sizes to sweep |
| `--shapes` | — | extra MxNxK shapes, e.g. `4096x2048x8192` |
| `--dtype` | fp16 | dtypes to stress: `fp16`, `bf16`, `fp32` |
| `--layout` | nn | transpose variant: `nn`, `nt`, `tn`, `tt` |
| `--warmup` | 5 | untimed warm-up iterations per config |
| `--repeats` | 20 | timed iterations per config (fixed mode) |
| `--seconds` | 0 | sustained seconds per config; >0 wins over `--repeats` |
| `--validate` | auto | `full`, `block`, `off`, or `auto` (full when it fits in budget, else block) |
| `--validate-block` | 2048 | corner-tile edge for block validation |
| `--validate-every` | 0 | re-validate every K iters (`0`: first + last only) |
| `--tol` | 0.02 | max relative Frobenius error vs FP32 reference |
| `--min-tflops` | 0 | fail configs below this median TFLOPS |
| `--tensor-ratio` | 0 | require FP16 median ≥ R × FP32 baseline per shape (auto-adds FP32) |
| `--max-mem-gb` | 12 | skip configs estimated above this device budget |
| `--init` | randn | operand fill: `randn` or `uniform` |
| `--seed` | 1234 | base RNG seed (offset per device) |
| `--telemetry` | off | sample `nvidia-smi` power/temp/clocks per config |
| `--parallel` | off | run devices concurrently (one thread each) |
| `--max-parallel` | 0 | most devices at once; batches the rest (0: sequential, or all-at-once with `--parallel`) |
| `--fail-fast` | off | stop a device on first FAIL/ERROR |
| `--log-interval` | 10 | progress seconds in sustained mode (`0`: off) |
| `--json`, `--csv` | — | machine-readable result files |

## Reading the output

The header prints one line per device with its architecture, SM count and
derived tensor-core total (queried SMs times the per-SM width for that
compute capability, e.g. 8 on sm_70; CUDA exposes no direct count).

Each config reports median/min/max TFLOPS over its timed iterations, plus:

- `iters` — timed iterations completed (sustained mode: as many as fit);
- `drift` — second-half median vs first-half median; a large negative drift
  under sustained load suggests thermal throttling or clock decay;
- `rel_err` — worst relative Frobenius error vs the FP32 reference;
- telemetry — end-of-config `power/temp/SM/MEM` and start→end deltas.

Exit status is `0` only when every config passes; any `FAIL`/`ERROR` (or a run
where nothing executed) exits `1`, so the tool is safe to gate scripts on.
`Ctrl+C` stops promptly with partial results and exits `130`.

## Interpreting results on Volta

- The GV100 Tensor path is FP16. Expect ~70–85 TFLOPS on large (≥8k) FP16
  squares when the path is engaged, versus ~10–12 TFLOPS for FP32 on CUDA
  cores — hence `--tensor-ratio 3` is a comfortable engagement proof. Small
  matrices (≤2k) under-saturate the GPU; tensor-ratio gates belong on large
  shapes, not small ones.
- BF16 has no Tensor acceleration on Volta and runs much slower (~6 TFLOPS
  at 2k in testing); the flag exists for contrast, not as a pass target.
- FP32 validation error is exactly `0.0` (reference and test are the same
  computation); FP16 `rel_err` around `1e-4`–`1e-3` is normal accumulation
  noise. The default `--tol 0.02` has wide margin.
- `--validate auto` switches to corner-block checking when a full FP32
  reference would exceed the memory budget (e.g. 32k squares), so huge
  shapes still get a correctness check on the stressed output itself.

## Scope

This tool measures throughput and numerics of the GEMM path; it does not
change GPU state and performs no unlock, clock, or persistence operations.
For the short canonical validation behind the published Tensor result, see
[`docs/TENSOR-RESULTS.md`](TENSOR-RESULTS.md).

AMD GPUs are always excluded from device selection (even if explicitly
requested): under a ROCm PyTorch build `torch.cuda` can enumerate AMD cards,
and this project targets NVIDIA hardware only. The same guard applies to
[`tools/benchmark_tensor.py`](../tools/benchmark_tensor.py).

CPU-only regression coverage for device selection, shape parsing and memory
estimation lives in [`tests/test_gemm_stress.py`](../tests/test_gemm_stress.py)
and runs with plain `python3` (no GPU or PyTorch install required):

```console
python3 tests/test_gemm_stress.py
```
