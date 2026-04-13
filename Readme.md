# RF Listening Station

A high-performance radio receiver system based on the HackRF Pro, designed for stable, sensitive RF signal reception. This build combines precision clock synchronization, RF protection, and a portable form factor for reliable spectrum monitoring and analysis.

## Overview

This listening station delivers professional-grade performance through careful component selection and proper RF shielding. The core features include:

- **HackRF Pro** receiver for wideband signal reception (30 MHz - 6 GHz)
- **GPS-disciplined oscillator (GPSDO)** for frequency accuracy and stability
- **RF protection** via a limiter and attenuators for handling high-power signals
- **Battery-powered** operation for portable deployment
- **Compact mechanical design** using precision-machined discs and 3D-printed mounts

---

## Bill of Materials

### Radio Frequency Hardware

| Component | Part Number | Purpose |
|-----------|-------------|---------|
| [HackRF Pro](https://greatscottgadgets.com/hackrf/pro/) | - | Wideband SDR receiver |
| [Mini-Circuits 6GHz Limiter](https://www.minicircuits.com/WebStore/dashboard.html?model=VLM-63-2W-S%2B) | VLM-63-2W-S+ | RF signal clipping & ESD protection |
| [Mini-Circuits DC to 6 GHz Attenuator](https://www.minicircuits.com/WebStore/dashboard.html?model=K1-VAT-A%2B) | K1-VAT-A+ | Input attenuation for high-power signals |
| [Double-shielded RG316 coax](https://www.ebay.com/itm/145242622653) | 10cm, SMA-M to SMA-M | Connects GPSDO to HackRF |
| [15cm M-F RG316 coax pigtail](https://www.amazon.com/dp/B091TH6CCJ?th=1) | B091TH6CCJ | SMA bulkhead and HackRF Pro RF port with inline limiter |
| SMA-M antenna | - | Your choice of antenna(s) |

### Clock & Timing

| Component | Description |
|-----------|-------------|
| [Leo Bodnar LBE-1420 GPSDO](https://www.leobodnar.com/shop/index.php?main_page=product_info&products_id=393) | GPS-disciplined reference clock |
| [Leo Bodnar LBE-0006 Bracket](https://www.leobodnar.com/shop/index.php?main_page=product_info&products_id=402) | Mounting bracket for GPSDO |

### Power & USB

| Component | Specification | Purpose |
|-----------|---------------|---------|
| [Bioenno Power Battery](https://www.bioennopower.com/products/12v-3ah-lifepo4-battery-pvc?_pos=2&_fid=890042680&_ss=c) | BLF-1203AB 12V 3Ah LiFePO4 | Clean DC power supply (8-10 hour runtime) |
| [DC Power Cable](https://www.amazon.com/dp/B0CW2MTHKS?th=1) | 5.5mm × 2.1mm male | Battery to hub connector |
| [StarTech USB C Hub](https://www.startech.com/en-us/usb-hubs/hb31c2a2cme) | 4-port, 10Gbps | Industrial low-EMI USB hub |
| [Digirig Shielded USB Cables](https://digirig.net/product/short-usb-a-to-usb-c-cable/) | Shielded and RF choked | High-quality USB 2.0 connectivity |

**Note:** You will need an Anderson Powerpole crimper and terminator kit to complete the DC power cable.

### Mechanical Components

#### Base Assembly (two discs, each 20cm radius)

| Component | Material & Spec | Purpose |
|-----------|-----------------|---------|
| Base plate | FR-4 G10 Black fiberglass, 0.125" | Primary mounting surface |
| Ground plane | 5052 H32 Aluminum, 0.080" | RF shielding and counterpoise |

**Manufacturing files:**
- [Base plate](assembly/base_plate.dxf)
- [Ground plane](assembly/ground_plane.dxf)

These files are suitable for direct upload to [SendCutSend](https://sendcutsend.com/) or equivalent.

The base plate has North-South through holes, mirrored, for mounting the HackRF and GPSDO.

License: [Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).

#### Fasteners & Mounts

| Type | Specification | Quantity | Application |
|------|---------------|----------|-------------|
| M3-0.5 machine screws | Inner mounts | - | HackRF & GPSDO |
| M2.5-0.5 thumb screws | 8mm | 12 per disc | Outer plate assembly |
| M2.5-0.5 standoffs | 30mm | 12 per disc | Plate spacers |
| Mounting holes (HackRF) | For 10mm machine screws | 4 x 2 | Inner mounting plate |
| Mounting holes (GPSDO) | For 6mm machine screws | 4 x 2 | Inner mounting plate |

#### 3D Printed Parts

- [HackRF Mounting Brackets](https://www.thingiverse.com/thing:3019950) (original design)
- [Mount, 1.0mm shorter](assembly/hackrf_mount_79mm_h-1.0.stl) (final design selection)
- [Mount, 1.5mm shorter](assembly/hackrf_mount_79mm_h-1.5.stl) (may also work)

**License:** These modified designs are based on the original [Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).

---

## Design Rationale

### Why LiFePO4 Battery Power?

- **Clean DC output with minimal ripple** - Essential for clock stability and receiver noise floor
- **AC noise isolation** - Wall adapters inject switching noise that degrades both the GPSDO accuracy and HackRF receiver performance
- **Portable operation** - 3Ah capacity provides 8-10 hours of continuous operation
- **Compact footprint** - Fits on the base plate without external enclosure

### Why RF Limiter?

- **30 MHz - 6 GHz coverage** with only 0.4 dB insertion loss
- **11.5 dB RF signal clamping** protects the HackRF frontend from damage
- **ESD protection** guards against static discharge during field use
- **Readily available** through DigiKey in the US

### Why Attenuators?

- **Dynamic range extension** - Allows safe reception of high-power signals that would exceed HackRF input limits
- **Prevents compression & damage** - Protects sensitive receiver stages
- **Configurable attenuation** - Adjust input levels based on signal environment
- **US availability** via DigiKey

### Why GPS-Disciplined Oscillator?

- **Frequency accuracy** - ±0.05 ppm from satellite references
- **Clock stability** - Essential for coherent signal analysis and tuning precision
- **Wideband receiver optimization** - Improves image rejection and phase noise characteristics

The HackRF Pro ships with a built-in TCXO that's decent for casual use, but it still drifts - typically a few ppm over temperature changes and time. At higher frequencies that drift gets multiplied: even 1 ppm of error at 1 GHz means you're off by 1 kHz, which is enough to smear a narrowband signal right out of your passband. For wideband scanning that might not matter much, but the moment you're trying to do anything precise - demodulating a narrowband FM signal, characterizing a filter's passband edges, or comparing signals across sessions - that drift becomes the limiting factor. The GPSDO locks the HackRF's sample clock to GPS-disciplined cesium references, pushing accuracy down to parts-per-billion and eliminating drift as a variable entirely. It turns the HackRF from a "good enough" receiver into a calibrated instrument.

To see the difference yourself, tune GQRX to a known stable signal - a local NOAA weather broadcast or an AM carrier works well. With the HackRF running on its internal oscillator, watch the waterfall over 15-20 minutes as the board warms up. You'll see the signal trace slowly wander left or right as the internal clock drifts, sometimes shifting by several hundred hertz. Now connect the GPSDO to CLKIN and restart GQRX. That same signal will pin to a single column in the waterfall and stay there indefinitely - no wander, no drift, just a rock-solid line. The visual difference is immediate and striking: a meandering smear versus a razor-sharp trace.

---

## Next Steps

Refer to assembly and configuration documentation for detailed build instructions.
