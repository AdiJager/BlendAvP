"""REBENVDT and its children — environment / level chunks.

Layouts verified byte-for-byte against Envchunk.cpp, Avpchunk.cpp and
battle.rif (S5/S6). Includes:
  - REBENVDT  (Environment_Data_Chunk)         container
  - ENDTHEAD  (Environment_Data_Header_Chunk)  flags + lock_user + version
  - RIFFNAME  (RIF_Name_Chunk)                 padded ASCII level name
  - BMPNAMES  (Global_BMP_Name_Chunk)          20 B fixed + padded name
  - BMNAMVER  (BMP_Names_Version_Chunk)        u32
  - BMNAMEXT  (BMP_Names_ExtraData_Chunk)      52 B zero
  - ENVSDSCL  (Environment_Scale_Chunk)        DOUBLE (8 B), not float
  - SPECLOBJ  (Special_Objects_Chunk)          container for placed markers
  - AVPSTART  (AVP_Player_Start_Chunk)         location + 3x3 matrix + moduleID
  - AVPENVIR  (AVP_Environment_Settings_Chunk) 24 B minimal variant

The BMPNAMES default d2 = 6 (confirmed in desk1.rif + sentry.rif); the
S2 note that said "priority defaults to 0" was wrong.
"""
import struct

from ..chunk_writer import Chunk, ChunkWithChildren
from ..types import pack_string


# BMPNAMES per-entry defaults confirmed against desk1/sentry samples.
BMPN_DEFAULT_FLAGS = 0x0100010C
BMPN_DEFAULT_D1 = 0
BMPN_DEFAULT_D2 = 6          # always 6 in both samples (S2 note said 0)
BMPN_DEFAULT_D3 = 0x00800080
BMPNAMES_VERSION = 0         # header high-16; 0 in both samples

# AVPSTART orientation matrix uses 16.16 fixed point (65536 == 1.0).
MATRIX_ONE = 65536


class EnvironmentDataChunk(ChunkWithChildren):
    """REBENVDT — level metadata container (root of environment tree)."""

    def __init__(self, children=None):
        super().__init__("REBENVDT", children)


class EnvironmentHeaderChunk(Chunk):
    """ENDTHEAD — flags(i32) + lock_user(16 B) + version_no(i32) = 24 B."""

    identifier = "ENDTHEAD"

    def __init__(self, flags: int = 0, lock_user: str = "",
                 version_no: int = 0):
        self.flags = flags
        self.lock_user = lock_user
        self.version_no = version_no

    def payload(self) -> bytes:
        """Serialise 4 + 16 + 4 = 24-byte payload."""
        buf = bytearray()
        buf += struct.pack("<i", self.flags)
        buf += self.lock_user.encode("ascii")[:16].ljust(16, b"\0")
        buf += struct.pack("<i", self.version_no)
        return bytes(buf)


class RIFNameChunk(Chunk):
    """RIFFNAME — level identifier (NUL-terminated ASCII, padded to 4 B)."""

    identifier = "RIFFNAME"

    def __init__(self, name: str):
        self.name = name

    def payload(self) -> bytes:
        """Serialise padded ASCII name."""
        return pack_string(self.name)


class BMPNamesVersionChunk(Chunk):
    """BMNAMVER — BMP_Names_Version_Chunk (u32 version)."""

    identifier = "BMNAMVER"

    def __init__(self, version: int = 1):
        self.version = version

    def payload(self) -> bytes:
        """Serialise 4-byte version int."""
        return struct.pack("<I", self.version)


class BMPNamesExtraDataChunk(Chunk):
    """BMNAMEXT — BMP_Names_ExtraData_Chunk (52 B, all zero in samples)."""

    identifier = "BMNAMEXT"
    RESERVED_BYTES = 52

    def payload(self) -> bytes:
        """Serialise 52 bytes of zero (flags + 12 reserved u32)."""
        return b"\0" * self.RESERVED_BYTES


