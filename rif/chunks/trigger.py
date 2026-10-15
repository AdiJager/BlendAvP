"""VIOBJECT, VOBJPROP and the death volume — the first single-player trigger.

A virtual object is a trigger the level editor places without any geometry: it
carries a position, a size, a name and a strategy, and the engine turns it into
a behaviour while loading. `Objsetup.cpp:3635` walks them and dispatches on the
strategy type; `StratDeathVolume` (77) becomes a kill volume there.

Three pieces, all verified against retail before being written:

    VIOBJECT   12 B  an empty container; `Virtual_Object_Chunk` overrides
                     neither `size_chunk` nor `fill_data_block`, so it is a
                     header followed by its children.
    VOBJPROP   32 B plus the padded name. All 2062 retail payloads match that,
                     and the ID is not invented:

                         md5_buffer("VirtOb" + name, ...);
                         buffer[7] = 0;
                         ID = *(ObjectID*)&buffer[0];

                     which reproduces all 2062 stored IDs.
    AVPSTRAT   56 B  for `DeathVolumeStrategy`: the base 12, the volume box,
                     flags, damage and a pad. All 57 retail payloads match.

The name matters twice: `CalculateID` derives the id from it, so an object's
identity is a function of its name and nothing else.
"""

from __future__ import annotations

import hashlib
import struct

from ..chunk_writer import Chunk, ChunkWithChildren

# DeathVolumeStrategy flags, from Strachnk.hpp:854.
DEATH_VOLUME_STARTS_ON = 0x00000001
DEATH_VOLUME_COLLISION_NOT_REQUIRED = 0x00000002

# StrategyType values this module writes.
STRAT_DEATH_VOLUME = 77


def virtual_object_id(name):
    """Return the ObjectID the engine computes for a virtual object.

    `Virtual_Object_Properties_Chunk::CalculateID` hashes the literal prefix
    "VirtOb" glued to the name, zeroes the eighth byte, and takes what is left.
    Retail's 2062 stored ids all match, so this is reproduction rather than a
    guess -- and it is why writing a virtual object is possible at all: the id
    is derived, not supplied.
    """
    digest = bytearray(hashlib.md5(
        ("VirtOb" + str(name)).encode("latin-1")).digest())
    digest[7] = 0
    return bytes(digest[:8])


class VirtualObjectPropertiesChunk(Chunk):
    """VOBJPROP — where a virtual object is and what it is called.

    Payload from `STRACHNK.CPP:397`:

        location   3 x i32   position, in millimetres
        size       i32       the default extent, 1000 in the engine's own
                             constructor; the trigger's real box lives in the
                             strategy, so this is a display size
        name       NUL, padded to 4
        ID         8 B       derived from the name, see `virtual_object_id`
        pad1, pad2 2 x i32
    """

    identifier = "VOBJPROP"

    def __init__(self, name, location=(0, 0, 0), size=1000, pad1=0, pad2=0):
        self.name = str(name)
        self.location = tuple(int(v) for v in location)
        self.size = int(size)
        self.pad1 = int(pad1)
        self.pad2 = int(pad2)

    @property
    def object_id(self):
        """Return the id the engine derives from this name."""
        return virtual_object_id(self.name)

    def payload(self) -> bytes:
        """Serialise the properties."""
        buf = bytearray()
        buf += struct.pack("<3i", *self.location)
        buf += struct.pack("<i", self.size)
        raw = self.name.encode("latin-1", "replace") + b"\x00"
        while len(raw) % 4:
            raw += b"\x00"
        buf += raw
        buf += self.object_id
        buf += struct.pack("<2i", self.pad1, self.pad2)
        return bytes(buf)


class DeathVolumeStrategyChunk(Chunk):
    """AVPSTRAT — a kill volume, as a 56-byte record.

    `DeathVolumeStrategy` (Strachnk.hpp:856) holds its own axis-aligned box in
    world millimetres, so a kill volume needs no geometry and no module: the
    box is the whole of it.

    `damage` is damage PER SECOND, and zero means `certainDeath` -- the engine
    picks between them at `bh_deathvol.c:127`. Zero is what retail uses on 53
    of its 57 volumes, so the default here kills outright.

    The volume only harms creatures and players; the behaviour checks the
    strategy type of everything in the box first, so scenery inside it is
    untouched.
    """

    identifier = "AVPSTRAT"

    def __init__(self, low, high, damage=0,
                 flags=DEATH_VOLUME_STARTS_ON, pad2=0):
        self.low = tuple(int(v) for v in low)
        self.high = tuple(int(v) for v in high)
        self.damage = int(damage)
        self.flags = int(flags)
        self.pad2 = int(pad2)

    def payload(self) -> bytes:
        """Serialise the 56-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                  # index, spare2
        buf += struct.pack("<i", STRAT_DEATH_VOLUME)     # +8  StrategyType
        buf += struct.pack("<i", 0)                      # +12 Type
        buf += struct.pack("<i", 0)                      # +16 ExtraData
        buf += struct.pack("<3i", *self.low)             # +20 volume_min
        buf += struct.pack("<3i", *self.high)            # +32 volume_max
        buf += struct.pack("<i", self.flags)             # +44
        buf += struct.pack("<i", self.damage)            # +48
        buf += struct.pack("<i", self.pad2)              # +52
        assert len(buf) == 56, len(buf)
        return bytes(buf)


class VirtualObjectChunk(ChunkWithChildren):
    """VIOBJECT — a trigger with no geometry, holding a strategy and a position."""

    identifier = "VIOBJECT"

    def __init__(self, name, strategy, location=(0, 0, 0), size=1000):
        self.name = str(name)
        self.properties = VirtualObjectPropertiesChunk(
            self.name, location=location, size=size)
        # Properties first, strategy second: the order every one of SUBWAY's
        # nine volumes uses. Children are found by identifier, so this should
        # not matter -- which is why it is worth matching rather than arguing.
        super().__init__(self.identifier, [self.properties, strategy])


class DeathVolume:
    """A kill volume, as one object to place and export.

    The box is given in world millimetres, matching the strategy on disk; the
    name is what the id is derived from, so it must be unique within a level.

    `collision_required` defaults to off, unlike the engine's constructor. An on
    volume is skipped when the object did not collide that frame
    (`bh_deathvol.c:79`), and a player falling into a pit collides with nothing
    -- so the engine's generic default is the wrong one for the case a kill
    volume is usually placed for. Retail clears the flag on 10 of its 57.
    """

    def __init__(self, name, low, high, damage=0, starts_on=True,
                 collision_required=False):
        self.name = str(name)
        self.low = tuple(int(v) for v in low)
        self.high = tuple(int(v) for v in high)
        self.damage = int(damage)
        self.starts_on = bool(starts_on)
        self.collision_required = bool(collision_required)

    def chunk(self):
        """Return the VIOBJECT to write into SPECLOBJ."""
        flags = 0
        if self.starts_on:
            flags |= DEATH_VOLUME_STARTS_ON
        if not self.collision_required:
            flags |= DEATH_VOLUME_COLLISION_NOT_REQUIRED
        centre = tuple((self.low[i] + self.high[i]) // 2 for i in range(3))
        span = max(self.high[i] - self.low[i] for i in range(3))
        return VirtualObjectChunk(
            self.name,
            DeathVolumeStrategyChunk(self.low, self.high, self.damage, flags),
            location=centre,
            size=max(span, 1),
        )
