"""AVPENVIR — the level's sky and its per-level AVP weapon settings.

`AVP_Environment_Settings_Chunk` (AVPCHUNK.CPP:610). Verified against all 57
retail chunks: every one has a 24-byte payload whose first field declares 24.

    data_size           i32   the payload size, and a VERSION TAG: the loader
                              fills in defaults for anything below 16 (sky
                              colour) or 24 (the weapon bits)
    sky_colour_red      i32
    sky_colour_green    i32
    sky_colour_blue     i32
    bits                u32   predator_pistol, predator_plasmacaster,
                              predator_disc, predator_grappling_hook,
                              predator_medicomp, marine_jetpack, stars_in_sky,
                              then 25 spare bits -- in that order
    predator_num_spears i32

The sky is a COLOUR, not a texture. Retail's most common value is (30, 30, 30),
with the loader's own default of (200, 200, 200) next and pure black third.
`stars_in_sky` is set on 25 of the 57 chunks.
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# The payload size this writer produces, and the revision retail ships.
SETTINGS_BYTES = 24

# Loader defaults, from AVPCHUNK.CPP:623 and :629.
DEFAULT_SKY_COLOUR = (200, 200, 200)
DEFAULT_SPEARS = 30

# Bit positions in the packed word, in declaration order (AVPCHUNK.HPP:268).
BIT_PREDATOR_PISTOL = 0
BIT_PREDATOR_PLASMACASTER = 1
BIT_PREDATOR_DISC = 2
BIT_PREDATOR_GRAPPLING_HOOK = 3
BIT_PREDATOR_MEDICOMP = 4
BIT_MARINE_JETPACK = 5
BIT_STARS_IN_SKY = 6


def pack_bits(**flags):
    """Return the packed weapon word from named booleans."""
    names = {
        "predator_pistol": BIT_PREDATOR_PISTOL,
        "predator_plasmacaster": BIT_PREDATOR_PLASMACASTER,
        "predator_disc": BIT_PREDATOR_DISC,
        "predator_grappling_hook": BIT_PREDATOR_GRAPPLING_HOOK,
        "predator_medicomp": BIT_PREDATOR_MEDICOMP,
        "marine_jetpack": BIT_MARINE_JETPACK,
        "stars_in_sky": BIT_STARS_IN_SKY,
    }
    word = 0
    for name, position in names.items():
        if flags.get(name):
            word |= 1 << position
    return word


def unpack_bits(word):
    """Return the packed word as a dict of named booleans."""
    return {
        "predator_pistol": bool(word & (1 << BIT_PREDATOR_PISTOL)),
        "predator_plasmacaster": bool(word & (1 << BIT_PREDATOR_PLASMACASTER)),
        "predator_disc": bool(word & (1 << BIT_PREDATOR_DISC)),
        "predator_grappling_hook": bool(
            word & (1 << BIT_PREDATOR_GRAPPLING_HOOK)),
        "predator_medicomp": bool(word & (1 << BIT_PREDATOR_MEDICOMP)),
        "marine_jetpack": bool(word & (1 << BIT_MARINE_JETPACK)),
        "stars_in_sky": bool(word & (1 << BIT_STARS_IN_SKY)),
    }


class AVPEnvironmentChunk(Chunk):
    """AVPENVIR — the sky and the level's AVP settings, one per level."""

    identifier = "AVPENVIR"

    def __init__(self, sky_colour=DEFAULT_SKY_COLOUR, stars_in_sky=False,
                 predator_pistol=True, predator_plasmacaster=True,
                 predator_disc=True, predator_medicomp=True,
                 predator_grappling_hook=False, marine_jetpack=False,
                 predator_num_spears=DEFAULT_SPEARS):
        red, green, blue = (int(v) for v in sky_colour)
        self.sky_colour = (red, green, blue)
        self.predator_num_spears = int(predator_num_spears)
        # The loader's own defaults, so a level that says nothing still gets a
        # playable set of weapons rather than an unarmed Predator.
        self.bits = pack_bits(
            predator_pistol=predator_pistol,
            predator_plasmacaster=predator_plasmacaster,
            predator_disc=predator_disc,
            predator_medicomp=predator_medicomp,
            predator_grappling_hook=predator_grappling_hook,
            marine_jetpack=marine_jetpack,
            stars_in_sky=stars_in_sky)

    @property
    def flags(self):
        """Return the packed word as named booleans."""
        return unpack_bits(self.bits)

    def payload(self) -> bytes:
        """Serialise the 24-byte record, `data_size` first."""
        buf = bytearray()
        buf += struct.pack("<i", SETTINGS_BYTES)
        buf += struct.pack("<3i", *self.sky_colour)
        buf += struct.pack("<I", self.bits & 0xFFFFFFFF)
        buf += struct.pack("<i", self.predator_num_spears)
        assert len(buf) == SETTINGS_BYTES, len(buf)
        return bytes(buf)
