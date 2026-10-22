"""`OBJTRAK2` — the track data four strategies share.

`Track` (72), `TrackDestruct` (80), `Fan` (75) and `PlatLift` (54) all read this
chunk, and without it none of them works: `Fan` does not merely misbehave, it
trips `GLOBALASSERT(track)` in `Objsetup.cpp:1159`.

Layout from `OBCHUNK.HPP:401-448` and `OBCHUNK.CPP:1143-1175`:

    + 0  num_sections       i32
    + 4  sections[]         n * 76 B
           + 0  quat_start        4 * i32   (w, x, y, z)
           +16  quat_end          4 * i32
           +32  pivot_start       3 * i32
           +44  pivot_end         3 * i32
           +56  object_offset     3 * i32
           +68  time_for_section  i32
           +72  spare            i32
    +4+76n  flags           i32
    +8+76n  timer_start     i32

Payload is `12 + 76n`; the formula holds for all 663 chunks in the shipped
levels, with no exceptions.

Two things the file looks like it says but does not:

* **The quaternion's `x`, `y` and `z` are negated, `w` is not.** The loader reads
  them as `value / -65536.0` (`OBCHUNK.CPP:1191-1207`) and `add_fan` does the
  same to `object_data.orientation` (`Objsetup.cpp:1173-1176`), so this is the
  file's convention rather than a slip.
* **`pivot_end` is a destination in the file but a delta at runtime.**
  `setup_track_controller` stores `pivot_travel = (pivot_end - pivot_start)`
  (`Objsetup.cpp:283`), so the pair means "from here to there", not two
  independent points.

`time_for_section` is seconds in 16.16 -- `65536` is one second. Retail's values
are 0.25, 0.5, 1, 2, 3, 5, 7 and 10 seconds, and it travels through the loader
unscaled, so the units are the file's own.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

TRACK_SECTION_BYTES = 76
TRACK_PAYLOAD_BASE = 12

# `OBCHUNK.HPP:419-423`.
TRACK_FLAG_LOOP = 0x00000001
TRACK_FLAG_PLAYING_AT_START = 0x00000002
TRACK_FLAG_LOOP_BACK_AND_FORTH = 0x00000004
TRACK_FLAG_USE_TRACK_SMOOTHING = 0x00000008
# Set by the loader on read (`OBCHUNK.CPP:1226`), never stored in a file.
TRACK_FLAG_QUAT_PROBLEM_SORTED = 0x80000000

ONE_FIXED = 1 << 16


def _fixed(value):
    """Convert a float to 16.16 fixed point, rounding to nearest."""
    return int(round(float(value) * ONE_FIXED))


class TrackSection:
    """One straight run of a track: a start, an end, and how long it takes.

    `pivot_start` and `pivot_end` are millimetres in the level's own frame. The
    engine turns the pair into `pivot_travel`, so a section that does not move
    simply has `pivot_end == pivot_start` -- which is what 481 of retail's
    sections do.
    """

    def __init__(self, pivot_start=(0, 0, 0), pivot_end=None,
                 object_offset=(0, 0, 0), seconds=1.0, spare=0,
                 quat_start=(1.0, 0.0, 0.0, 0.0),
                 quat_end=None):
        self.pivot_start = tuple(int(v) for v in pivot_start)
        # A section that does not move is the common case, so `pivot_end`
        # defaults to the start rather than asking every caller for it.
        self.pivot_end = (tuple(int(v) for v in pivot_end)
                          if pivot_end is not None else self.pivot_start)
        self.object_offset = tuple(int(v) for v in object_offset)
        self.seconds = float(seconds)
        self.spare = int(spare)
        # Stored as (w, x, y, z), Blender's own order.
        self.quat_start = tuple(float(v) for v in quat_start)
        self.quat_end = (tuple(float(v) for v in quat_end)
                         if quat_end is not None else self.quat_start)

    def payload(self) -> bytes:
        """Serialise the 76-byte section."""
        buf = bytearray()
        for quat in (self.quat_start, self.quat_end):
            w, x, y, z = quat
            # The file negates x, y and z. See the module docstring.
            buf += struct.pack("<4i", _fixed(w), -_fixed(x), -_fixed(y), -_fixed(z))
        for vector in (self.pivot_start, self.pivot_end, self.object_offset):
            buf += struct.pack("<3i", vector[0], vector[1], vector[2])
        buf += struct.pack("<2i", _fixed(self.seconds), self.spare)
        assert len(buf) == TRACK_SECTION_BYTES, len(buf)
        return bytes(buf)


class ObjectTrackChunk(Chunk):
    """OBJTRAK2 — the object's track, as `12 + 76n` bytes."""

    identifier = "OBJTRAK2"

    def __init__(self, sections=None, flags=TRACK_FLAG_QUAT_PROBLEM_SORTED,
                 timer_start=0):
        self.sections = list(sections or [])
        # The shipped files DO carry the sorting bit: it was set when they were
        # last written, not only when read, so leaving it out produces a chunk
        # that differs from every retail one by exactly this word.
        self.flags = int(flags) & 0xFFFFFFFF
        self.timer_start = int(timer_start)

    def payload(self) -> bytes:
        """Serialise the section table and the trailing flags."""
        buf = bytearray()
        buf += struct.pack("<i", len(self.sections))
        for section in self.sections:
            buf += section.payload()
        # Flags are a bitfield, so `0x80000000` has to be packed unsigned;
        # the timer is an ordinary signed int.
        buf += struct.pack("<Ii", self.flags, self.timer_start)
        expected = TRACK_PAYLOAD_BASE + TRACK_SECTION_BYTES * len(self.sections)
        assert len(buf) == expected, len(buf)
        return bytes(buf)


