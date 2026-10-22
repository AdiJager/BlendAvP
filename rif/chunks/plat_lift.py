"""AVPSTRAT for a platform lift — `PlatLiftStrategy`, strategy type 54.

A platform lift is the one moving thing that is **not** a module: the bible is
explicit ("The lift object is now no longer a module and has become a placed
object"), and `Objsetup.cpp` treats it the same way a fan is treated -- its
position and orientation come from the object, and its travel comes from the
track. `strategy.htm` adds that the lift only needs the endpoint of a track, not
its sections.

Layout, `Strachnk.hpp:320-341`:

    + 0  index        i32
    + 4  spare2       i32
    + 8  StrategyType i32   = 54
    +12  Type         i32
    +16  ExtraData    i32
    +20  flags        i32
    +24  spare[5]     5 x i32

`PlatformLiftFlags_Disabled` (0x1) and `PlatformLiftFlags_OneUse` (0x2) are the
only flags the struct defines.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_PLAT_LIFT = 54

PLAT_LIFT_PAYLOAD_BYTES = 44

# `Strachnk.hpp:320-321`.
PLAT_LIFT_FLAG_DISABLED = 0x00000001
PLAT_LIFT_FLAG_ONE_USE = 0x00000002

# Five words the struct calls `spare`. They are carried through unchanged
# because a third of retail's lifts do not leave them at zero, and guessing at
# their meaning would be a change of behaviour rather than a translation.
PLAT_LIFT_SPARE_WORDS = 5


class PlatLiftStrategyChunk(Chunk):
    """AVPSTRAT — a platform lift, as a 44-byte record."""

    identifier = "AVPSTRAT"

    def __init__(self, flags=0, spare=None, index=0, spare2=0, type_=0,
                 extra_data=0):
        self.flags = int(flags)
        self.spare = list(spare or [0] * PLAT_LIFT_SPARE_WORDS)
        self.index = int(index)
        self.spare2 = int(spare2)
        self.type_ = int(type_)
        self.extra_data = int(extra_data)
        if len(self.spare) != PLAT_LIFT_SPARE_WORDS:
            raise ValueError(
                f"a platform lift carries exactly {PLAT_LIFT_SPARE_WORDS} "
                f"spare words, got {len(self.spare)}")

    def payload(self) -> bytes:
        """Serialise the 44-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.spare2)
        buf += struct.pack("<i", STRAT_PLAT_LIFT)
        buf += struct.pack("<i", self.type_)
        buf += struct.pack("<i", self.extra_data)
        buf += struct.pack("<i", self.flags)
        buf += struct.pack("<5i", *self.spare)
        assert len(buf) == PLAT_LIFT_PAYLOAD_BYTES, len(buf)
        return bytes(buf)
