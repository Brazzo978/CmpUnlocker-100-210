# CMP100-210 peer-to-peer: capability flag versus correct data

**No byte-exact CMP-to-CMP P2P is demonstrated by this project.** Our earlier
local two-CMP baseline had `cudaDeviceCanAccessPeer=false` in both directions
on Gen1 x1 links. The later, more extensive lab account supplied to us is an
*external test report*, not a run we reproduced on our boards. It describes
`nvidia-smi topo -p2p` returning `OK` after capability overrides, while a
72-pair CMP-to-CMP sweep under `intel_iommu=off` still found **0/72 correct**
data transfers. Several first-use reader cards wedged. Disabling IOMMU removed
one reported Xid class without making the bytes correct. V100-to-CMP transfers
were reported byte-exact in both directions on one pair at about 352/413 MB/s.

These observations distinguish PCIe forwarding from peer address translation.
They suggest the reader-side mapping/PDE/PTE construction as a lead: mailbox
aperture registers appeared programmed, but the first remote read returned
zeros or deterministic wrong data. That is an inference, not a captured proof
of a particular malformed peer PTE or a proven small-BAR1 root cause.
Rewriting aperture registers after enable did not repair the already-used
mapping in the external report. A capability display of `OK` is insufficient;
success requires a unique source pattern, a destination read on the other GPU,
byte equality in both directions, and no Xid or fallback.

The public [CMP 170HX/GA100 work](https://github.com/bayley/cmpunlocker)
reports a BAR1-based P2P fix on different hardware and software. It does not
establish a working GV100/CMP100 implementation. Likewise, the
[duggasco GV100 project](https://github.com/duggasco/CMP100-210) demonstrates
privileged-write/unlock research, not a byte-verified CMP-to-CMP P2P fix for
our `1d84`. A driver mapping correction is plausible, but remains unbuilt and
unvalidated here. No throughput claim for functional CMP-to-CMP P2P follows
from the external `OK` matrix.

The private archive retains the attributed external transcript and the
18 September evidence review. No external failure is relabelled as a local
hardware measurement.
