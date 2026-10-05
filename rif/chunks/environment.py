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
    """AVPENVIR — AVP_Environment_Settings_Chunk (24-byte minimal variant).

    The full struct in Avpchunk.cpp is larger, but the loader fills
    defaults for fields past data_size. battle.rif uses the 24-byte form,
    so we emit that for compatibility.
    """

    identifier = "AVPENVIR"

    def __init__(self, sky_r: int = 128, sky_g: int = 128, sky_b: int = 128,
                 spare1: int = 0, spare2: int = 128):
        self.sky_r = sky_r
        self.sky_g = sky_g
        self.sky_b = sky_b
        self.spare1 = spare1
        self.spare2 = spare2

    def payload(self) -> bytes:
        """Serialise data_size + RGB + 2 spares = 24 bytes."""
        return struct.pack(
            "<6i", 24, self.sky_r, self.sky_g, self.sky_b,
            self.spare1, self.spare2,
        )