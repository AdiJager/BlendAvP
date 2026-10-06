"""LIGHTSET and its children — level light set chunks + SHPVTINT.

Verified against Ltchunk.hpp / Ltchunk.cpp and battle.rif:

    LIGHTSET (container)
      ├─ LTSETHDR   (8-char set name + 4 B pad = 12 B payload)
      ├─ LITSCALE   (40 B payload: i f f f f f f i i i)
      ├─ AMBIENCE   (4 B payload)
      └─ STDLIGHT×N (84 B payload per light)

Shape_Vertex_Intensities_Chunk (SHPVTINT) lives in Ltchunk.hpp and is a
child of RBOBJECT (NOT REBSHAPE) — see battle.rif. It stores per-vertex
prelight values for one named light set.

Lighting model (verified in Kshape.c::VertexIntensity_Standard_Opt):

    redI/greenI/blueI = 0
    if shape has ShapeFlag_PreLit:
        redI/greenI/blueI <- SHPVTINT[vertexNumber]   (packed 0x00RRGGBB)
    for each light in LightSourcesForObject[]:
        redI/greenI/blueI += light contribution

The engine starts with an all-zero vertex colour. Without prelight AND
without runtime LIGHTBLOCKs (which only exist for placed light objects,
see bh_light.c::MakePlacedLightNear), every vertex renders black. The
runtime `STDLIGHT` chunk in LIGHTSET is NOT the source of runtime lights
— it is metadata consumed by the prelight bake step in the level editor.

Fixes applied in S6 F7 (in-place, same public API):

  B1  DEFAULT_BRIGHTNESS was 255 (treated as a fixed-point fraction by
      the engine: 255/65536 ≈ 0.004 → zero light). Corrected to 65536
      (ONE_FIXED = 1.0 in 16.16 fixed point).

  B2  DEFAULT_SPREAD was 360 (interpreted as ~32° in the engine's
      4096-per-circle convention). Corrected to 4096 = full 360°.

  B3  LightScaleChunk payload was 44 B (11 i32 fields including spare4).
      The engine struct (Ltchunk.cpp::Light_Scale_Chunk) has only 40 B
      (10 fields, spare1..spare3 — no spare4). The extra 4 bytes shifted
      every subsequent chunk in the LIGHTSET subtree by 4 bytes on load.

  B4  LightScaleChunk.*_above and .*_add are stored as int in the engine
      struct but serialised as float in fill_data_block. Our writer now
      packs them as float to match.

  B5  ShapeVertexIntensitiesChunk gained a convenience classmethod
      all_white() for the common "full bright prelight" case.

  B6  DEFAULT_LIGHT_SPECS updated to match the new brightness/spread.

STDLIGHT orientation is a 9×i32 row-major matrix in 16.16 fixed point
(identity = 65536 on the diagonal). Colour is packed 0x00BBGGRR.

Prelight packed format (Shape_Vertex_Intensities_Chunk::intensity_array):
    bits 0..7   blue
    bits 8..15  green
    bits 16..23 red
    bits 24..31 unused
Full white prelight per vertex = 0x00FFFFFF.
"""
import struct

from ..chunk_writer import Chunk, ChunkWithChildren


# 16.16 fixed-point one — the engine's internal "1.0" for light scalars.
ONE_FIXED = 65536

IDENTITY_MATRIX_16_16 = (
    65536, 0, 0,
    0, 65536, 0,
    0, 0, 65536,
)

DEFAULT_AMBIENCE = 128

# Engine light scalars are fixed-point 16.16. brightness = 65536 == 1.0.
# The old value of 255 produced ~0.004, i.e. effectively no light.
DEFAULT_BRIGHTNESS = 65536

# Engine angles use 4096-per-full-circle (wrap360 = 4096). 360° = 4096.
DEFAULT_SPREAD = 4096

DEFAULT_RANGE = 10000
DEFAULT_COLOUR = 0x00FFFFFF

