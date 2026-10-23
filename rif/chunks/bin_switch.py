"""AVPSTRAT for a binary switch — `BinSwitchStrategy`, strategy type 52.

The lever the player operates with `Operate`: aim at it within three metres
(`TRIGGERS.C:20-22`) and the nearest operable thing in view fires. Four of them
exist in the shipped levels, all in `Genshd1.RIF`, all identical apart from their
target -- four generator switches in one shed.

Layout, `Strachnk.hpp:405-425` and `Strachnk.cpp:888-904`:

    + 0  index        i32
    + 4  spare2       i32
    + 8  StrategyType i32   = 52
    +12  Type         i32
    +16  ExtraData    i32
    +20  flags        i32
    +24  Mode         i32
    +28  Time         i32
    +32  Security     i32
    +36  Target       SwitchTarget { ObjectID(8) + request(4) }

48 bytes of payload, and `LinkSwitch` (61) is this plus a list of other switch
ids that fire with it.

**`request` is not "on" or "off".** `RequestState` reads its lowest bit as a
state and hands the rest to the target (`BH_TYPES.C:2150-2154`), and each target
interprets that bit its own way: a proximity door unlocks on 1 and locks on 0, a
platform lift sets `Enabled = state`, a generator sets `Active = state`. The
comments in the dispatch call it "rest state" (0) and "unusual state" (1), which
is the honest description -- what it means depends entirely on what it is sent to.

The switch sends `request` when pressed and `request ^ 1` when released
(`bh_binsw.c:387` and `:445`), so the two are always opposites.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_BIN_SWITCH = 52

BIN_SWITCH_PAYLOAD_BYTES = 48

# `Strachnk.hpp:411-413`.
BIN_SWITCH_FLAG_STARTS_ON = 0x00000001
BIN_SWITCH_FLAG_OFF_MESSAGE_SAME = 0x00000002
BIN_SWITCH_FLAG_OFF_MESSAGE_NONE = 0x00000004

# `BH_BINSW.H:25-34`.
MODE_TIMER = 0
MODE_WAIT = 1
MODE_TOGGLE = 2
MODE_MOVING = 3
MODE_TIME_DELAY = 4
MODE_TIME_DELAY_AUTOEXEC = 5

DEFAULT_MODE = MODE_WAIT

# What a state bit means, per target type. Read from `BH_TYPES.C:2180-2400`.
# The key is the label the panel shows; the pair is (state 1, state 0).
TARGET_ACTIONS = {
    "door": ("Unlock", "Lock"),
    "lift_door": ("Open", "Close"),
    "switch_door": ("Open", "Close"),
    "platform": ("Enable", "Disable"),
    "autogun": ("Activate", "Deactivate"),
    "generator": ("Activate", "Deactivate"),
    "light": ("Brighten", "Dim"),
    "switch": ("Unusual state", "Rest state"),
    "unknown": ("State 1", "State 0"),
}


def encode_request(press_state, extra=0):
    """Return the packed request word: `(extra << 1) | state`."""
    state = 1 if press_state else 0
    return ((int(extra) << 1) | state) & 0xFFFFFFFF


def decode_request(word):
    """Return (press_state, extra) from a packed request word."""
    word = int(word) & 0xFFFFFFFF
    return (word & 1), (word >> 1)


class BinSwitchStrategyChunk(Chunk):
    """AVPSTRAT — a binary switch, as a 48-byte record."""

    identifier = "AVPSTRAT"

    def __init__(self, flags=0, mode=DEFAULT_MODE, time=0, security=0,
                 target_id=b"\x00" * 8, request=0, index=0, spare2=0, type_=0,
                 extra_data=0):
        self.flags = int(flags)
        self.mode = int(mode)
        self.time = int(time)
        self.security = int(security)
        self.target_id = bytes(target_id).ljust(8, b"\x00")[:8]
        self.request = int(request)
        self.index = int(index)
        self.spare2 = int(spare2)
        self.type_ = int(type_)
        self.extra_data = int(extra_data)

    def payload(self) -> bytes:
        """Serialise the 48-byte record."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.spare2)
        buf += struct.pack("<i", STRAT_BIN_SWITCH)
        buf += struct.pack("<i", self.type_)
        buf += struct.pack("<i", self.extra_data)
        buf += struct.pack("<4i", self.flags, self.mode, self.time,
                           self.security)
        buf += self.target_id
        buf += struct.pack("<i", self.request)
        assert len(buf) == BIN_SWITCH_PAYLOAD_BYTES, len(buf)
        return bytes(buf)
