# PCIe Gen2 and link-width results

## Gen2 result

Both tested CMP100-210 endpoints were observed at PCIe Gen2 x1 after a volatile
private research intervention. The physical link state, not merely a software
maximum field, reported 5 GT/s and width x1.

A pinned 32 MiB transfer test produced approximately:

| Direction | Stock-control measurement | Gen2 measurement |
| --- | ---: | ---: |
| Host to device | 207 MB/s | 417 MB/s |
| Device to host | 209 MB/s | 419 MB/s |

The approximately twofold change is consistent with a physical Gen1-to-Gen2
transition on a single lane.

## Width result

Both cards remained x1. They were tested through two different upstream paths:

- one root path capable of Gen3 x8;
- one root path capable of Gen3 x16.

An NVMe device on the relevant riser operated at Gen3 x4. These observations
make a common server-wide Gen1 or x1 policy an implausible explanation for the
two CMP endpoints. They do not determine whether the width limitation is due to
missing board components, straps, firmware or device configuration.

No Gen1 x16 or Gen2 x16 result is claimed. Testing a card with the missing PCIe
coupling components populated is the next useful width experiment.

## Public reproducibility boundary

Standard PCIe state can be collected with `tools/collect_state.sh`. The script
performs no configuration writes. The procedure used to establish the private
experimental state is not included in this repository.
