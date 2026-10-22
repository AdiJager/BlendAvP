"""`PLOBJLIT` — a placed light, as a 64-byte record.

A lamp is a placed object with real geometry, not an invisible volume: all 2846
in the shipped levels carry vertices, the commonest twelve or sixteen. The object
is the lamp fitting; the light comes out of the object's **origin**
(`Objsetup.cpp:1734`), and a polygon flagged `iflag_light_corona` adds a flare at
its own centre (`bh_light.c:193`).

Layout, `LTCHUNK.HPP:55-80` and `LTCHUNK.CPP:465-480`:

    + 0  brightness         i32   16.16, 65536 = 1.0
    + 4  spread             i32   always 0 in retail -- the light is omni
    + 8  range              i32   millimetres
    +12  up_colour          i32   0x00RRGGBB
    +16  down_colour        i32   0x00RRGGBB, the colour when off
    +20  engine_light_flags i32   0x804 in 83% -- believed to be runtime state
    +24  local_light_flags  i32   0x50 = Runtime | NoPreLight
    +28  fade_up_time       i32   milliseconds
    +32  fade_down_time     i32   milliseconds
    +36  up_time            i32   milliseconds
    +40  down_time          i32   milliseconds
    +44  start_time         i32   milliseconds
    +48  light_type         i32   0 Standard, 1 Strobe, 2 Flicker
    +52  on_off_type        i32   0 Standard, 1 Fade, 2 Flicker
    +56  flags              i32   0x1 On, 0x2 SwapColourBright, 0x4 NoSpecular
    +60  num_extra_data     i32   always 0 in retail

`local_flags = Runtime | NoPreLight` is the one field that is not a guess: a light
that has to go out when the lamp is shot must not be baked into the vertices, or
the wall would stay lit after the bulb is gone.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

PLACED_LIGHT_PAYLOAD_BYTES = 64
ONE_FIXED = 1 << 16

# `LTCHUNK.HPP:44-54`.
LIGHT_TYPE_STANDARD = 0
LIGHT_TYPE_STROBE = 1
LIGHT_TYPE_FLICKER = 2

ON_OFF_STANDARD = 0
ON_OFF_FADE = 1
ON_OFF_FLICKER = 2

PLACED_LIGHT_FLAG_ON = 0x00000001
PLACED_LIGHT_FLAG_SWAP_COLOUR = 0x00000002
PLACED_LIGHT_FLAG_NO_SPECULAR = 0x00000004

# `LTCHUNK.HPP:11-17`.
LO_FLAG_ON = 0x00000001
LO_FLAG_RUNTIME = 0x00000010
LO_FLAG_NO_PRELIGHT = 0x00000040

# The combination 2751 of 2846 retail lamps use.
DEFAULT_LOCAL_FLAGS = LO_FLAG_RUNTIME | LO_FLAG_NO_PRELIGHT
# Written to match retail. Its meaning is not understood: the bits do not
# correspond to any constant in LTCHUNK.HPP, and the value is identical in 83% of
# the lamps, which looks like runtime bookkeeping that the editor saved by
# accident. `0` is the honest alternative; `0x804` is the compatible one.
DEFAULT_ENGINE_FLAGS = 0x804


def pack_colour(red, green, blue):
    """Return a 0x00RRGGBB word from three 0..255 channels."""
    return ((int(red) & 0xFF) << 16) | ((int(green) & 0xFF) << 8) | (int(blue) & 0xFF)


def unpack_colour(word):
    """Return (red, green, blue) from a 0x00RRGGBB word."""
    word = int(word) & 0xFFFFFF
    return ((word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF)


class PlacedLightChunk(Chunk):
    """PLOBJLIT — one lamp's light."""

    identifier = "PLOBJLIT"

    def __init__(self, brightness=1.0, range_mm=10000,
                 up_colour=0xFFFFFF, down_colour=0x000000,
                 engine_light_flags=DEFAULT_ENGINE_FLAGS,
                 local_light_flags=DEFAULT_LOCAL_FLAGS,
                 fade_up_ms=0, fade_down_ms=0, up_ms=0, down_ms=0,
                 start_ms=0, light_type=LIGHT_TYPE_STANDARD,
                 on_off_type=ON_OFF_STANDARD, flags=PLACED_LIGHT_FLAG_ON,
                 num_extra_data=0):
        self.brightness = float(brightness)
        self.range_mm = int(range_mm)
        self.up_colour = int(up_colour)
        self.down_colour = int(down_colour)
        self.engine_light_flags = int(engine_light_flags)
        self.local_light_flags = int(local_light_flags)
        self.fade_up_ms = int(fade_up_ms)
        self.fade_down_ms = int(fade_down_ms)
        self.up_ms = int(up_ms)
        self.down_ms = int(down_ms)
        self.start_ms = int(start_ms)
        self.light_type = int(light_type)
        self.on_off_type = int(on_off_type)
        self.flags = int(flags)
        self.num_extra_data = int(num_extra_data)

    def payload(self) -> bytes:
        """Serialise the 64-byte record."""
        buf = bytearray()
        buf += struct.pack("<i", int(round(self.brightness * ONE_FIXED)))
        # `spread` is written as zero: every retail lamp is omni.
        buf += struct.pack("<2i", 0, self.range_mm)
        buf += struct.pack("<2i", self.up_colour, self.down_colour)
        buf += struct.pack("<2i", self.engine_light_flags,
                           self.local_light_flags)
        buf += struct.pack("<5i", self.fade_up_ms, self.fade_down_ms,
                           self.up_ms, self.down_ms, self.start_ms)
        buf += struct.pack("<3i", self.light_type, self.on_off_type,
                           self.flags)
        buf += struct.pack("<i", self.num_extra_data)
        assert len(buf) == PLACED_LIGHT_PAYLOAD_BYTES, len(buf)
        return bytes(buf)
