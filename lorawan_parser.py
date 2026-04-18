#!/usr/bin/env python3
"""
lorawan_parser.py — Decode LoRaWAN MAC layer frames from raw hex bytes.

Parses the LoRaWAN 1.0.x MAC frame structure per the LoRaWAN spec:
  MHDR | MACPayload | MIC
    |       |         |
    1 byte  N bytes   4 bytes

Where MACPayload for data frames is:
  FHDR | FPort | FRMPayload
    |     |         |
  7-22   0-1     variable
  bytes  byte    bytes

And FHDR is:
  DevAddr | FCtrl | FCnt | FOpts
     |       |      |       |
    4 bytes  1 byte 2 bytes 0-15 bytes

Cannot decrypt FRMPayload without AppSKey (AES-128-CTR) or verify MIC without
NwkSKey. But all MAC metadata is in cleartext and very useful for device
identification and traffic analysis.

Usage:
    # Parse a single hex string
    python3 lorawan_parser.py "40 11 22 33 44 80 00 01 02 ab cd ef 12 34 56 78"

    # Parse from stdin (pipe-friendly)
    echo "40112233448000010..." | python3 lorawan_parser.py -

    # Parse a file of hex strings, one per line
    python3 lorawan_parser.py --file captures.hex

    # JSON output
    python3 lorawan_parser.py --json "40 11 22..."
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field


# LoRaWAN MHDR MType field values (upper 3 bits of MHDR byte)
MTYPE_NAMES = {
    0b000: "Join Request",
    0b001: "Join Accept",
    0b010: "Unconfirmed Data Up",
    0b011: "Unconfirmed Data Down",
    0b100: "Confirmed Data Up",
    0b101: "Confirmed Data Down",
    0b110: "Rejoin Request",
    0b111: "Proprietary",
}

# Data frame MTypes (uplink + downlink, confirmed + unconfirmed)
DATA_MTYPES = {0b010, 0b011, 0b100, 0b101}
UPLINK_MTYPES = {0b010, 0b100}


@dataclass
class LoRaWANFrame:
    """Parsed LoRaWAN frame fields."""
    raw_hex: str
    length: int
    mhdr: int
    mtype: int
    mtype_name: str
    major: int
    rfu: int
    is_uplink: bool | None = None
    dev_addr: str | None = None
    dev_addr_raw: int | None = None
    fctrl: int | None = None
    adr: bool | None = None
    adr_ack_req: bool | None = None
    ack: bool | None = None
    fpending_or_classb: bool | None = None
    fopts_len: int | None = None
    fcnt: int | None = None
    fopts_hex: str | None = None
    fport: int | None = None
    frm_payload_hex: str | None = None
    frm_payload_len: int | None = None
    mic: str | None = None
    # Join Request specific
    join_eui: str | None = None
    dev_eui: str | None = None
    dev_nonce: int | None = None
    # Error tracking
    parse_error: str | None = None


def parse_hex_input(s: str) -> bytes:
    """Parse a flexible hex string: '40 11 22' or '401122' or '40-11-22' etc."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", s)
    if len(cleaned) % 2 != 0:
        raise ValueError(f"Hex string has odd length: {len(cleaned)} chars")
    return bytes.fromhex(cleaned)


