# CMP100 variants and outside claims

"CMP 100-210" is not one tested firmware/PCB identity. Our primary live
evidence is for `10de:1d84`, PG500 SKU 110. A published result for `1df4`
does not automatically transfer to `1d84`.

| Claim | Evidence available here | Classification |
| --- | --- | --- |
| `1d84` Tensor, HBM 877 MHz and Gen2 x1 | Repeated two-card local measurements with compute/copy checks | Demonstrated on named baseline |
| `1d84` ACR PLM, host-L0 Tensor write, trap-20 SPI access | Local isolated live tests with immediate register and SPI readback | Demonstrated capabilities, not full V100 conversion |
| `1d84` 80 SM, Gen3, V100 identity or functional CMP P2P | No passing local test | Unproved; specific tested routes have negative results |
| `1d84` physical x16 link after IFR edit | Local link measurements and flash readback | Demonstrated Gen1 x16 *link*; GPU initialization failed |
| `1df4` full compute-oriented software unlock | [duggasco's public one-card hardware result](https://github.com/duggasco/CMP100-210) | Upstream result, different SKU and trigger |
| `1df4` strap + V100 VBIOS (`1db4`) | Community description and screenshot, no local reproduction | Community claim only |
| `1dc1` strap + Titan V VBIOS (`1d81`) | Community description and screenshot, no local reproduction | Community claim only |

Even an upstream "100%" result should be read against what was actually
measured: compute and memory performance do not establish video-engine
availability, device identity, PCIe x16 or all V100 peripherals. Our sampled
`1d84` FWSECLIC body did not support reuse of the published `1df4` gadget
addresses, while our separate ACR dependency-map bug did work on `1d84`.

External ten-card Ubuntu and other community reports are useful leads, but
their driver, PCB, VBIOS and per-card tests are not interchangeable with the
two-card local baseline. Results reported by others are labelled as such.
Private source campaign: `gv100-variant-unlock-matrix-2026-09-18`,
`duggasco-rom-compat-1d84-2026-09-18`, community strap notes and the
ten-card report.
