"""AVPSTRAT and its container OBJPRJDT — the strategy attached to an object.

AvP does not model a pickup as a special object type. An object either becomes
a **module** (geometry the player walks through) or a **placed object**, and the
difference is one flag on `OBJHEAD1`:

    if (ob->get_header()->flags & OBJECT_FLAG_PLACED_OBJECT)
        deal_with_placed_object(ob, ...);      // Projload.cpp:2078
    else
        copy_to_module(ob, ...);

A placed object carries its behaviour in `OBJPRJDT > AVPSTRAT`, and
`deal_with_placed_object` reads the pickup identity straight out of it:

    tdio->typeId  = (INANIMATEOBJECT_TYPE) asc->Strategy->Type;   // Objsetup.cpp:2218
    tdio->subType = asc->Strategy->ExtraData;

**`AVPSTRAT` is polymorphic.** The int at payload +8 selects one of about thirty
subclasses, each with its own size (STRACHNK.CPP:32). Pickups and decorations
use `StratNewSimpleObject` (62), which is the only strategy shipping data uses
in bulk — 7416 of the 9213 chunks across the 63 retail levels.

**`SimpleStrategy` shadows the base fields.** It declares its own `Type` and
`ExtraData`, and its constructor skips the base fields before reading them
(STRACHNK.CPP:488):

    data_start += AvpStrat::GetStrategySize();   // skip the base
    Type = *((int *)data_start);                 // read our own

So the base `Type`/`ExtraData` at +12/+16 are **always zero** in retail, and the
real values sit at **+20/+24**. Reading the base ones yields `IOT_Static` for
every object — a plausible-looking answer that is entirely wrong.

Layout of one 52-byte record, every field confirmed against 7416 retail chunks:

    +0   i32 index                 always 0 for retail
    +4   i32 spare2                always 0
    +8   i32 StrategyType          62 = StratNewSimpleObject
    +12  i32 Type (base)           always 0, shadowed
    +16  i32 ExtraData (base)      always 0, shadowed
    +20  i32 Type                  IOT_* category
    +24  i32 ExtraData             subType within the category
    +28  i32 mass                  pickups 50, furniture 1000-2000
    +32  i32 integrity             bit field; see INTEGRITY_MASK
    +36  i32 flags                 retail: 1, or 17 for heavy furniture
    +40  i32 target_request        0, or a strategy link
    +44  ObjectID targetID         8 bytes, zero unless linked

    total 52
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk, ChunkWithChildren

# Payload size of one SimpleStrategy record, and the size retail ships.
RECORD_BYTES = 52

# `StratNewSimpleObject` — the strategy that covers pickups and decorations.
# `StratSimpleObject` (53) exists in the source but appears in **none** of the
# 63 retail levels, so it is not the one to write.
STRAT_NEW_SIMPLE_OBJECT = 62

# `INANIMATEOBJECT_TYPE` (PVISIBLE.H:14). Retail also uses 16 for placed
# lights, which the 1999 enum does not name.
IOT_STATIC = 0
IOT_FURNITURE = 1
IOT_WEAPON = 2
IOT_AMMO = 3
IOT_HEALTH = 4
IOT_ARMOUR = 5
IOT_FIELD_CHARGE = 15
IOT_LIGHT = 16

# `get_integrity()` masks this off: the top bit is junk left in a spare.
INTEGRITY_MASK = 0x7FFFFFFF

# The top bit is a spares marker the engine masks off. Retail always sets it,
# so a value read straight from the file looks like a large negative number.
SPARES_MARKER = 0x80000000


def compose_integrity(value):
    """Return a file-ready integrity for a plain 0-21 style value.

    `SimpleStrategy::get_integrity()` masks `SparesDontContainJunk` off, so the
    top bit carries no meaning -- but every retail record has it set, and writing
    a bare small number would be the one thing shipping data never does.
    """
    return SPARES_MARKER | (value & INTEGRITY_MASK)


# What retail uses for a pickup: mass 50 and this integrity, in 96%+ of cases.
DEFAULT_MASS = 50
DEFAULT_INTEGRITY = 0x80000002
DEFAULT_FLAGS = 1


class AVPStrategyChunk(Chunk):
    """AVPSTRAT — the strategy attached to a placed object."""

    identifier = "AVPSTRAT"

    def __init__(self, type_id, sub_type=0, mass=DEFAULT_MASS,
                 integrity=DEFAULT_INTEGRITY, flags=DEFAULT_FLAGS,
                 target_request=0, target_id=0,
                 strategy_type=STRAT_NEW_SIMPLE_OBJECT):
        """Store one SimpleStrategy record.

        type_id : INANIMATEOBJECT_TYPE category, e.g. IOT_WEAPON.
        sub_type : the concrete item inside that category, e.g. WEAPON_SMARTGUN.
        target_id : 8-byte ObjectID, zero unless the strategy links elsewhere.
        """
        self.type_id = type_id
        self.sub_type = sub_type
        self.mass = mass
        self.integrity = integrity
        self.flags = flags
        self.target_request = target_request
        self.target_id = target_id
        self.strategy_type = strategy_type

    def payload(self) -> bytes:
        """Serialise the 52-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                    # index, spare2
        buf += struct.pack("<i", self.strategy_type)
        # The base class fields are shadowed and always zero in retail.
        buf += struct.pack("<2i", 0, 0)
        buf += struct.pack("<2i", self.type_id, self.sub_type)
        buf += struct.pack("<i", self.mass)
        # Unsigned: the spares bit makes this 0x80000000-ish, which does not fit
        # a signed int.
        buf += struct.pack("<I", self.integrity & 0xFFFFFFFF)
        buf += struct.pack("<2i", self.flags, self.target_request)
        buf += struct.pack("<Q", self.target_id & 0xFFFFFFFFFFFFFFFF)
        return bytes(buf)


