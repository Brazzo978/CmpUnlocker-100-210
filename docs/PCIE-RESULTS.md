# PCIe Gen2 and link-width results

## Gen2 result

Both validated CMP100-210 endpoints were observed at PCIe Gen2 x1 after the
published volatile Gen2 procedure. The physical link state, not merely a
software maximum field, reported 5 GT/s and width x1.

A pinned 32 MiB transfer test produced approximately:

| Direction | Stock-control measurement | Gen2 measurement |
| --- | ---: | ---: |
| Host to device | 207 MB/s | 417 MB/s |
| Device to host | 209 MB/s | 419 MB/s |

The approximately twofold change is consistent with a physical Gen1-to-Gen2
transition on a single lane.


## Public reproducibility

Standard PCIe state can be collected with `tools/collect_state.sh`; that script
performs no configuration writes. The write-capable guest helper, Proxmox
coordinator and installation guides used for the published Gen2 procedure are
included in this repository.