def quaternion_payload(matrix):
    """Return (w, x, y, z) from a Blender matrix's rotation.

    Blender normalises its own quaternions, so a scaled object does not produce
    a scaled rotation -- which is what the track wants, since the engine treats
    the quaternions as orientations and nothing else.
    """
    quat = matrix.to_quaternion()
    return (quat.w, quat.x, quat.y, quat.z)


def build_track_chunk(sections, flags=0, timer_start=0):
    """Build the OBJTRAK2 chunk for a list of `TrackSection` objects."""
    return ObjectTrackChunk(sections=list(sections), flags=flags,
                            timer_start=timer_start)


# `Strachnk.hpp:745-747`.
TRACK_REQUEST_FLAG_ACTIVE_FORWARD = 0x00000001
TRACK_REQUEST_FLAG_ACTIVE_BACKWARD = 0x00000002
TRACK_REQUEST_FLAG_OPPOSITE_BACKWARD = 0x00000004

STRAT_TRACK = 72
TRACK_STRATEGY_BASE = 32


class TrackRequest:
    """One message a track point sends: who, what, and when it applies.

    `request` uses the encoding every strategy shares -- `(code << 1) | state`.
    `flags` says which direction of travel triggers it; retail only ever uses
    `ActiveForward` and `ActiveForward|ActiveBackward`.
    """

    def __init__(self, target_id=b"\x00" * 8, request=0,
                 flags=TRACK_REQUEST_FLAG_ACTIVE_FORWARD):
        self.target_id = bytes(target_id).ljust(8, b"\x00")[:8]
        self.request = int(request)
        self.flags = int(flags)

    def payload(self) -> bytes:
        """Serialise the 16-byte target record."""
        buf = self.target_id + struct.pack("<2i", self.request, self.flags)
        assert len(buf) == 16, len(buf)
        return buf


class TrackPoint:
    """One point on the track and the messages it sends on arrival.

    Retail's 232 tracks hold 20 targets between them and 224 of the points lists
    are empty, so an empty `TrackPoint` is not a degenerate case -- it is what
    almost every track in the game is.
    """

    def __init__(self, point_no=0, targets=None, pad1=0):
        self.point_no = int(point_no)
        self.targets = list(targets or [])
        self.pad1 = int(pad1)

    def payload(self) -> bytes:
        """Serialise the point header followed by its targets."""
        buf = bytearray()
        # `pad1` sits BETWEEN the point number and the target count; the file
        # order differs from the struct order in `Strachnk.hpp:756`.
        buf += struct.pack("<3i", self.point_no, self.pad1, len(self.targets))
        for target in self.targets:
            buf += target.payload()
        return bytes(buf)


class TrackStrategyChunk(Chunk):
    """AVPSTRAT — a `Track`, strategy type 72."""

    identifier = "AVPSTRAT"

    def __init__(self, points=None, index=0, spare2=0, type_=0,
                 extra_data=0, pad1=0, pad2=0):
        self.points = list(points or [])
        self.index = int(index)
        self.spare2 = int(spare2)
        self.type_ = int(type_)
        self.extra_data = int(extra_data)
        self.pad1 = int(pad1)
        self.pad2 = int(pad2)

    def payload(self) -> bytes:
        """Serialise the strategy, its points and the trailing pads."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.spare2)
        buf += struct.pack("<i", STRAT_TRACK)
        buf += struct.pack("<i", self.type_)
        buf += struct.pack("<i", self.extra_data)
        buf += struct.pack("<i", len(self.points))
        for point in self.points:
            buf += point.payload()
        buf += struct.pack("<2i", self.pad1, self.pad2)
        return bytes(buf)


def build_track_strategy(points=None, **kwargs):
    """Build the AVPSTRAT chunk for a `Track` strategy."""
    return TrackStrategyChunk(points=list(points or []), **kwargs)
