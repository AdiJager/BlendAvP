"""One-off: a MissionObjective strategy, the level's end condition.

`StratMissionObjective` (70) is written as a VIOBJECT beside the kill volumes --
`Objsetup.cpp:3628` walks those and `:3663` dispatches on the type. The engine
then sorts them by `mission_number` (`add_mission_to_list`, `Objsetup.cpp:3251`)
and builds the level's objective from the first.

All 31 retail payloads are exactly 56 bytes, which fixes the record at twelve
ints. `GetStrategySize()` in the engine reports 36 and counts only six of the
nine fields `fill_data_block` writes; the shipping data sides with
`fill_data_block`, so that is what this follows.

The two arrays declared in the header are empty in every retail objective --
`num_mission_targets` and `num_request_targets` are both zero at +40 and +44 --
so `MissionMessage` and `RequestTarget` are never written and never needed.

The text fields are indices into the game's own `english.txt`, and 0 means
blank (`SetupMissionObjectives`, `Objsetup.cpp:3282`), which is what retail uses.
So an objective needs no text of its own and no file of the level's.
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# StrategyType values that live in a virtual object.
STRAT_MISSION_OBJECTIVE = 70
STRAT_TEXT_MESSAGE = 73

# The payloads retail ships, and the standard this writer is held to.
OBJECTIVE_BYTES = 56
MESSAGE_BYTES = 32

# `SetupMissionObjectives` asserts both indices are under this.
MAX_TEXT_INDEX = 450

# What retail uses most: flags 3 in 28 of 31, effect 1 in all 31.
# Retail's `size` for every objective it ships: one metre, the default.
# It is not a measurement -- the engine reads the position and the name,
# not an extent -- so a value taken from the scene would be invented.
DEFAULT_OBJECTIVE_SIZE = 1000

DEFAULT_OBJECTIVE_FLAGS = 3
DEFAULT_COMPLETION_EFFECT = 1


class MissionObjectiveStrategyChunk(Chunk):
    """AVPSTRAT — one mission objective, as a 56-byte record.

    `description` and `complete` are 1-based indices into the game's own text,
    with 0 meaning blank. `order` sorts the objectives when a level has more
    than one, and retail's are all zero, which leaves a single objective the
    level is completed by.
    """

    identifier = "AVPSTRAT"

    def __init__(self, description=0, complete=0, order=0,
                 flags=DEFAULT_OBJECTIVE_FLAGS,
                 effect=DEFAULT_COMPLETION_EFFECT):
        self.description = int(description)
        self.complete = int(complete)
        self.order = int(order)
        self.flags = int(flags)
        self.effect = int(effect)

    def payload(self) -> bytes:
        """Serialise the 56-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                  # index, spare2
        buf += struct.pack("<i", STRAT_MISSION_OBJECTIVE)
        buf += struct.pack("<2i", 0, 0)                  # Type, ExtraData
        buf += struct.pack("<i", self.description)       # +20
        buf += struct.pack("<i", self.complete)          # +24
        buf += struct.pack("<i", self.order)             # +28
        buf += struct.pack("<i", self.flags)             # +32
        buf += struct.pack("<i", self.effect)            # +36
        # Both arrays are empty in every retail objective, so the counts are
        # zero and nothing follows them. `GetStrategySize()` in the engine
        # counts six fields where `fill_data_block` writes nine; retail's 56
        # bytes agree with the writer, so the two pads are included.
        buf += struct.pack("<4i", 0, 0, 0, 0)            # +40..+52
        assert len(buf) == OBJECTIVE_BYTES, len(buf)
        return bytes(buf)


class TextMessageStrategyChunk(Chunk):
    """AVPSTRAT — one on-screen message, as a 32-byte record.

    `TextMessageStrategy` (`Strachnk.hpp:726`) holds a single index into the
    game's text, so a message says nothing a level has to supply beyond a
    number. All 314 retail payloads are 32 bytes.
    """

    identifier = "AVPSTRAT"

    def __init__(self, message_string=0, flags=0, pad2=0):
        self.message_string = int(message_string)
        self.flags = int(flags)
        self.pad2 = int(pad2)

    def payload(self) -> bytes:
        """Serialise the 32-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)
        buf += struct.pack("<i", STRAT_TEXT_MESSAGE)
        buf += struct.pack("<2i", 0, 0)
        buf += struct.pack("<3i", self.message_string, self.flags, self.pad2)
        assert len(buf) == MESSAGE_BYTES, len(buf)
        return bytes(buf)


class MissionObjective:
    """A level's end condition: a marker, and the name a switch will aim at.

    `VirtualObjectChunk` carries it and it needs no geometry. What matters is
    the position, where the goal is shown, and the NAME -- the id a switch sends
    its request to is `MD5("VirtOb" + name)` and nothing else, so renaming the
    object in Blender changes the id a switch must use.

    Retail names these after the switch that finishes them:
    `endstrategyareaswitch`, `endmissionstrategy`, `VENDOFLEVELb`. An objective
    on its own does not end a level: `Objsetup.cpp:3329` builds a separate
    completion block, and `BH_TYPES.C:2471` routes the request that fires it.
    """

    def __init__(self, name, position, description=0, complete=0, order=0):
        self.name = str(name)
        self.position = tuple(int(v) for v in position)
        self.description = int(description)
        self.complete = int(complete)
        self.order = int(order)

    def chunk(self):
        """Return the VIOBJECT to write into SPECLOBJ."""
        from .trigger import VirtualObjectChunk

        return VirtualObjectChunk(
            self.name,
            MissionObjectiveStrategyChunk(
                self.description, self.complete, self.order),
            location=self.position,
            size=DEFAULT_OBJECTIVE_SIZE,
        )