# StrategyType values from Strachnk.hpp:25/34.
STRAT_ENEMY = 69
STRAT_GENERATOR = 79

# Objsetup.cpp:2464 -- what the generator's creatures do.
MISSION_NONE = 0          # leave the default, MM_Wait_Then_Wander
MISSION_WANDER = 1
MISSION_GUARD = 2
MISSION_NON_COMBATANT = 3
MISSION_LOCAL_GUARD = 4
MISSION_PATHFINDER = 5    # follows a patrol route


def compose_mission_data(path_id, start_object_index):
    """Return the packed `ExtraMissionData` for a patrol.

    `Objsetup.cpp:2478` splits it in half: the route id in the low 16 bits and
    the object index of the starting module in the high 16. The engine then
    looks that module up in the route's module list to find the step to begin
    at, and treats a module that is not on the route as an error rather than
    falling back.
    """
    return (int(path_id) & 0xFFFF) | ((int(start_object_index) & 0xFFFF) << 16)


class GeneratorStrategyChunk(Chunk):
    """AVPSTRAT — the strategy of a generator, as a 52-byte record.

    `mission` decides what the spawned creatures do, and `mission_data` carries
    the route and starting module for `MISSION_PATHFINDER`. `strategy_type`
    picks the layout, because the two differ -- see the module docstring.
    """

    identifier = "AVPSTRAT"

    def __init__(self, mission=MISSION_NONE, mission_data=0,
                 strategy_type=STRAT_ENEMY, type_id=0, extra_data=0):
        self.mission = int(mission)
        self.mission_data = int(mission_data)
        self.strategy_type = int(strategy_type)
        self.type_id = int(type_id)
        self.extra_data = int(extra_data)

    def payload(self) -> bytes:
        """Serialise the record, laying the fields out by strategy type."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                  # index, spare2
        buf += struct.pack("<i", self.strategy_type)     # +8
        buf += struct.pack("<i", self.type_id)           # +12 Type
        buf += struct.pack("<i", self.extra_data)        # +16 ExtraData
        buf += struct.pack("<i", self.mission)           # +20 MissionType

        if self.strategy_type == STRAT_GENERATOR:
            buf += struct.pack("<i", self.mission_data)  # +24
            buf += struct.pack("<6i", *([0] * 6))        # +28..+51
        else:
            buf += struct.pack("<i", 0)                  # +24 target_request
            buf += struct.pack("<Q", 0)                  # +28 DeathTarget
            buf += struct.pack("<i", self.mission_data)  # +36
            buf += struct.pack("<3i", *([0] * 3))        # +40..+51
        return bytes(buf)


class ObjectProjectDataChunk(ChunkWithChildren):
    """OBJPRJDT — container for the project-side data of a placed object.

    Only one child matters to us: `AVPSTRAT`. The engine looks the strategy up
    as `ob->lookup_single_child("OBJPRJDT")->lookup_single_child("AVPSTRAT")`,
    so both levels are required (Objsetup.cpp:2235).
    """

    def __init__(self, children=None):
        """Store the container children."""
        super().__init__("OBJPRJDT", children)