class BMPNamesChunk(Chunk):
    """BMPNAMES — Global_BMP_Name_Chunk.

    Header:  u32 (count low 16 | version high 16)
    Entry :  u32 flags / u32 index / u32 d1 / u32 d2 / u32 d3 / cstr name
    The 'index' field is what poly_colour.bitmap_idx matches against
    (NOT the array position — confirmed in S3).
    """

    identifier = "BMPNAMES"

    def __init__(self, entries, version: int = BMPNAMES_VERSION):
        self.entries = list(entries)
        self.version = version

    def payload(self) -> bytes:
        """Serialise header + per-entry fixed block + padded name."""
        buf = bytearray()
        count = len(self.entries) & 0xFFFF
        buf += struct.pack("<I", (self.version << 16) | count)
        for entry in self.entries:
            buf += struct.pack("<I", entry.get("flags", BMPN_DEFAULT_FLAGS))
            buf += struct.pack("<I", entry["index"] & 0xFFFFFFFF)
            buf += struct.pack("<I", entry.get("d1", BMPN_DEFAULT_D1))
            buf += struct.pack("<I", entry.get("d2", BMPN_DEFAULT_D2))
            buf += struct.pack("<I", entry.get("d3", BMPN_DEFAULT_D3))
            buf += pack_string(entry["name"])
        return bytes(buf)


class EnvironmentScaleChunk(Chunk):
    """ENVSDSCL — Environment_Scale_Chunk (double scale, 8 B).

    Verified against Envchunk.cpp: fill_data_block writes a **double**,
    not a float. Optional — used by SHPEXTFL to rescale external shapes.
    """

    identifier = "ENVSDSCL"

    def __init__(self, scale: float = 1.0):
        self.scale = float(scale)

    def payload(self) -> bytes:
        """Serialise 8-byte little-endian IEEE-754 double."""
        return struct.pack("<d", self.scale)


class SpecialObjectsChunk(ChunkWithChildren):
    """SPECLOBJ — Special_Objects_Chunk (holds AVPSTART, generators, paths)."""

    def __init__(self, children=None):
        super().__init__("SPECLOBJ", children)


# AVPGENER flags, Avpchunk.hpp:9-26.
GEN_MODEFLAGSSET = 0x00000001
GEN_GAMEMODE_MARINE = 0x00000800
GEN_GAMEMODE_ALIEN = 0x00001000
GEN_GAMEMODE_PREDATOR = 0x00002000
GEN_ADVANCEDGENERATOR = 0x08000000
GEN_MULTIPLAYERSTART = 0x40000000
GEN_INACTIVE = 0x80000000

# 4096 units per full turn, matching every other angle in the format.
ORIENTATION_UNITS_PER_TURN = 4096


def generator_flags(races=("marine", "alien", "predator"), inactive=False,
                    multiplayer_start=False, advanced=False):
    """Return the AVPGENER flag word for the given roles and races.

    `MODEFLAGSSET` is set unconditionally: all 1480 retail generators carry it,
    and it marks the race bits as meaningful. The race bits themselves are read
    by the engine only on MULTIPLAYERSTART generators.
    """
    flags = GEN_MODEFLAGSSET
    if "marine" in races:
        flags |= GEN_GAMEMODE_MARINE
    if "alien" in races:
        flags |= GEN_GAMEMODE_ALIEN
    if "predator" in races:
        flags |= GEN_GAMEMODE_PREDATOR
    if multiplayer_start:
        flags |= GEN_MULTIPLAYERSTART | GEN_ADVANCEDGENERATOR
    if advanced:
        flags |= GEN_ADVANCEDGENERATOR
    if inactive:
        flags |= GEN_INACTIVE
    return flags


