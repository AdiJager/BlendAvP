"""SelfDestructStrategy — the level's escape sequence.

`StratSelfDestruct` (78) arms a countdown that destroys the level, and it is
armed the way a death volume is toggled: both are `active = state` in the same
enum (`BH_TYPES.C:2623` and `:2619`). So a switch turns it on, and the timer is
how long the player has -- which is what `Escape.RIF` and the levels that end
with a countdown are built from.

The record is fixed at 32 bytes of strategy. `GetStrategySize` returns
`AvpStrat::GetStrategySize() + 20`, so 12 + 20, and the payload adds the
eight-byte chunk header: 40, which is every one of the five retail instances.

    SelfDestructStrategy : public AvpStrat
    {
        int timer;   // in seconds
        int pad[4];
    };

The object is a marker, not a volume: there is no box in the strategy and
nothing to walk into. A switch arms it, and the level ends on its own.
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_SELF_DESTRUCT = 78

# Retail's five instances carry 0 or 70 seconds; 70 is the one that says what
# the field is for.
DEFAULT_SELF_DESTRUCT_TIMER = 70


class SelfDestructStrategyChunk(Chunk):
    """AVPSTRAT — a 78: a countdown, and four unused integers."""

    identifier = "AVPSTRAT"

    def __init__(self, timer_seconds=DEFAULT_SELF_DESTRUCT_TIMER):
        self.timer = int(timer_seconds)

    def payload(self) -> bytes:
        """Serialise the 40-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                 # index, spare2
        buf += struct.pack("<i", STRAT_SELF_DESTRUCT)
        buf += struct.pack("<2i", 0, 0)                 # Type, ExtraData
        buf += struct.pack("<i", self.timer)            # +20, in seconds
        # `pad[4]`. Retail leaves all four at zero; they are written because
        # `fill_data_block` writes them and the record's length depends on it.
        buf += struct.pack("<4i", 0, 0, 0, 0)           # +24 .. +36
        return bytes(buf)


class SelfDestruct:
    """A level's countdown, as one object to place and export.

    A marker: the strategy holds no volume, so what matters is the position the
    object sits at and the timer it carries. A switch arms it, and the level
    ends when the timer runs out.
    """

    def __init__(self, name, position, timer_seconds=DEFAULT_SELF_DESTRUCT_TIMER):
        self.name = str(name)
        self.position = tuple(int(v) for v in position)
        self.timer_seconds = int(timer_seconds)

    def chunk(self):
        """Return the VIOBJECT to write into SPECLOBJ."""
        from .objective import DEFAULT_OBJECTIVE_SIZE
        from .trigger import VirtualObjectChunk

        return VirtualObjectChunk(
            self.name,
            SelfDestructStrategyChunk(self.timer_seconds),
            location=self.position,
            size=DEFAULT_OBJECTIVE_SIZE,
        )
