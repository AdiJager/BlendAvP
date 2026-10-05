"""LIGHTSET and its children — level light set chunks + SHPVTINT.

Verified against Ltchunk.hpp and battle.rif:

    LIGHTSET (container)
      ├─ LTSETHDR   (8-char set name + 4 B pad = 12 B payload)
      ├─ LITSCALE   (44 B payload: i f i i f i i + 4 spare i32)
      ├─ AMBIENCE   (4 B payload)
      └─ STDLIGHT×N (84 B payload per light)

Shape_Vertex_Intensities_Chunk (SHPVTINT) lives in Ltchunk.hpp and is a
child of RBOBJECT (NOT REBSHAPE) — see battle.rif. It stores per-vertex
prelight values for one named light set. Zero-filled = ambient only.

STDLIGHT orientation is a 9×i32 row-major matrix in 16.16 fixed point
(identity = 65536 on the diagonal). Colour is packed 0x00BBGGRR.
"""
import struct

from ..chunk_writer import Chunk, ChunkWithChildren


IDENTITY_MATRIX_16_16 = (
    65536, 0, 0,
    0, 65536, 0,
    0, 0, 65536,
)

DEFAULT_AMBIENCE = 128
DEFAULT_BRIGHTNESS = 255
DEFAULT_SPREAD = 360
DEFAULT_RANGE = 2000
DEFAULT_COLOUR = 0x00FFFFFF

# A single fallback light used when the caller supplies none. Positioned
# 1 m above world origin with 10 m range, bright white, full spread.
DEFAULT_LIGHT_SPECS = (
    {
        "location": (0.0, 0.0, 1.0),
        "orientation": IDENTITY_MATRIX_16_16,
        "brightness": 255,
        "spread": 360,
        "range": 10000,
        "colour": 0x00FFFFFF,
        "engine_flags": 0,
        "local_flags": 0,
    },
)


class LightSetChunk(ChunkWithChildren):
    """LIGHTSET — container for a named light set (usually 'NORMALLT')."""

    def __init__(self, children=None):
        super().__init__("LIGHTSET", children)


class LightSetHeaderChunk(Chunk):
    """LTSETHDR — 8-char set name + 4-byte pad."""

    identifier = "LTSETHDR"

    def __init__(self, name: str = "NORMALLT", pad: int = 0):
        self.name = name
        self.pad = pad

    def payload(self) -> bytes:
        """Serialise 8-char name + i32 pad = 12 bytes."""
        raw = self.name.encode("ascii")[:8].ljust(8, b"\0")
        return raw + struct.pack("<i", self.pad)


class AmbienceChunk(Chunk):
    """AMBIENCE — ambient light intensity applied to all shapes in the set."""

    identifier = "AMBIENCE"

    def __init__(self, ambience: int = DEFAULT_AMBIENCE):
        self.ambience = ambience

    def payload(self) -> bytes:
        """Serialise one i32 ambience level."""
        return struct.pack("<i", self.ambience)


class LightScaleChunk(Chunk):
    """LITSCALE — prelight / runtime intensity multipliers (44 B payload).

    Field layout from Ltchunk.hpp::Light_Scale_Chunk:
        i32 LightMode
        f32 prelight_multiply
        i32 prelight_multiply_above
        i32 prelight_add
        f32 runtime_multiply
        i32 runtime_multiply_above
        i32 runtime_add
        i32 spare1, spare2, spare3, spare4

    The mixed int/float layout is easy to get wrong — the original
    S6 Faza 1 code emitted 40 bytes because it used 6 floats in a row.
    """

    identifier = "LITSCALE"

    def __init__(self, light_mode: int = 0,
                 prelight_multiply: float = 1.0,
                 prelight_multiply_above: int = 0,
                 prelight_add: int = 0,
                 runtime_multiply: float = 1.0,
                 runtime_multiply_above: int = 0,
                 runtime_add: int = 0,
                 spare1: int = 0, spare2: int = 0,
                 spare3: int = 0, spare4: int = 0):
        self.light_mode = light_mode
        self.prelight_multiply = prelight_multiply
        self.prelight_multiply_above = prelight_multiply_above
        self.prelight_add = prelight_add
        self.runtime_multiply = runtime_multiply
        self.runtime_multiply_above = runtime_multiply_above
        self.runtime_add = runtime_add
        self.spare1 = spare1
        self.spare2 = spare2
        self.spare3 = spare3
        self.spare4 = spare4

    def payload(self) -> bytes:
        """Serialise i-f-i-i-f-i-i + 4×i32 = 44-byte payload."""
        return struct.pack(
            "<ifiifiiiiii",
            self.light_mode,
            self.prelight_multiply,
            self.prelight_multiply_above,
            self.prelight_add,
            self.runtime_multiply,
            self.runtime_multiply_above,
            self.runtime_add,
            self.spare1,
            self.spare2,
            self.spare3,
            self.spare4,
        )