# There is deliberately no default light: fabricating one would light the
# player and NPCs (they read exactly the STDLIGHT chunks) and quietly ruin
# any lighting test. Callers pass the lights they mean; an empty list gives an
# empty LIGHTSET, which the loader accepts.


class LightSetChunk(ChunkWithChildren):
    """LIGHTSET — container for a named light set (usually 'NORMALLT')."""

    def __init__(self, children=None):
        super().__init__("LIGHTSET", children)


class LightSetHeaderChunk(Chunk):
    """LTSETHDR — 8-char set name + 4-byte pad (12-byte payload)."""

    identifier = "LTSETHDR"

    def __init__(self, name: str = "NORMALLT", pad: int = 0):
        self.name = name
        self.pad = pad

    def payload(self) -> bytes:
        """Serialise 8-char name + i32 pad = 12 bytes."""
        raw = self.name.encode("ascii")[:8].ljust(8, b"\0")
        return raw + struct.pack("<i", self.pad)


class AmbienceChunk(Chunk):
    """AMBIENCE — ambient light intensity hint for the set.

    Note: this value is NOT consumed by VertexIntensity_Standard_Opt in
    Kshape.c (that function only reads SHPVTINT prelight and runtime
    LightSourcesForObject[]). It is a hint for the prelight bake step
    or for the D3D global ambient multiplier. Emitting it is harmless.
    """

    identifier = "AMBIENCE"

    def __init__(self, ambience: int = DEFAULT_AMBIENCE):
        self.ambience = ambience

    def payload(self) -> bytes:
        """Serialise one i32 ambience level."""
        return struct.pack("<i", self.ambience)


class LightScaleChunk(Chunk):
    """LITSCALE — prelight / runtime intensity multipliers.

    Field layout from Ltchunk.cpp::Light_Scale_Chunk::fill_data_block:

        i32 LightMode
        f32 prelight_multiply
        f32 prelight_multiply_above    (int in struct, written as f32)
        f32 prelight_add               (int in struct, written as f32)
        f32 runtime_multiply
        f32 runtime_multiply_above     (int in struct, written as f32)
        f32 runtime_add                (int in struct, written as f32)
        i32 spare1
        i32 spare2
        i32 spare3

    Total payload = 40 bytes. The engine struct has three spare fields,
    NOT four — the old writer emitted 44 bytes, shifting every following
    chunk in the LIGHTSET subtree by 4 bytes on load.

    Apply methods (Ltchunk.cpp):

        ApplyPrelightScale(l):
            if l < prelight_multiply_above: return l
            l += prelight_add - prelight_multiply_above
            l = (int)(l * prelight_multiply)
            l += prelight_multiply_above
            return clamp(l, prelight_multiply_above, 255)
    """

    identifier = "LITSCALE"

    def __init__(self, light_mode: int = 0,
                 prelight_multiply: float = 1.0,
                 prelight_multiply_above: int = 0,
                 prelight_add: int = 0,
                 runtime_multiply: float = 1.0,
                 runtime_multiply_above: int = 0,
                 runtime_add: int = 0,
                 spare1: int = 0, spare2: int = 0, spare3: int = 0):
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

    def payload(self) -> bytes:
        """Serialise 10 fields = 40-byte payload.

        The *_above and *_add fields are int in the engine struct but
        serialised as f32 in fill_data_block — see Ltchunk.cpp.
        """
        return struct.pack(
            "<iffffffiii",
            self.light_mode,
            float(self.prelight_multiply),
            float(self.prelight_multiply_above),
            float(self.prelight_add),
            float(self.runtime_multiply),
            float(self.runtime_multiply_above),
            float(self.runtime_add),
            self.spare1,
            self.spare2,
            self.spare3,
        )