class AVPGeneratorChunk(Chunk):
    """AVPGENER — AVP_Generator_Chunk: a spawn point or an NPC generator.

    Payload from Avpchunk.cpp:15-60, verified against all 1480 generators in
    the 63 shipped levels:

        location     3 x i32   RIF millimetres
        orientation  i32       rotation about Y, 4096 units per full turn
        type         i32       0 spawn/random, 1..9 a fixed NPC kind
        flags        i32       `generator_flags()`
        textureID    u8        model variant
        sub_type     u8        weapon or species variant
        extra1, extra2  u8
        name         char[]    NUL-terminated, then padding

    The chunk is exactly `round_up_to_4(29 + len(name))` bytes of payload, which
    is what `size_chunk()` in the engine computes. Only 12 bits of
    `orientation` are stored, so out-of-range values are masked rather than
    rejected.

    This chunk is a plain `Chunk`, NOT a container: the optional extra data
    (`AVPGENEX`) is a SIBLING inside `SPECLOBJ` and is matched to this chunk by
    the `name` string, so names must be unique within a level.
    """

    identifier = "AVPGENER"

    def __init__(self, name, location=(0, 0, 0), orientation=0, npc_type=0,
                 flags=None, texture_id=0, sub_type=0, extra1=0, extra2=0):
        self.name = str(name)
        self.location = tuple(location)
        self.orientation = int(orientation)
        self.npc_type = int(npc_type)
        self.flags = int(flags) if flags is not None else generator_flags()
        self.texture_id = int(texture_id)
        self.sub_type = int(sub_type)
        self.extra1 = int(extra1)
        self.extra2 = int(extra2)

    def payload(self) -> bytes:
        """Serialise the generator, padding the name to a 4-byte boundary."""
        buf = bytearray()
        buf += struct.pack("<3i", *self.location)
        # Only the low 12 bits survive a round trip through the loader.
        buf += struct.pack("<i", self.orientation & 0xFFF)
        buf += struct.pack("<i", self.npc_type)
        buf += struct.pack("<i", self.flags)
        buf += struct.pack("<4B", self.texture_id & 0xFF, self.sub_type & 0xFF,
                           self.extra1 & 0xFF, self.extra2 & 0xFF)
        buf += self.name.encode("latin-1", "replace") + b"\x00"
        while len(buf) % 4:
            buf += b"\x00"
        return bytes(buf)


# GLOGENDC.EnemyGenerated: which creature a generator without weights falls
# back to. `Objsetup.cpp:3105-3119` reads this when WeightingTotal == 0.
GENERATE_ALIENS = 0
GENERATE_MARINES = 1

# Defaults taken from the commonest retail combination, `als-dm-coop.rif`:
# aliens, at most 25 NPCs, four per minute. Every one of the 46 retail chunks
# leaves both spare fields at zero, so these do too.
GLOGENDC_DEFAULTS = {
    "enemy_generated": GENERATE_ALIENS,
    "max_npcs": 25,
    "npcs_per_minute": 4,
    "npc_acceleration": 2,
    "hive_state_change_time": 32000,
}


class GlobalGeneratorDataChunk(Chunk):
    """GLOGENDC — Global_Generator_Data_Chunk: the level's spawning budget.

    Payload (28 B) from Avpchunk.cpp, confirmed against all 46 retail chunks,
    every one of which is exactly 28 bytes and a DIRECT CHILD OF REBENVDT:

        EnemyGenerated        i32   0 aliens, 1 marines (GENERATE_*)
        MaxNPCSOnLevel        i32   hard cap on live NPCs
        NPCSPerMinute         i32   spawning rate
        NPCAcceleration       i32
        HiveStateChangeTime   i32
        spare1, spare2        i32   zero in every retail chunk

    This chunk is not optional decoration. Every one of the 28 retail levels
    that carries generators with non-zero alien weights also carries GLOGENDC,
    and with no budget the engine has nothing to spawn into — a level with
    perfect generators and no GLOGENDC stays empty. The exporter therefore
    writes one whenever a level has any generator at all.
    """

    identifier = "GLOGENDC"

    def __init__(self, enemy_generated=GENERATE_ALIENS, max_npcs=25,
                 npcs_per_minute=4, npc_acceleration=2,
                 hive_state_change_time=32000, spare1=0, spare2=0):
        self.enemy_generated = int(enemy_generated)
        self.max_npcs = int(max_npcs)
        self.npcs_per_minute = int(npcs_per_minute)
        self.npc_acceleration = int(npc_acceleration)
        self.hive_state_change_time = int(hive_state_change_time)
        self.spare1 = int(spare1)
        self.spare2 = int(spare2)

    @classmethod
    def from_defaults(cls, **overrides):
        """Build a chunk from the retail defaults, overriding named fields."""
        settings = dict(GLOGENDC_DEFAULTS)
        settings.update(overrides)
        return cls(**settings)

    def payload(self) -> bytes:
        """Serialise the 28-byte payload (seven i32)."""
        return struct.pack(
            "<7i", self.enemy_generated, self.max_npcs, self.npcs_per_minute,
            self.npc_acceleration, self.hive_state_change_time,
            self.spare1, self.spare2)


# ------------------------------------------------------------------ generators

