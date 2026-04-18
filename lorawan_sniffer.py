#!/usr/bin/env python3
"""
lorawan_sniffer.py — Multi-channel US915 LoRaWAN sniffer using HackRF Pro + gr-lora_sdr.

Uses a polyphase channelizer (single FFT) to efficiently split the wideband
capture into 200 kHz channels, then feeds selected channels into gr-lora_sdr
receiver instances. This is orders of magnitude more efficient than parallel
FIR filters — O(N log N) per FFT vs O(N x taps) per channel.

Covers all 64 US915 125kHz uplink channels (902.3 - 914.9 MHz) with a
20 MHz HackRF capture centered at 908.6 MHz.

Usage:
    python3 lorawan_sniffer.py
    python3 lorawan_sniffer.py --sf 10
    python3 lorawan_sniffer.py --channels 0,8,16,24,32,40,48,56
    python3 lorawan_sniffer.py --sync-word 0x12   # Meshtastic

Requires:
    - gr-lora_sdr (tapparelj)
    - gr-osmosdr (for HackRF)
    - HackRF One/Pro connected
"""

import argparse
import datetime
import signal
import sys
import time
from pathlib import Path

import numpy as np
import pmt

from gnuradio import gr, blocks, filter as grfilter
from gnuradio.fft import window as fft_window
from gnuradio.filter import pfb
from gnuradio import lora_sdr
import osmosdr

# Import our LoRaWAN parser from the same directory
sys.path.insert(0, str(Path(__file__).parent))
try:
    from lorawan_parser import parse_lorawan_frame, print_frame_report
    HAS_PARSER = True
except ImportError:
    print("[WARN] lorawan_parser.py not found — raw hex only", file=sys.stderr)
    HAS_PARSER = False


# US915 uplink channel definitions (125 kHz BW)
# Channels 0-63: 902.3 + n*0.2 MHz, n=0..63
US915_BASE_FREQ = 902.3e6
US915_CHAN_SPACING = 200e3  # 200 kHz
US915_NUM_CHANNELS = 64
US915_BW = 125e3


def us915_freq(ch_num: int) -> float:
    """Return center frequency for US915 125kHz uplink channel number."""
    if not 0 <= ch_num < US915_NUM_CHANNELS:
        raise ValueError(f"Channel {ch_num} out of range 0-63")
    return US915_BASE_FREQ + ch_num * US915_CHAN_SPACING


