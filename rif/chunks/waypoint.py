"""WAYPOINT — AI navigation volumes for a module.

A waypoint is a **box**, not a point. `WaypointContainsPoint` builds the test
box as `centre + min_extents` / `centre + max_extents`, and `WaypointContainsNPC`
tests the corners of the NPC's own box against it, so it is an AABB overlap.
The volume is what makes the answer to "am I in a waypoint" stable: with a point,
the answer would flip every few frames and `SweepWaypoints` would fall into its
dropout path, sending the NPC straight at the target instead of following the
graph.

**In the file `min` and `max` are ABSOLUTE WORLD coordinates**, not offsets from
`centre`, despite the header comment saying otherwise. Derived from retail data
(`Derelict.RIF`): reading them as offsets puts every box tens of metres from its
module, reading them as absolute puts every box inside its module.

Layout (confirmed in the loader at `wpchunk.cpp:30` and the writer at `:235`):

    i32 NumWaypoints
    per waypoint:
        3×i32 min          — absolute world
        3×i32 max          — absolute world
        3×i32 centre       — absolute world
        i32   flags
        i32   spare2
        i32   NumWPLinks
        NumWPLinks × ( i32 index, i32 flags )     // WaypointLink, 8 B
        i32   NumModLinks
        NumModLinks × ( cstr name padded to 4, i32 flags )

Record = `52 + 8 × NumWPLinks` bytes.

Two details that are easy to get wrong:

* `index` is **not** in the file — the loader assigns `cw->index = i`.
* `spare1`/`spare2` are read by the loader after the records, but the writer
  never emits them: every one of the 332 retail chunks ends exactly at the last
  record. Writing them would add 8 bytes the loader does not expect from a file
  it wrote itself.

The whole list is stored as **alien** waypoints. Retail never sets
`WaypointFlag_FirstGroundWaypoint` in 63 levels, so `NumGroundWaypoints` comes
out zero and everything is alien; ground patrols use a separate spline system
(the `Path` mode in Inteng).
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# Set when the author placed the centre rather than deriving it from the box.
WAYPOINT_FLAG_CENTRE_DEFINED_BY_USER = 0x80000000
# Set by the writer on the first ground waypoint; retail never sets it.
WAYPOINT_FLAG_FIRST_GROUND_WAYPOINT = 0x40000000

# Bytes of a record before its link array.
RECORD_HEADER_BYTES = 52
# Bytes of one WaypointLink.
LINK_BYTES = 8


class WaypointChunk(Chunk):
    """WAYPOINT — the navigation volumes belonging to one module.

    One chunk per module, living inside that module's MODULEDT. Membership is
    structural: a waypoint belongs to the module whose chunk holds it, and there
    is no separate link field. That matters functionally, because the engine
    only consults `containingModule->m_aimodule->m_waypoints` — a waypoint whose
    box spills into the next room is still only used while the NPC stands in the
    module that owns it.
    """

    identifier = "WAYPOINT"

    def __init__(self, waypoints):
        """Store the waypoint records.

        waypoints : list of (min, max, centre, flags, [(index, flags), ...]).
            `min` and `max` are absolute world coordinates.
        """
        self.waypoints = list(waypoints)

    def payload(self) -> bytes:
        """Serialise the count followed by every record."""
        buf = bytearray()
        buf += struct.pack("<i", len(self.waypoints))
        for low, high, centre, flags, links in self.waypoints:
            buf += struct.pack("<3i", *low)
            buf += struct.pack("<3i", *high)
            buf += struct.pack("<3i", *centre)
            # Unsigned: 0x80000000 does not fit a signed int, and the
            # flags field is a bit set rather than a number.
            buf += struct.pack("<I", flags & 0xFFFFFFFF)
            buf += struct.pack("<i", 0)               # spare2
            buf += struct.pack("<i", len(links))
            for index, link_flags in links:
                buf += struct.pack("<2i", index, link_flags)
            buf += struct.pack("<i", 0)               # NumModLinks
        return bytes(buf)