# `AVP_Generator_Weighting`, in the order the struct declares it. Index 0 is
# the struct's own `data_size` field, not a weight.
WEIGHT_FIELDS = (
    "PulseMarine_Wt", "FlameMarine_Wt", "SmartMarine_Wt", "SadarMarine_Wt",
    "GrenadeMarine_Wt", "MinigunMarine_Wt", "ShotgunCiv_Wt", "PistolCiv_Wt",
    "FlameCiv_Wt", "UnarmedCiv_Wt", "MolotovCiv_Wt", "Alien_Wt",
    "PredAlien_Wt", "Praetorian_Wt", "PistolMarine_Wt",
)
WEIGHT_DATA_SIZE = 64
# The three kinds a network game can actually spawn. Everything else in the
# table describes marines and civilians, which `Objsetup.cpp:2993` refuses when
# NetworkAIServer is set, so the exporter does not offer them.
WEIGHT_ALIEN = "Alien_Wt"
WEIGHT_PREDALIEN = "PredAlien_Wt"
WEIGHT_PRAETORIAN = "Praetorian_Wt"
NETWORK_WEIGHT_FIELDS = (WEIGHT_ALIEN, WEIGHT_PREDALIEN, WEIGHT_PRAETORIAN)


class GeneratorExSettingsChunk(Chunk):
    """GENEXSET — Generator_Extended_Settings_Chunk: rate, limit and weights.

    Payload is exactly 84 bytes in all 384 retail chunks, confirmed against
    real bytes rather than the loader; an earlier note claimed 96, which would
    have written 12 bytes of rubbish into every weighted generator:

        GenerationRate      i32     0
        GenRateIncrease     i32     4
        GenLimit            u8      8
        pad1..3             u8      9..11
        spare1, spare2      i32     12, 16
        AVP_Generator_Weighting     20..83   (data_size + 15 weights)

    The engine reads this chunk ONLY when the generator carries
    `ADVANCEDGENERATOR` (`AVPCHUNK.CPP:201` returns null otherwise), so the two
    must be written together or the weights are silently ignored.
    """

    identifier = "GENEXSET"

    def __init__(self, generation_rate=0, gen_rate_increase=0, gen_limit=0,
                 spare1=0, spare2=0, weights=None):
        self.generation_rate = int(generation_rate)
        self.gen_rate_increase = int(gen_rate_increase)
        self.gen_limit = int(gen_limit)
        self.spare1 = int(spare1)
        self.spare2 = int(spare2)
        # Named so a caller can set the three network kinds and leave the rest
        # zero, which is what a skirmish generator actually needs.
        self.weights = {name: 0 for name in WEIGHT_FIELDS}
        for name, value in (weights or {}).items():
            if name not in self.weights:
                raise ValueError(f"unknown weight {name!r}; "
                                 f"expected one of {WEIGHT_FIELDS}")
            self.weights[name] = int(value)

    @property
    def weighting_total(self):
        """Return the sum the engine rolls against.

        Zero means "no weights"; the engine then falls back to the level's
        default creature (`Objsetup.cpp:3105`), which is a legitimate setup and
        not a mistake.
        """
        return sum(self.weights.values())

    def payload(self) -> bytes:
        """Serialise the 84-byte payload (20 bytes of header plus weights)."""
        buf = bytearray()
        buf += struct.pack("<2i", self.generation_rate, self.gen_rate_increase)
        buf += struct.pack("<4B", self.gen_limit & 0xFF, 0, 0, 0)
        buf += struct.pack("<2i", self.spare1, self.spare2)
        buf += struct.pack("<i", WEIGHT_DATA_SIZE)
        buf += struct.pack("<15i", *(self.weights[n] for n in WEIGHT_FIELDS))
        return bytes(buf)


class GeneratorNameChunk(Chunk):
    """AVPGENNM — the generator name, padded to a four-byte boundary.

    The engine matches this string against `AVPGENER.name` with `strcmp`
    (`AVPCHUNK.CPP:169-192`) to attach extra data to the right generator, which
    is why generator names must be unique within a level.

    Payload size is `(len(name) + 4) & ~3`, confirmed on retail: the observed
    sizes are 4, 8, 12, 16, 20, 24, 28 and 40 for names of increasing length.
    """

    identifier = "AVPGENNM"

    def __init__(self, name):
        self.name = str(name)

    def payload(self) -> bytes:
        """Serialise the name, padded so the payload is a multiple of four."""
        buf = bytearray(self.name.encode("latin-1", "replace") + b"\x00")
        while len(buf) % 4:
            buf += b"\x00"
        return bytes(buf)