class StandardLightChunk(Chunk):
    """STDLIGHT — one light record (point or directional), 84-byte payload.

    Layout from Ltchunk.cpp::Light_Chunk::fill_data_block:

        i32 light_number
        ChunkVectorInt location      (3 × i32, RIF mm)
        ChunkMatrix    orientation   (9 × i32, 16.16 fixed, row-major)
        i32 brightness               (16.16 fixed; ONE_FIXED = 1.0)
        i32 spread                   (angle units; 4096 = full circle)
        i32 range                    (RIF mm)
        i32 colour                   (packed 0x00BBGGRR)
        i32 engine_light_flags
        i32 local_light_flags
        i32 pad1
        i32 pad2

    This chunk is metadata consumed by the prelight bake step in the
    level editor. It does NOT create a runtime LIGHTBLOCK by itself —
    runtime lights come from placed light objects (I_BehaviourPlacedLight
    in bh_light.c). Without prelight (SHPVTINT), STDLIGHT has no visible
    effect in-game.
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

    Layout from Ltchunk.cpp::Shape_Vertex_Intensities_Chunk:

        char light_set_name[8]
        i32  pad
        i32  num_vertices
        i32  intensity_array[num_vertices]

    `num_vertices` must equal SHPHEAD1.num_verts in the linked REBSHAPE;
    VertexIntensity_Standard_Opt in Kshape.c indexes the array by
    vertex number without a bounds check.

    Packed intensity format (per vertex, 32-bit int):
        bits 0..7   blue   (0..255)
        bits 8..15  green  (0..255)
        bits 16..23 red    (0..255)
        bits 24..31 unused

    Full white prelight = 0x00FFFFFF. All-zero = no prelight (still legal
    but produces black vertices when combined with no runtime lights).

    This chunk is a direct child of RBOBJECT, alongside OBJHEAD1 /
    OBJINTDT / MODULEDT. The engine only reads it if the linked shape
    has ShapeFlag_PreLit set in SHPHEAD1.flags.
    """

    identifier = "SHPVTINT"
    DEFAULT_LIGHT_SET = "NORMALLT"

    # Canonical per-vertex packed colours.
    INTENSITY_BLACK = 0x00000000
    INTENSITY_WHITE = 0x00FFFFFF

    def __init__(self, light_set_name: str = DEFAULT_LIGHT_SET,
                 num_vertices: int = 0, intensities=None):
        self.light_set_name = light_set_name
        self.num_vertices = int(num_vertices)
        if intensities is None:
            intensities = [self.INTENSITY_BLACK] * self.num_vertices
        if len(intensities) != self.num_vertices:
            raise ValueError(
                "intensities length must match num_vertices "
                f"({len(intensities)} != {self.num_vertices})"
            )
        self.intensities = list(intensities)

    @classmethod
    def all_white(cls, num_vertices: int,
                  light_set_name: str = DEFAULT_LIGHT_SET):
        """Build a SHPVTINT with every vertex at full white prelight.

        Convenience for the common "make the geometry fully lit" case
        while we chase the prelight bake path. Combine with
        ShapeFlag_PreLit set in SHPHEAD1.flags.
        """
        return cls(
            light_set_name=light_set_name,
            num_vertices=num_vertices,
            intensities=[cls.INTENSITY_WHITE] * num_vertices,
        )

    @classmethod
    def all_black(cls, num_vertices: int,
                  light_set_name: str = DEFAULT_LIGHT_SET):
        """Build a SHPVTINT with every vertex unlit (baseline / sentinel)."""
        return cls(
            light_set_name=light_set_name,
            num_vertices=num_vertices,
            intensities=[cls.INTENSITY_BLACK] * num_vertices,
        )

    def payload(self) -> bytes:
        """Serialise 8 B name + pad + count + N × i32 = 16 + 4N bytes."""
        buf = bytearray()
        buf += self.light_set_name.encode("ascii")[:8].ljust(8, b"\0")
        buf += struct.pack("<i", 0)
        buf += struct.pack("<i", self.num_vertices)
        if self.num_vertices:
            buf += struct.pack(f"<{self.num_vertices}i", *self.intensities)
        return bytes(buf)