class FrameCollector(gr.sync_block):
    """
    Sync block that watches a decoded byte stream from gr-lora_sdr's crc_verif
    block. It uses the 'frame_info' stream tags to identify frame boundaries
    and collects each frame's bytes into a complete payload buffer.

    gr-lora_sdr's crc_verif tags the first byte of each frame with:
      - key 'frame_info': PMT dict containing {'is_header': True, 'cr': int,
                          'pay_len': int, 'crc': bool, 'err': int}
    The 'pay_len' value tells us how many bytes belong to this frame.

    When a complete frame has been collected, on_frame() is called with the
    raw bytes, channel number, and frequency.
    """

    def __init__(self, channel: int, freq: float, parse: bool = True,
                 hex_only: bool = False, log_file=None):
        gr.sync_block.__init__(
            self,
            name=f"frame_collector_ch{channel}",
            in_sig=[np.uint8],
            out_sig=None,
        )
        self.channel = channel
        self.freq = freq
        self.parse = parse
        self.hex_only = hex_only
        self.log_file = log_file
        self.buffer = bytearray()
        self.expected_len = 0
        self.collecting = False

    def work(self, input_items, output_items):
        in0 = input_items[0]
        n = len(in0)

        # Get any tags in this work span
        abs_start = self.nitems_read(0)
        tags = self.get_tags_in_range(0, abs_start, abs_start + n)

        # Build a list of (relative_offset, pay_len) from frame_info tags
        frame_starts = []
        for tag in tags:
            try:
                key = pmt.symbol_to_string(tag.key)
            except Exception:
                continue

            if key != "frame_info":
                continue

            # frame_info is a PMT dict
            try:
                value = tag.value
                pay_len_pmt = pmt.dict_ref(
                    value, pmt.intern("pay_len"), pmt.PMT_NIL
                )
                if pmt.is_integer(pay_len_pmt):
                    pay_len = pmt.to_long(pay_len_pmt)
                    rel_offset = tag.offset - abs_start
                    frame_starts.append((rel_offset, pay_len))
            except Exception as e:
                print(f"[WARN] ch{self.channel}: tag parse error: {e}",
                      file=sys.stderr)

        # Process the work span, handling frame boundaries
        pos = 0
        for frame_start, pay_len in frame_starts:
            # If we had an in-progress frame, flush it first
            if self.collecting and self.buffer:
                self._maybe_emit_frame()

            # Bytes before this new frame start are stale, skip
            # Start collecting from this frame's position
            self.buffer = bytearray()
            self.expected_len = pay_len
            self.collecting = True
            pos = frame_start

            # Copy bytes for this frame up to end of work span or
            # next frame start (whichever comes first)
            remaining = self.expected_len - len(self.buffer)
            available = n - pos
            take = min(remaining, available)
            self.buffer.extend(in0[pos:pos + take].tobytes())
            pos += take

            if len(self.buffer) >= self.expected_len:
                self._maybe_emit_frame()

        # Continue collecting if mid-frame
        if self.collecting and pos < n:
            remaining = self.expected_len - len(self.buffer)
            available = n - pos
            take = min(remaining, available) if remaining > 0 else 0
            if take > 0:
                self.buffer.extend(in0[pos:pos + take].tobytes())

            if len(self.buffer) >= self.expected_len:
                self._maybe_emit_frame()

        return n

    def _maybe_emit_frame(self):
        """Emit a complete frame if we have enough bytes."""
        if len(self.buffer) >= self.expected_len and self.expected_len > 0:
            frame_bytes = bytes(self.buffer[:self.expected_len])
            self.buffer = bytearray()
            self.collecting = False
            self.on_frame(frame_bytes)

    def on_frame(self, data: bytes):
        """Called when a complete frame has been collected."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hex_str = data.hex(" ")

        # Log raw hex line if requested
        if self.log_file:
            try:
                self.log_file.write(
                    f"{ts}\tch{self.channel}\t{self.freq/1e6:.1f}MHz\t{hex_str}\n"
                )
                self.log_file.flush()
            except Exception as e:
                print(f"[WARN] log write error: {e}", file=sys.stderr)

        if self.hex_only or not (self.parse and HAS_PARSER):
            print(f"\n[{ts}] Ch {self.channel:2d} "
                  f"({self.freq/1e6:.1f} MHz, {len(data)} bytes)")
            print(f"  HEX: {hex_str}")
            return

        # Full parse
        try:
            frame = parse_lorawan_frame(data)
            print(f"\n[{ts}] Ch {self.channel:2d} "
                  f"({self.freq/1e6:.1f} MHz)")
            print_frame_report(frame)
        except Exception as e:
            print(f"\n[{ts}] Ch {self.channel:2d}: parse failed: {e}")
            print(f"  HEX: {hex_str}")


class LoRaWANSniffer(gr.top_block):
    def __init__(
        self,
        center_freq: float = 908.5e6,
        samp_rate: float = 20e6,
        channels: list[int] | None = None,
        sf: int = 7,
        cr: int = 1,          # coding rate 4/(4+cr), cr=1 => 4/5
        sync_word: int = 0x34,  # LoRaWAN public
        lna_gain: int = 16,
        vga_gain: int = 16,
        hex_only: bool = False,
        log_path: str | None = None,
    ):
        gr.top_block.__init__(self, "LoRaWAN US915 Sniffer")

        self.samp_rate = samp_rate
        self.center_freq = center_freq

        # Open log file if requested
        self.log_file = None
        if log_path:
            self.log_file = open(log_path, "a", buffering=1)
            print(f"[INFO] Logging raw frames to {log_path}")

        # Default: all 64 US915 125kHz uplink channels
        if channels is None:
            channels = list(range(US915_NUM_CHANNELS))

        ##################################################
        # Polyphase channelizer setup
        ##################################################
        # Channel width = 200 kHz (US915 channel spacing)
        # Number of PFB channels = samp_rate / chan_width
        chan_width = US915_CHAN_SPACING  # 200 kHz
        n_pfb_chans = int(samp_rate / chan_width)  # 100 at 20 MHz
        pfb_samp_rate = int(chan_width)  # 200 kHz per output channel

        # The LoRa demod wants samp_rate = BW * 2^n
        # 200 kHz = 125 kHz * 1.6 — not a power of 2 multiple
        # We need to resample each channel from 200 kHz to 250 kHz
        # Use a rational resampler: 250/200 = 5/4
        lora_samp_rate = int(US915_BW * 2)  # 250 kHz

        # Map each LoRa channel number to a PFB output index.
        # The PFB channelizer outputs are ordered by frequency:
        #   PFB output 0 = center_freq (DC)
        #   PFB output k = center_freq + k * chan_width  (for k < n/2)
        #   PFB output k = center_freq + (k - n) * chan_width  (for k >= n/2)
        #
        # So for a LoRa channel at freq f:
        #   offset = f - center_freq
        #   pfb_idx = round(offset / chan_width) % n_pfb_chans

        half_bw = samp_rate / 2.0
        valid_channels = []
        pfb_mapping = {}  # lora_ch -> pfb output index

        for ch in channels:
            ch_freq = us915_freq(ch)
            offset = ch_freq - center_freq
            if abs(offset) >= (half_bw - US915_BW):
                print(f"[WARN] Channel {ch} ({ch_freq/1e6:.1f} MHz) outside "
                      f"capture BW, skipping", file=sys.stderr)
                continue

            pfb_idx = round(offset / chan_width) % n_pfb_chans
            valid_channels.append(ch)
            pfb_mapping[ch] = pfb_idx

        if not valid_channels:
            print("[ERROR] No channels fit within the capture bandwidth!",
                  file=sys.stderr)
            sys.exit(1)

        print(f"[INFO] HackRF center: {center_freq/1e6:.1f} MHz, "
              f"sample rate: {samp_rate/1e6:.1f} MHz")
        print(f"[INFO] Coverage: {(center_freq - half_bw)/1e6:.1f} - "
              f"{(center_freq + half_bw)/1e6:.1f} MHz")
        print(f"[INFO] Polyphase channelizer: {n_pfb_chans} channels × "
              f"{chan_width/1e3:.0f} kHz = {samp_rate/1e6:.1f} MHz")
        print(f"[INFO] SF{sf}, CR 4/{4+cr}, sync word 0x{sync_word:02X}")
        print(f"[INFO] Monitoring {len(valid_channels)} LoRa channels "
              f"(PFB outputs used: {len(set(pfb_mapping.values()))})")
        for ch in valid_channels:
            freq = us915_freq(ch)
            offset = freq - center_freq
            print(f"       Ch {ch:2d}: {freq/1e6:.1f} MHz "
                  f"(PFB bin {pfb_mapping[ch]:3d}, "
                  f"offset {offset/1e3:+.0f} kHz)")
        print()

        ##################################################
        # SDR Source — HackRF via osmocom
        ##################################################
        self.source = osmosdr.source(args="hackrf=0")
        self.source.set_sample_rate(samp_rate)
        self.source.set_center_freq(center_freq)
        self.source.set_freq_corr(0)        # GPSDO — no correction needed
        self.source.set_dc_offset_mode(0)
        self.source.set_iq_balance_mode(0)
        self.source.set_gain_mode(False)
        self.source.set_gain(0)              # RF gain (not used on HackRF)
        self.source.set_if_gain(lna_gain)    # LNA
        self.source.set_bb_gain(vga_gain)    # VGA
        self.source.set_bandwidth(samp_rate)

        ##################################################
        # Polyphase channelizer
        ##################################################
        # Design prototype lowpass filter for the PFB
        # The PFB internally handles the per-channel filtering
        pfb_taps = grfilter.firdes.low_pass_2(
            1.0,                        # gain
            n_pfb_chans,                # sampling rate (normalized)
            0.5,                        # cutoff (half channel)
            0.1,                        # transition (fraction of channel)
            60,                         # stopband attenuation dB
            fft_window.WIN_BLACKMAN_HARRIS,
        )
        print(f"[INFO] PFB prototype filter: {len(pfb_taps)} taps "
              f"({len(pfb_taps) // n_pfb_chans} per channel)")

        self.channelizer = pfb.channelizer_ccf(
            n_pfb_chans,
            pfb_taps,
            1.0,  # oversampling ratio
        )

        # Connect source to channelizer
        self.connect(self.source, self.channelizer)

        ##################################################
        # Per-channel: resample → LoRa Rx
        ##################################################
        # Null sinks for unused PFB outputs
        self.null_sinks = []
        used_pfb_indices = set(pfb_mapping.values())

        for i in range(n_pfb_chans):
            if i not in used_pfb_indices:
                ns = blocks.null_sink(gr.sizeof_gr_complex)
                self.connect((self.channelizer, i), ns)
                self.null_sinks.append(ns)

        # Rational resampler taps: 200 kHz → 250 kHz (5/4)
        resamp_taps = grfilter.firdes.low_pass(
            1.0,
            lora_samp_rate,         # output rate
            US915_BW / 2 * 1.1,    # cutoff ~69 kHz
            US915_BW / 4,           # transition 31 kHz
            fft_window.WIN_HAMMING,
        )
        print(f"[INFO] Resampler filter: {len(resamp_taps)} taps "
              f"(200 kHz → {lora_samp_rate/1e3:.0f} kHz)")

        self.receivers = []
        for ch in valid_channels:
            pfb_idx = pfb_mapping[ch]
            ch_freq = us915_freq(ch)

            # Rational resampler 200 kHz → 250 kHz
            resampler = grfilter.rational_resampler_ccf(
                interpolation=5,
                decimation=4,
                taps=resamp_taps,
            )

            # gr-lora_sdr hierarchical receiver block
            # print_rx disabled — we collect the output stream ourselves
            lora_rx = lora_sdr.lora_sdr_lora_rx(
                center_freq=int(ch_freq),
                bw=int(US915_BW),
                sf=sf,
                cr=cr,
                samp_rate=lora_samp_rate,
                soft_decoding=False,
                pay_len=255,
                has_crc=True,
                impl_head=False,
                ldro_mode=2,
                print_rx=[False, False],  # suppress built-in printing
                sync_word=[sync_word],
            )

            # Frame collector — assembles decoded bytes into complete frames
            # using stream tags from gr-lora_sdr's crc_verif block
            collector = FrameCollector(
                channel=ch,
                freq=ch_freq,
                parse=True,
                hex_only=hex_only,
                log_file=self.log_file,
            )

            # Connect: channelizer output → resampler → lora_rx → collector
            self.connect((self.channelizer, pfb_idx), resampler)
            self.connect(resampler, lora_rx)
            self.connect(lora_rx, collector)

            self.receivers.append({
                "channel": ch,
                "freq": ch_freq,
                "pfb_idx": pfb_idx,
                "resampler": resampler,
                "lora_rx": lora_rx,
                "collector": collector,
            })

        print(f"[INFO] {len(self.receivers)} receivers ready, "
              f"{len(self.null_sinks)} PFB outputs nulled")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-channel US915 LoRaWAN sniffer for HackRF + gr-lora_sdr"
    )
    parser.add_argument(
        "--center-freq", type=float, default=908.5e6,
        help="HackRF center frequency in Hz (default: 908.5e6, aligned to US915 grid)"
    )
    parser.add_argument(
        "--samp-rate", type=float, default=20e6,
        help="Sample rate in Hz (default: 20e6 = 20 MHz)"
    )
    parser.add_argument(
        "--channels", type=str, default=None,
        help="Comma-separated US915 channel numbers 0-63 "
             "(default: all 64 channels)"
    )
    parser.add_argument(
        "--sf", type=int, default=7, choices=range(7, 13),
        help="Spreading factor (default: 7)"
    )
    parser.add_argument(
        "--cr", type=int, default=1, choices=range(1, 5),
        help="Coding rate: 1=4/5, 2=4/6, 3=4/7, 4=4/8 (default: 1)"
    )
    parser.add_argument(
        "--sync-word", type=lambda x: int(x, 0), default=0x34,
        help="Sync word: 0x34=LoRaWAN, 0x12=private/Meshtastic (default: 0x34)"
    )
    parser.add_argument(
        "--lna-gain", type=int, default=16,
        help="HackRF LNA (IF) gain in dB (default: 16)"
    )
    parser.add_argument(
        "--vga-gain", type=int, default=16,
        help="HackRF VGA (BB) gain in dB (default: 16)"
    )
    parser.add_argument(
        "--hex-only", action="store_true",
        help="Print raw hex only, skip LoRaWAN parsing"
    )
    parser.add_argument(
        "--log", type=str, default=None,
        help="Append raw hex frames to this log file "
             "(tab-separated: timestamp, channel, freq, hex)"
    )
    args = parser.parse_args()

    channels = None
    if args.channels:
        channels = [int(c.strip()) for c in args.channels.split(",")]

    tb = LoRaWANSniffer(
        center_freq=args.center_freq,
        samp_rate=args.samp_rate,
        channels=channels,
        sf=args.sf,
        cr=args.cr,
        sync_word=args.sync_word,
        lna_gain=args.lna_gain,
        vga_gain=args.vga_gain,
        hex_only=args.hex_only,
        log_path=args.log,
    )

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    print("[INFO] Starting flowgraph... press Ctrl+C to stop")
    tb.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()
        print("\n[INFO] Stopped.")


if __name__ == "__main__":
    main()
