"""A minimal FIT file writer for tests: file_id, sport and record messages.

Follows the FIT protocol (little-endian, one definition per local message
type, CRC-16 at the end) closely enough for fitdecode to read it back.
"""

import struct
from datetime import datetime, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ CRC_TABLE[nibble]
    return crc


def _definition(local: int, global_number: int, fields: list[tuple[int, int, int]]) -> bytes:
    header = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_number, len(fields))
    return header + b"".join(struct.pack("<BBB", *field) for field in fields)


def write_fit(points: list[dict], sport: int = 2) -> bytes:
    """points: dicts with time, lat, lon and optional ele, hr, power."""
    body = bytearray()
    # file_id: type (0) = activity (4).
    body += _definition(0, 0, [(0, 1, 0x00)]) + struct.pack("<BB", 0, 4)
    # sport: sport (field 0), 2 = cycling.
    body += _definition(1, 12, [(0, 1, 0x00)]) + struct.pack("<BB", 1, sport)
    # record: timestamp, position_lat, position_long, altitude, heart_rate, power.
    body += _definition(
        2, 20, [(253, 4, 0x86), (0, 4, 0x85), (1, 4, 0x85), (2, 2, 0x84), (3, 1, 0x02), (7, 2, 0x84)]
    )
    for p in points:
        altitude = 0xFFFF if p.get("ele") is None else round((p["ele"] + 500) * 5)
        body += struct.pack(
            "<BIiiHBH",
            2,
            int((p["time"] - FIT_EPOCH).total_seconds()),
            round(p["lat"] / 180 * 2**31),
            round(p["lon"] / 180 * 2**31),
            altitude,
            0xFF if p.get("hr") is None else p["hr"],
            0xFFFF if p.get("power") is None else p["power"],
        )
    header = struct.pack("<BBHI4s", 14, 0x10, 2093, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    data = header + bytes(body)
    return data + struct.pack("<H", crc16(data))
