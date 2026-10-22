"""AVPSTRAT for a fan — `FanStrategy`, strategy type 75.

A fan is a placed object that pushes things: no geometry of its own, no
collision, just a direction and a strength. The direction does not live here --
`add_fan` reads it from the first section of the object's `OBJTRAK2`
(`Objsetup.cpp:1190`) -- so a fan without a track is not merely down a perk, it
trips `GLOBALASSERT(track)` at `Objsetup.cpp:1159`.

Layout, `Strachnk.hpp:836-852`:

    + 0  index             i32
    + 4  spare2            i32
    + 8  StrategyType      i32   = 75
    +12  Type              i32
    +16  ExtraData         i32
    +20  speed_up_time     i32   16.16 SECONDS
    +24  slow_down_time    i32   16.16 SECONDS
    +28  fan_wind_strength i32   per mille
    +32  pad2              i32

The first 20 bytes are `AvpStrat`, the same base every strategy uses.

Two units that are easy to get wrong, both from `Objsetup.cpp:1187-1195`:

* **`speed_up_time` is seconds in 16.16.** The loader does
  `DIV_FIXED(ONE_FIXED, speed_up_time)`, so `655360` means ten seconds and
  produces a spin-up multiplier of about 0.1 per frame. Writing `10` there would
  ask for `DIV_FIXED(65536, 10)` and reach full speed instantly.
* **`fan_wind_strength` is per mille.** The loader does
  `(strength * 65536) / 100`, so `100` is full strength and `-10` is a tenth of
  it blowing the other way. Anything outside +-1000 is replaced with 1.0.

Retail's 95 fans all use `655360` for both times -- ten seconds up and ten down
-- and strengths between 0 and +-10.0. A few carry uninitialised garbage in
`pad2` and in the strength, which is why the verifier counts those separately
instead of pretending they are reproducible.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_FAN = 75

FAN_PAYLOAD_BYTES = 36
ONE_FIXED = 1 << 16

# The one value every retail fan uses: ten seconds to spin up and to slow down.
DEFAULT_FAN_SECONDS = 10.0
DEFAULT_FAN_STRENGTH = 100
# The loader treats anything outside this range as "full strength".
MAX_FAN_STRENGTH = 1000


def _seconds_to_fixed(seconds, what):
    """Convert a duration in seconds to the file's 16.16 fixed point."""
    value = int(round(float(seconds) * ONE_FIXED))
    if value < 0 or value > 0x7FFFFFFF:
        raise ValueError(
            f"{what} is {seconds} s, which does not fit the 16.16 field")
    return value


class FanStrategyChunk(Chunk):
    """AVPSTRAT — a fan, as a 36-byte record."""

    identifier = "AVPSTRAT"

    def __init__(self, speed_up_seconds=DEFAULT_FAN_SECONDS,
                 slow_down_seconds=DEFAULT_FAN_SECONDS,
                 strength=DEFAULT_FAN_STRENGTH,
                 index=0, spare2=0, type_=0, extra_data=0, pad2=0):
        self.speed_up_seconds = float(speed_up_seconds)
        self.slow_down_seconds = float(slow_down_seconds)
        self.strength = int(strength)
        self.index = int(index)
        self.spare2 = int(spare2)
        self.type_ = int(type_)
        self.extra_data = int(extra_data)
        self.pad2 = int(pad2)

        if not -MAX_FAN_STRENGTH <= self.strength <= MAX_FAN_STRENGTH:
            # The loader silently substitutes full strength, so a value outside
            # the range would not be exported as the artist asked for.
            raise ValueError(
                f"fan strength {self.strength} is outside +-{MAX_FAN_STRENGTH}; "
                f"the engine would replace it with full strength")

    def payload(self) -> bytes:
        """Serialise the 36-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.spare2)
        buf += struct.pack("<i", STRAT_FAN)
        buf += struct.pack("<i", self.type_)
        buf += struct.pack("<i", self.extra_data)
        buf += struct.pack(
            "<3i",
            _seconds_to_fixed(self.speed_up_seconds, "speed_up_time"),
            _seconds_to_fixed(self.slow_down_seconds, "slow_down_time"),
            self.strength)
        buf += struct.pack("<i", self.pad2)
        assert len(buf) == FAN_PAYLOAD_BYTES, len(buf)
        return bytes(buf)
