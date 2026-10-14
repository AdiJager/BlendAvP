"""AVPPATH2 — a patrol route for the human AI.

From `PathChnk.cpp` / `PathChnk.hpp`, and verified against all 69 retail chunks:
the formula below reproduces every one of them byte for byte
(`scratch/verify_path_chunk.py`).

The chunk is a child of `SPECLOBJ`, not of the environment chunk, which
`Projload.cpp:166` settles:

    Chunk* pChunk = h->envd->lookup_single_child("SPECLOBJ");
    ((Chunk_With_Children*)pChunk)->lookup_child("AVPPATH2", pathlist);

`lookup_child` rather than `lookup_single_child`, so a level may hold several.

A path is a list of MODULES, not of free points: the bible calls the editor's
Path mode "setting up paths of modules that can be used by the AI for patrol
routes", and each point names the module it belongs to. The engine turns the
module list into AI modules at load (`Projload.cpp:201`).
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# PathFlag_* from PathChnk.hpp:19.
PATH_FLAG_LOOP = 0x00000001
PATH_FLAG_BACK_AND_FORTH = 0x00000002

# PathPointFlag_* from PathChnk.hpp:8.
PATH_POINT_INTERPOLATED = 0x00000001
PATH_POINT_LOCATION_VALID = 0x00000002

# ChunkPathPoint is 24 bytes: module index, a world position, and two ints.
POINT_BYTES = 24


def padded_name(name):
    """Return `name` as NUL-terminated bytes padded to a multiple of four."""
    raw = str(name).encode("latin-1", "replace") + b"\x00"
    while len(raw) % 4:
        raw += b"\x00"
    return raw


class PathPoint:
    """One step of a patrol route.

    `location` is in millimetres and in world space, matching the shipping data:
    `ChunkPathPoint::location` is commented "relative to world", and the loader
    multiplies it straight through without adding the module's position.
    """

    __slots__ = ("module_index", "location", "flags", "spare2")

    def __init__(self, module_index, location, flags=0, spare2=0):
        self.module_index = int(module_index)
        self.location = tuple(int(v) for v in location)
        self.flags = int(flags)
        self.spare2 = int(spare2)

    def payload(self) -> bytes:
        """Serialise the point."""
        return struct.pack("<i3iii", self.module_index, *self.location,
                           self.flags, self.spare2)


class PathChunk(Chunk):
    """AVPPATH2 — one patrol route.

    Payload from `PathChnk.cpp:51`:

        PathName        NUL, padded to 4
        PathID          i32   indexes the engine's PathArray, so IDs must be
                              dense from zero: `Projload.cpp:178` sizes the array
                              from the highest ID it sees
        flags           i32   PATH_FLAG_*
        spare2          i32
        PathLength      i32
        Path[]          PathLength x 24 bytes

    The name is for people and for the editor, not for the engine -- routing
    addresses paths by `PathID` alone. Retail names them `path01`, `sentrys`,
    `guardresponse`.
    """

    identifier = "AVPPATH2"

    def __init__(self, name, path_id, points, flags=PATH_FLAG_BACK_AND_FORTH,
                 spare2=0):
        self.name = str(name)
        self.path_id = int(path_id)
        self.points = list(points)
        self.flags = int(flags)
        self.spare2 = int(spare2)

    def payload(self) -> bytes:
        """Serialise the route."""
        buf = bytearray()
        buf += padded_name(self.name)
        buf += struct.pack("<4i", self.path_id, self.flags, self.spare2,
                           len(self.points))
        for point in self.points:
            buf += point.payload()
        return bytes(buf)

    def size_chunk(self) -> int:
        """Return the total chunk size, header included."""
        return 12 + len(padded_name(self.name)) + 16 + \
            len(self.points) * POINT_BYTES
