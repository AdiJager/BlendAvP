"""AVPSTRAT for a door — `DoorStrategy`, strategy type 50.

A door is not a special kind of object. It is an ordinary module with a door
strategy attached: the shape comes from the geometry, and the strategy only says
how to move it. That is why doors can be built without a geometry importer.

Layout, `Strachnk.hpp:349` and confirmed against all 417 doors in the 36
shipped levels -- every one is a 40-byte chunk, so a 28-byte payload:

    + 0  index          i32
    + 4  spare2         i32
    + 8  StrategyType   i32   = 50
    +12  Type           i32
    +16  ExtraData      i32
    +20  DoorFlags      i32
    +24  TimeToOpen     u8    tenths of a second
    +25  TimeToClose    u8    tenths of a second
    +26  spare          i16

The first 20 bytes are `AvpStrat`, the same base every strategy uses; they match
`DeathVolumeStrategyChunk`, which is verified 57/57.

Watch the two numbers: the chunk's own `size` field is 40, the payload is 28.
Confusing them is easy and was worth one detour during the research.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_DOOR = 50

# `Strachnk.hpp:343-347`.
DOOR_FLAG_LOCKED = 0x00000001
DOOR_FLAG_PROXIMITY = 0x00000002
DOOR_FLAG_OPEN = 0x00000004
DOOR_FLAG_LIFT = 0x00000008
DOOR_FLAG_HORIZONTAL = 0x00000010

DOOR_PAYLOAD_BYTES = 28

# What the game actually uses. `flags=3` with one second to open and half a
# second to close is the commonest setup in retail -- 91 of 417 doors -- and
# `TimeToOpen` is 10 in 74% of them, `TimeToClose` 5 in 89%.
DEFAULT_DOOR_FLAGS = DOOR_FLAG_LOCKED | DOOR_FLAG_PROXIMITY
DEFAULT_OPEN_SECONDS = 1.0
DEFAULT_CLOSE_SECONDS = 0.5
# The field is a single byte of tenths, so ten seconds is the ceiling.
MAX_DOOR_SECONDS = 25.5


class DoorStrategyChunk(Chunk):
    """AVPSTRAT — a door, as a 28-byte record."""

    identifier = "AVPSTRAT"

    def __init__(self, door_flags=DEFAULT_DOOR_FLAGS,
                 open_seconds=DEFAULT_OPEN_SECONDS,
                 close_seconds=DEFAULT_CLOSE_SECONDS,
                 index=0, spare2=0, type_=0, extra_data=0, spare=0):
        self.door_flags = int(door_flags) & 0xFFFFFFFF
        self.open_seconds = float(open_seconds)
        self.close_seconds = float(close_seconds)
        self.index = int(index)
        self.spare2 = int(spare2)
        self.type_ = int(type_)
        self.extra_data = int(extra_data)
        self.spare = int(spare)

    @staticmethod
    def _tenths(seconds, what):
        """Return the field's byte value for a duration in seconds."""
        tenths = int(round(float(seconds) * 10.0))
        if tenths < 0 or tenths > 255:
            raise ValueError(
                f"{what} is {seconds} s; the field holds tenths in one byte, "
                f"so the range is 0 to {MAX_DOOR_SECONDS} s")
        return tenths

    def payload(self) -> bytes:
        """Serialise the 28-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.spare2)
        buf += struct.pack("<i", STRAT_DOOR)
        buf += struct.pack("<i", self.type_)
        buf += struct.pack("<i", self.extra_data)
        buf += struct.pack("<i", self.door_flags)
        buf += struct.pack(
            "<BBh",
            self._tenths(self.open_seconds, "TimeToOpen"),
            self._tenths(self.close_seconds, "TimeToClose"),
            self.spare)
        assert len(buf) == DOOR_PAYLOAD_BYTES, len(buf)
        return bytes(buf)