def parse_lorawan_frame(data: bytes) -> LoRaWANFrame:
    """Parse a LoRaWAN frame from raw bytes."""
    frame = LoRaWANFrame(
        raw_hex=data.hex(" "),
        length=len(data),
        mhdr=0,
        mtype=0,
        mtype_name="",
        major=0,
        rfu=0,
    )

    # Minimum frame: MHDR(1) + MIC(4) = 5 bytes
    if len(data) < 5:
        frame.parse_error = f"Frame too short: {len(data)} bytes (min 5)"
        return frame

    # Parse MHDR
    frame.mhdr = data[0]
    frame.mtype = (frame.mhdr >> 5) & 0x07
    frame.rfu = (frame.mhdr >> 2) & 0x07
    frame.major = frame.mhdr & 0x03
    frame.mtype_name = MTYPE_NAMES.get(frame.mtype, f"Unknown ({frame.mtype})")
    frame.is_uplink = frame.mtype in UPLINK_MTYPES if frame.mtype in DATA_MTYPES else None

    # MIC is always last 4 bytes
    frame.mic = data[-4:].hex(" ")
    mac_payload = data[1:-4]

    if frame.mtype in DATA_MTYPES:
        _parse_data_frame(frame, mac_payload)
    elif frame.mtype == 0b000:  # Join Request
        _parse_join_request(frame, mac_payload)
    elif frame.mtype == 0b001:  # Join Accept (encrypted — can't parse cleartext)
        frame.parse_error = "Join Accept is encrypted; cannot parse without NwkKey"
    else:
        frame.parse_error = f"MType {frame.mtype} ({frame.mtype_name}) not parsed"

    return frame


def _parse_data_frame(frame: LoRaWANFrame, mac_payload: bytes):
    """Parse a data uplink/downlink MAC payload."""
    if len(mac_payload) < 7:
        frame.parse_error = f"MAC payload too short for FHDR: {len(mac_payload)} bytes"
        return

    # FHDR: DevAddr(4) | FCtrl(1) | FCnt(2) | FOpts(0-15)
    # DevAddr is little-endian on the wire
    frame.dev_addr_raw = int.from_bytes(mac_payload[0:4], "little")
    frame.dev_addr = f"{frame.dev_addr_raw:08X}"

    frame.fctrl = mac_payload[4]
    # FCtrl bits differ slightly for uplink vs downlink; here we interpret as uplink
    # Uplink FCtrl: ADR | ADRACKReq | ACK | ClassB | FOptsLen[3:0]
    # Downlink FCtrl: ADR | RFU | ACK | FPending | FOptsLen[3:0]
    frame.adr = bool(frame.fctrl & 0x80)
    frame.adr_ack_req = bool(frame.fctrl & 0x40) if frame.is_uplink else None
    frame.ack = bool(frame.fctrl & 0x20)
    frame.fpending_or_classb = bool(frame.fctrl & 0x10)
    frame.fopts_len = frame.fctrl & 0x0F

    frame.fcnt = int.from_bytes(mac_payload[5:7], "little")

    fopts_end = 7 + frame.fopts_len
    if fopts_end > len(mac_payload):
        frame.parse_error = (
            f"FOptsLen={frame.fopts_len} exceeds MAC payload "
            f"({len(mac_payload)} bytes)"
        )
        return
    frame.fopts_hex = mac_payload[7:fopts_end].hex(" ") if frame.fopts_len else ""

    # After FHDR: optional FPort(1) + FRMPayload
    remaining = mac_payload[fopts_end:]
    if len(remaining) == 0:
        # MAC-only frame, no FPort/FRMPayload
        frame.fport = None
        frame.frm_payload_hex = ""
        frame.frm_payload_len = 0
    else:
        frame.fport = remaining[0]
        frame.frm_payload_hex = remaining[1:].hex(" ")
        frame.frm_payload_len = len(remaining) - 1


def _parse_join_request(frame: LoRaWANFrame, mac_payload: bytes):
    """Parse a Join Request MAC payload."""
    # JoinEUI(8) | DevEUI(8) | DevNonce(2) — all little-endian on the wire
    if len(mac_payload) != 18:
        frame.parse_error = (
            f"Join Request payload has wrong length: {len(mac_payload)} "
            f"(expected 18)"
        )
        return

    # Reverse for display (big-endian hex is conventional for EUIs)
    frame.join_eui = bytes(reversed(mac_payload[0:8])).hex(":").upper()
    frame.dev_eui = bytes(reversed(mac_payload[8:16])).hex(":").upper()
    frame.dev_nonce = int.from_bytes(mac_payload[16:18], "little")


