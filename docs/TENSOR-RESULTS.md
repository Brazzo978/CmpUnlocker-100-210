# FP16 Tensor result

## Outcome

Two NVIDIA CMP100-210 cards completed a reproducible FP16 matrix-multiplication
benchmark after a volatile private research intervention:

| GPU | Median | Sample range | Validation |
| --- | ---: | ---: | --- |
| CMP100-210 GPU 0 | 74.179 TFLOPS | 74.036–75.600 TFLOPS | PASS |
| CMP100-210 GPU 1 | 75.040 TFLOPS | 74.550–76.537 TFLOPS | PASS |

The captured test used:

- PyTorch `2.6.0+cu124`;
- CUDA `12.4`;
- FP16 inputs;
- `8192 x 8192` matrices;
- 8 warm-up iterations;
- 15 measured iterations per GPU;
- CUDA events and a synchronization at the end of each measurement;
- a finite-result check after the final multiplication.

The reported operation count is `2 * N^3`, divided by the measured elapsed
time. The median, minimum and maximum are calculated independently for each
GPU.

## Scope

The result demonstrates that the tested CMP100-210 devices executed the GV100
FP16 Tensor path at the measured throughput. It does not disclose how the
volatile experimental state was established and does not imply persistence
across reset or power loss.

The exact captured console output is stored in
[`results/tensor-benchmark.txt`](../results/tensor-benchmark.txt), and the
benchmark implementation is [`tools/benchmark_tensor.py`](../tools/benchmark_tensor.py).
