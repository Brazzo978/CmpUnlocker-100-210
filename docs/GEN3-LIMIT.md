# PCIe Gen3 negative result

## Conclusion

PCIe Gen3 was not achieved through the tested volatile software-policy changes.
The endpoint continued to expose a maximum link speed of 5 GT/s (Gen2), and its
standard Supported Link Speeds vector contained 2.5 and 5 GT/s but omitted
8 GT/s.

During the controlled experiment, the higher-level software policy was made to
request Gen3. The request reached the endpoint's standard Target Link Speed
field, but the first subsequent read already returned Gen2. No observable
Gen3/equalization transition occurred.

A separate bounded physical speed-change request was accepted as a command,
but the negotiated link remained Gen2 x1. No equalization phase was observed,
and no new correctable, non-fatal or fatal PCIe error was attributed to the
attempt.

## What this proves

- changing the tested high-level policy is insufficient;
- reporting a Gen3 choice in software does not make the endpoint Gen3-capable;
- the endpoint's own read-only PCIe capability image remains a lower gate;
- repeating the same target request while that capability remains unchanged is
  not expected to add information.

## What this does not prove

- that the GV100 physical layer is fundamentally incapable of 8 GT/s;
- whether the capability image originates in eFuse/OTP, straps, early firmware
  initialization or hardwired product logic;
- that a different board configuration or firmware would change the result;
- that crossflashing another product's firmware is safe or sufficient.

The most discriminating next measurement is an early, read-only comparison of
the standard PCIe capabilities on a CMP100-210 and a genuine V100/GV100 before
the vendor driver binds.