def format_fctrl_bits(frame: LoRaWANFrame) -> str:
    """Pretty-print FCtrl flags."""
    if frame.fctrl is None:
        return ""
    flags = []
    if frame.adr:
        flags.append("ADR")
    if frame.adr_ack_req:
        flags.append("ADRACKReq")
    if frame.ack:
        flags.append("ACK")
    if frame.fpending_or_classb:
        flags.append("ClassB" if frame.is_uplink else "FPending")
    return ",".join(flags) if flags else "-"


def print_frame_report(frame: LoRaWANFrame):
    """Human-readable frame dump."""
    print("=" * 72)
    print(f"  Raw ({frame.length} bytes): {frame.raw_hex}")
    print("-" * 72)
    print(f"  MHDR:        0x{frame.mhdr:02X}")
    print(f"    MType:     {frame.mtype_name} ({frame.mtype:03b})")
    print(f"    Major:     {frame.major}")
    print(f"    RFU:       {frame.rfu}")

    if frame.parse_error:
        print(f"  [!] {frame.parse_error}")
        print()
        return

    if frame.mtype in DATA_MTYPES:
        print(f"  DevAddr:     {frame.dev_addr}")
        nwk_id = (frame.dev_addr_raw >> 25) & 0x7F
        nwk_addr = frame.dev_addr_raw & 0x01FFFFFF
        print(f"    NwkID:     0x{nwk_id:02X}")
        print(f"    NwkAddr:   0x{nwk_addr:07X}")
        print(f"  FCtrl:       0x{frame.fctrl:02X} [{format_fctrl_bits(frame)}]")
        print(f"    FOptsLen:  {frame.fopts_len}")
        print(f"  FCnt:        {frame.fcnt} (0x{frame.fcnt:04X})")
        if frame.fopts_len:
            print(f"  FOpts:       {frame.fopts_hex}")
        if frame.fport is not None:
            port_note = ""
            if frame.fport == 0:
                port_note = "  (MAC commands)"
            elif frame.fport == 224:
                port_note = "  (LoRaWAN reserved)"
            print(f"  FPort:       {frame.fport}{port_note}")
        if frame.frm_payload_len:
            print(f"  FRMPayload:  {frame.frm_payload_hex} "
                  f"({frame.frm_payload_len} bytes, encrypted)")

    elif frame.mtype == 0b000:  # Join Request
        print(f"  JoinEUI:     {frame.join_eui}")
        print(f"  DevEUI:      {frame.dev_eui}")
        print(f"  DevNonce:    0x{frame.dev_nonce:04X}")

    print(f"  MIC:         {frame.mic}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Parse LoRaWAN MAC frames from hex bytes"
    )
    parser.add_argument(
        "hex", nargs="?",
        help="Hex string to parse, or '-' to read from stdin"
    )
    parser.add_argument(
        "--file", "-f",
        help="Read hex strings from file, one frame per line"
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output as JSON instead of human-readable"
    )
    args = parser.parse_args()

    # Collect input sources
    hex_lines = []
    if args.file:
        with open(args.file) as f:
            hex_lines = [line.strip() for line in f if line.strip()
                         and not line.strip().startswith("#")]
    elif args.hex == "-":
        hex_lines = [line.strip() for line in sys.stdin if line.strip()]
    elif args.hex:
        hex_lines = [args.hex]
    else:
        parser.print_help()
        sys.exit(1)

    frames = []
    for line in hex_lines:
        try:
            data = parse_hex_input(line)
            frame = parse_lorawan_frame(data)
            frames.append(frame)
        except ValueError as e:
            print(f"[ERROR] Failed to parse '{line}': {e}", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(f) for f in frames], indent=2))
    else:
        for frame in frames:
            print_frame_report(frame)


if __name__ == "__main__":
    main()
