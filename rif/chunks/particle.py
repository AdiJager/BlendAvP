"""Particle generators — `PARGENER` with a `PARGENDA` inside it.

A generator is a `Chunk_With_Children` in `SPECLOBJ` (`Objsetup.cpp:3776`) whose
data chunk carries what the bible calls Frequency, Probability, Quantity, Speed
and the Active button, plus a position, an orientation and an optional indexed
sound.

The bible and the header agree name for name, which is worth saying because they
rarely do:

    Bible              AVPCHUNK.HPP:346
    Frequency          unsigned short time         // in tenths of a second
    Probability        unsigned short probability  // 0-100
    Speed              unsigned short speed        // in cm/second
    Quantity           unsigned short quantity
    Active Button      int flags                  // bit 0 is Inactive

The Active button is inverted: `ParticleGeneratorFlag_Inactive` is set to mean
the emitter is off, the same way `TextMessageFlag_NotActiveAtStart` works on a
message. All 570 retail generators have it clear.

There are four emitter types and their order is not the one you would guess:

    PARGEN_TYPE_SPARK       0    272 in retail
    PARGEN_TYPE_STEAM       1    178
    PARGEN_TYPE_BLACKSMOKE  2     55
    PARGEN_TYPE_FLAME       3     65

Only Spark and Flame light anything (`MakeLightElement`), and Steam and
BlackSmoke share a plain emission loop. There is nothing to infer: the enum has
to be read.

An emitter only runs while its module is VISIBLE (`bh_pargen.c:162`), which is
the opposite of an enemy generator. One placed where the player can never see it
does nothing, silently.
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# `bh_pargen.h:15`. The numbering is the enum's, not a preferred order.
PARGEN_TYPE_SPARK = 0
PARGEN_TYPE_STEAM = 1
PARGEN_TYPE_BLACKSMOKE = 2
PARGEN_TYPE_FLAME = 3

PARGEN_TYPE_NAMES = {
    PARGEN_TYPE_SPARK: "Spark",
    PARGEN_TYPE_STEAM: "Steam",
    PARGEN_TYPE_BLACKSMOKE: "Black smoke",
    PARGEN_TYPE_FLAME: "Flame",
}

# Only these two light the room (`MakeLightElement` in `bh_pargen.c:190`, `:200`).
PARGEN_TYPES_THAT_LIGHT = (PARGEN_TYPE_SPARK, PARGEN_TYPE_FLAME)

# `AVPCHUNK.HPP:344`. Set means inactive, so the panel's "Active" clears it.
PARGEN_FLAG_INACTIVE = 0x00000001

# The fixed part before the name: everything in the payload up to it.
PARGENDA_HEADER_BYTES = 68

# The bible calls probability a chance from 0 to 100. Retail's commonest value
# is 0, and `SPARK` tests `(FastRandom() & 0xffff) <= probability`, so zero is
# "almost never" rather than "always".
PARGEN_MAX_PROBABILITY = 100

# `speed` is in centimetres per second, so a metre a second is 100.
PARGEN_SPEED_PER_METRE = 100


def _pad_name(name: str) -> bytes:
    """Return a NUL-terminated name padded to a four-byte boundary.

    The length is rounded up past the terminator, the same rule the paths use,
    which is why a ten-character name produces twelve bytes.
    """
    raw = name.encode("latin-1") + b"\x00"
    while len(raw) % 4:
        raw += b"\x00"
    return raw


class ParticleGeneratorDataChunk(Chunk):
    """PARGENDA — type, emission settings, pose and name.

    `flags` bit 0 is `Inactive`, so `active` is stored in the negative here and
    turned round once, at serialisation, rather than at every call site.
    """

    identifier = "PARGENDA"

    def __init__(self, name, particle_type=PARGEN_TYPE_SPARK,
                 frequency_tenths=0, probability=0, speed=0, quantity=0,
                 object_id=b"\x00" * 8, parent_id=b"\x00" * 8,
                 location=(0, 0, 0), orientation=(0.0, 0.0, 0.0, 1.0),
                 active=True):
        self.name = str(name)
        self.particle_type = int(particle_type)
        self.frequency = int(frequency_tenths)
        self.probability = int(probability)
        self.speed = int(speed)
        self.quantity = int(quantity)
        self.object_id = bytes(object_id)
        self.parent_id = bytes(parent_id)
        self.location = tuple(int(v) for v in location)
        self.orientation = tuple(float(v) for v in orientation)
        self.active = bool(active)
        assert len(self.object_id) == 8, len(self.object_id)
        assert len(self.parent_id) == 8, len(self.parent_id)
        assert len(self.orientation) == 4, len(self.orientation)

    def payload(self) -> bytes:
        """Serialise the record: a fixed part, then the name."""
        buf = bytearray()
        buf += struct.pack("<i", self.particle_type)
        buf += struct.pack(
            "<4H",
            self.frequency & 0xFFFF,
            self.probability & 0xFFFF,
            self.speed & 0xFFFF,
            self.quantity & 0xFFFF,
        )
        buf += struct.pack("<2i", 0, 0)                  # spare1, spare2
        flags = 0 if self.active else PARGEN_FLAG_INACTIVE
        buf += struct.pack("<i", flags)
        buf += self.object_id                            # +24
        buf += self.parent_id                            # +32
        buf += struct.pack("<3i", *self.location)        # +40
        buf += struct.pack("<4f", *self.orientation)     # +52
        assert len(buf) == PARGENDA_HEADER_BYTES, len(buf)
        buf += _pad_name(self.name)                      # +68
        return bytes(buf)


class ParticleGeneratorChunk(Chunk):
    """PARGENER — the container, with its data and an optional sound."""

    identifier = "PARGENER"

    def __init__(self, data, sound=None, alternate_locations=None):
        # The sound arrives as bytes, or as anything with a `payload`. There is
        # no `INDSOUND` writer yet -- 444 of the 570 retail generators have one,
        # and it is this chunk's child, but knowing that is not the same as
        # having the layout. Taking it ready-made keeps the generator testable
        # now and leaves the sound to be attached when it exists.
        self.data = data
        self.sound = sound
        self.alternate_locations = alternate_locations

    def payload(self) -> bytes:
        """Serialise the children in the order retail writes them.

        The data chunk comes first and the sound second: that is how all 444
        retail generators that have one are laid out, and children are found by
        identifier, so matching costs nothing.
        """
        # `serialize`, not `payload`: a child is written with its identifier
        # and size, and `payload` is only the bytes inside it. Concatenating
        # payloads produced a container whose children had no names, which the
        # engine would read as an empty generator.
        parts = [self.data.serialize()]
        if self.sound is not None:
            parts.append(self.sound.serialize()
                         if hasattr(self.sound, "serialize") else self.sound)
        if self.alternate_locations is not None:
            parts.append(self.alternate_locations)
        return b"".join(parts)


class ParticleGenerator:
    """An emitter, as one object to place and export.

    A marker rather than a volume: the generator's own reach is the particles'
    speed and lifetime, not a box. What matters is the position, the type and
    the emission settings.
    """

    def __init__(self, name, position, particle_type=PARGEN_TYPE_SPARK,
                 frequency_seconds=0.0, probability=0, quantity=0,
                 speed_metres=0.0, active=True, sound_name=None,
                 sound_inner_mm=0, sound_outer_mm=0, sound_volume=127,
                 sound_loop=False, orientation=(0.0, 0.0, 0.0, 1.0)):
        self.name = str(name)
        self.position = tuple(int(v) for v in position)
        self.particle_type = int(particle_type)
        self.frequency_seconds = float(frequency_seconds)
        self.probability = int(probability)
        self.quantity = int(quantity)
        self.speed_metres = float(speed_metres)
        self.active = bool(active)
        self.sound_name = sound_name
        self.sound_inner_mm = int(sound_inner_mm)
        self.sound_outer_mm = int(sound_outer_mm)
        self.sound_volume = int(sound_volume)
        self.sound_loop = bool(sound_loop)
        # The direction the particles leave in. The engine turns this into a
        # matrix and rotates every particle's velocity by it, which is what
        # makes an emitter directional rather than a point that sprays.
        self.orientation = tuple(float(v) for v in orientation)

    def chunk(self):
        """Return the PARGENER to write into SPECLOBJ."""
        from .trigger import virtual_object_id

        # The sound is this chunk's child, and 444 of the 570 retail
        # generators have one, so a generator without its sound is a whole
        # emitter rather than a broken one -- but the hole is worth closing
        # wherever a name is given.
        sound = None
        if self.sound_name:
            from .indexed_sound import IndexedSoundChunk

            sound = IndexedSoundChunk(
                self.sound_name,
                inner_range=self.sound_inner_mm,
                outer_range=self.sound_outer_mm,
                max_volume=self.sound_volume,
                loop=self.sound_loop,
            )

        data = ParticleGeneratorDataChunk(
            name=self.name,
            particle_type=self.particle_type,
            # The field is tenths of a second, so a whole second is ten.
            frequency_tenths=int(round(self.frequency_seconds * 10.0)),
            probability=self.probability,
            quantity=self.quantity,
            # Centimetres per second, so a metre a second is a hundred.
            speed=int(round(self.speed_metres * PARGEN_SPEED_PER_METRE)),
            object_id=virtual_object_id(self.name),
            location=self.position,
            orientation=self.orientation,
            active=self.active,
        )
        return ParticleGeneratorChunk(data, sound)
