"""MODULEDT and children — per-object module / collision data.

Verified against Obchunk.cpp:

    MODULEDT (Object_Module_Data_Chunk)   container
      ├─ ADJMDLEP  Adjacent_Module_Entry_Points_Chunk
      ├─ MODACOUS  Module_Acoustics_Chunk
      ├─ VMDARRAY  VModule_Array_Chunk
      ├─ MODFLAGS  Module_Flag_Chunk           (optional)
      └─ MODZONE   Module_Zone_Chunk           (optional)

In AvP the physics engine operates on **modules**, not on shapes. A
REBSHAPE is only a visual mesh; collision, culling and spatial queries
all go through MODULEDT. An RBOBJECT without MODULEDT is invisible to
physics — the player falls through it and projectiles pass straight
through, because there is no module to query.

The OBJHEAD1.flags field must have bit 0x200 (OBJECT_FLAG_BASE_OBJECT)
set for the object to participate in the module system. Without it the
loader silently ignores MODULEDT.
"""
import struct

from ..chunk_writer import Chunk, ChunkWithChildren


class ObjectModuleDataChunk(ChunkWithChildren):
    """MODULEDT — container for the module / collision block of one RBOBJECT."""

    def __init__(self, children=None):
        super().__init__("MODULEDT", children)


class AdjacentModuleEntryPointsChunk(Chunk):
    """ADJMDLEP — entry points into this module from adjacent modules.

    Layout (Obchunk.cpp::Adjacent_Module_Entry_Points_Chunk):
        u32 count
        per entry (20 B):
            i32 object_index   (peer module index_num the player comes from)
            i32 flags
            3×i32 entry_point  (RIF mm, position where the player appears)

    Empty list is valid: a standalone module with no neighbours has no
    entry points, and the player spawns directly inside it via AVPSTART.
    """

    identifier = "ADJMDLEP"

    def __init__(self, entries=None):
        # entries: iterable of (object_index, flags, (x, y, z))
        self.entries = list(entries) if entries else []

    def payload(self) -> bytes:
        """Serialise entry count + per-entry 5×i32 record."""
        buf = bytearray()
        buf += struct.pack("<I", len(self.entries))
        for idx, flags, pt in self.entries:
            buf += struct.pack("<2i", idx, flags)
            buf += struct.pack("<3i", *pt)
        return bytes(buf)


class VModuleArrayChunk(Chunk):
    """VMDARRAY — list of peer modules this module links to.

    Layout (Obchunk.cpp::VModule_Array_Chunk::fill_data_block):
        u32 num_items
        per item (16 B):
            i32 object_index   (peer OBJHEAD1.index_num)
            i32 branch_no
            i32 flags
            i32 spare

    For a standalone one-module level, one entry pointing at self
    (object_index == our own index_num) is enough. For multi-module
    levels every object should reference every other reachable object
    so the engine can build the spatial graph.
    """

    identifier = "VMDARRAY"

    def __init__(self, items=None):
        # items: iterable of (object_index, branch_no, flags, spare)
        self.items = list(items) if items else []

    def payload(self) -> bytes:
        """Serialise item count + per-item 4×i32 record."""
        buf = bytearray()
        buf += struct.pack("<I", len(self.items))
        for item in self.items:
            idx, branch, flags, spare = item
            buf += struct.pack("<4i", idx, branch, flags, spare)
        return bytes(buf)


class ModuleAcousticsChunk(Chunk):
    """MODACOUS — per-module reverb / environment acoustics.

    Layout (Obchunk.cpp::Module_Acoustics_Chunk):
        i32 env_index  (-1 = default level acoustics)
        f32 reverb     (-1.0 = default)
        i32 spare
    """

    identifier = "MODACOUS"

    def __init__(self, env_index: int = -1, reverb: float = -1.0,
                 spare: int = 0):
        self.env_index = env_index
        self.reverb = reverb
        self.spare = spare

    def payload(self) -> bytes:
        """Serialise i32 + f32 + i32 = 12-byte payload."""
        return struct.pack("<ifi", self.env_index, self.reverb, self.spare)


# Module flags, from module.h:318. Only these four are worth offering: the
# fifth, MODULEFLAG_HORIZONTALDOOR, is set while loading by Objsetup.cpp:1838.
MODULE_FLAG_AIRDUCT = 0x80000000
MODULE_FLAG_STAIRS = 0x40000000
MODULE_FLAG_SKY = 0x20000000
MODULE_FLAG_FOG = 0x10000000


def module_flags(airduct=False, stairs=False, sky=False, fog=False):
    """Return the packed module flag word from named booleans."""
    word = 0
    if airduct:
        word |= MODULE_FLAG_AIRDUCT
    if stairs:
        word |= MODULE_FLAG_STAIRS
    if sky:
        word |= MODULE_FLAG_SKY
    if fog:
        word |= MODULE_FLAG_FOG
    return word


class ModuleFlagChunk(Chunk):
    """MODFLAGS — optional per-module flags bitfield.

    Layout (Obchunk.cpp::Module_Flag_Chunk):
        i32 Flags
        i32 spare
    """

    identifier = "MODFLAGS"

    def __init__(self, flags: int = 0, spare: int = 0):
        self.flags = flags
        self.spare = spare

    def payload(self) -> bytes:
        """Serialise two i32 fields."""
        # Unsigned: MODULEFLAG_AIRDUCT is 0x80000000, which does not fit a
        # signed int.
        return struct.pack("<II", self.flags & 0xFFFFFFFF,
                           self.spare & 0xFFFFFFFF)