class StandardLightChunk(Chunk):
    """STDLIGHT — one light record (point or directional), 84-byte payload.

    Layout from Ltchunk.hpp::Light_Data:
        i32 light_number
        ChunkVectorInt location       (3 × i32, RIF mm)
        ChunkMatrix    orientation    (9 × i32, 16.16 fixed, row-major)
        i32 brightness / spread / range / colour
        i32 engine_light_flags / local_light_flags / pad1 / pad2
    """

    identifier = "STDLIGHT"

    def __init__(self, *, light_number: int = 0, location=(0, 0, 0),
                 orientation=None, brightness: int = DEFAULT_BRIGHTNESS,
                 spread: int = DEFAULT_SPREAD, range: int = DEFAULT_RANGE,
                 colour: int = DEFAULT_COLOUR, engine_light_flags: int = 0,
                 local_light_flags: int = 0, pad1: int = 0, pad2: int = 0):
        self.light_number = light_number
        self.location = tuple(location)
        self.orientation = (tuple(orientation)
                            if orientation is not None
                            else IDENTITY_MATRIX_16_16)
        self.brightness = brightness
        self.spread = spread
        self.range = range
        self.colour = colour
        self.engine_light_flags = engine_light_flags
        self.local_light_flags = local_light_flags
        self.pad1 = pad1
        self.pad2 = pad2

    def payload(self) -> bytes:
        """Serialise the 84-byte light record."""
        buf = bytearray()
        buf += struct.pack("<i", self.light_number)
        buf += struct.pack("<3i", *self.location)
        buf += struct.pack("<9i", *self.orientation)
        buf += struct.pack(
            "<8i",
            self.brightness, self.spread, self.range, self.colour,
            self.engine_light_flags, self.local_light_flags,
            self.pad1, self.pad2,
        )
        return bytes(buf)


class ShapeVertexIntensitiesChunk(Chunk):
    """SHPVTINT — per-vertex prelight intensities for one RBOBJECT.

    Layout from Ltchunk.hpp::Shape_Vertex_Intensities_Chunk:
        char light_set_name[8]
        i32  pad
        i32  num_vertices
        i32  intensity_array[num_vertices]

    `num_vertices` must match the count in the linked REBSHAPE's
    SHPHEAD1. Values pack per-channel RGB into low 24 bits
    (approximately R | G<<8 | B<<16). Zero-filled arrays are legal and
    yield ambient-only shading — used as our safe default.

    Despite living in Ltchunk.hpp in the source tree, this chunk is a
    direct child of RBOBJECT, alongside OBJHEAD1 / OBINTDT / MODULEDT.
    """

    identifier = "SHPVTINT"
    DEFAULT_LIGHT_SET = "NORMALLT"

    def __init__(self, light_set_name: str = DEFAULT_LIGHT_SET,
                 num_vertices: int = 0, intensities=None):
        self.light_set_name = light_set_name
        self.num_vertices = int(num_vertices)
        if intensities is None:
            intensities = [0] * self.num_vertices
        if len(intensities) != self.num_vertices:
            raise ValueError(
                "intensities length must match num_vertices "
                f"({len(intensities)} != {self.num_vertices})"
            )
        self.intensities = list(intensities)

    def payload(self) -> bytes:
        """Serialise 8 B name + pad + count + N × i32 = 16 + 4N bytes."""
        buf = bytearray()
        buf += self.light_set_name.encode("ascii")[:8].ljust(8, b"\0")
        buf += struct.pack("<i", 0)
        buf += struct.pack("<i", self.num_vertices)
        if self.num_vertices:
            buf += struct.pack(f"<{self.num_vertices}i", *self.intensities)
        return bytes(buf)