class AVPGeneratorExtraChunk(ChunkWithChildren):
    """AVPGENEX — extra data for one generator, a SIBLING of its `AVPGENER`.

    Deliberately not a child: `AVP_Generator_Chunk` derives from `Chunk`, only
    `AVPGENEX` is a container. It is linked to its generator by NAME, not by
    position, so the two chunks need not even be adjacent.

    Retail assembles it from an optional subset — `AVPGENNM` is always present
    (986 of 986), `GENEXSET` in 384, `AVPSTRAT` in 380 and `ALTLOCAT` in 355.
    The exporter writes the name plus, when the generator has weights, the
    settings.
    """

    identifier = "AVPGENEX"

    def __init__(self, name, settings=None, children=None):
        self.name = str(name)
        built = [GeneratorNameChunk(self.name)]
        if settings is not None:
            built.append(settings)
        if children:
            built.extend(children)
        super().__init__(self.identifier, built)


class PlayerStartChunk(Chunk):
    """AVPSTART — AVP_Player_Start_Chunk.

    Payload (56 B) from Avpchunk.cpp:
        ChunkVectorInt location      3x i32 (12 B)
        ChunkMatrix    orientation   9x i32 (36 B, 16.16 fixed, row-major)
        ObjectID       moduleID      2x u32 (8 B)
    Default identity orientation: 65536 on the diagonal, 0 elsewhere.
    """

    identifier = "AVPSTART"

    def __init__(self, location=(0, 0, 0), orientation=None,
                 module_id=(0, 0)):
        self.location = tuple(location)
        if orientation is None:
            orientation = (
                MATRIX_ONE, 0, 0,
                0, MATRIX_ONE, 0,
                0, 0, MATRIX_ONE,
            )
        self.orientation = tuple(orientation)
        self.module_id = tuple(module_id)

    def payload(self) -> bytes:
        """Serialise 56-byte payload (location + matrix + moduleID)."""
        buf = bytearray()
        buf += struct.pack("<3i", *self.location)
        buf += struct.pack("<9i", *self.orientation)
        buf += struct.pack("<2I", *self.module_id)
        return bytes(buf)


class AVPEnvironmentSettingsChunk(Chunk):
    """AVPENVIR — AVP_Environment_Settings_Chunk.

    Layout from `AVPCHUNK.HPP:256`, verified byte for byte against all 57
    retail chunks (`scratch/verify_env_writer.py`):

        data_size            i32  24, and a VERSION TAG: the loader fills in the
                                  sky colour below 16 and the weapon bits below
                                  24, so older levels carry fewer fields
        sky_colour_red       i32
        sky_colour_green     i32
        sky_colour_blue      i32
        bits                 u32  seven flags then 25 spares, in the order
                                  AVPCHUNK.HPP:268 declares them
        predator_num_spears  i32

    The two fields this class used to call `spare1` and `spare2` are the packed
    word and the spear count; the offsets were right and the names were not.
    `spare2` also defaulted to 128 where the engine's own default, and every
    retail level, uses 30.

    The sky is a COLOUR, not a texture: the engine fills the void with it.
    Retail's commonest value is (30, 30, 30).
    """

    identifier = "AVPENVIR"

    def __init__(self, sky_r: int = 30, sky_g: int = 30, sky_b: int = 30,
                 bits: int = 0, predator_num_spears: int = 30):
        self.sky_r = sky_r
        self.sky_g = sky_g
        self.sky_b = sky_b
        self.bits = bits
        self.predator_num_spears = predator_num_spears

    @property
    def flags(self):
        """Return the packed word as named booleans."""
        from .environment_settings import unpack_bits
        return unpack_bits(self.bits)

    def payload(self) -> bytes:
        """Serialise data_size + RGB + the packed word + spears = 24 bytes."""
        from .environment_settings import SETTINGS_BYTES
        buf = struct.pack(
            "<6i", SETTINGS_BYTES, self.sky_r, self.sky_g, self.sky_b,
            self.bits, self.predator_num_spears,
        )
        assert len(buf) == SETTINGS_BYTES, len(buf)
        return buf