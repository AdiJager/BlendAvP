"""AreaSwitchStrategy — a trigger volume that sends requests to other objects.

`StratAreaSwitch` (68) is the commonest trigger in the game: 690 of them across
40 levels. It is a `MultiSwitchStrategy` (`Strachnk.hpp:506`) plus a box, which
is why the box lives in the strategy rather than in `VOBJPROP` -- the object it
is attached to is virtual and has no geometry of its own.

The engine walks the box against an object's world position
(`BH_BINSW.C:320`), scaled by `local_scale` (`Objsetup.cpp:1560`), exactly as a
death volume is. So the values are absolute world millimetres and take the same
`blender_to_rif_axis` conversion. Three features in this project shipped without
it and each needed its own repair; it is applied here from the start.

What the fields mean, from the shipping data and the tools documentation:

    RestState   "Rest State On" -- where the switch sits when nothing acts
    Mode        1 Wait, 2 Toggle, 0/4/5 the time-based modes. `Wait` is retail's
                commonest at 548 of 690, which is what the documentation calls
                it: "The most common type of switch"
    Time        fixed point seconds; 65536 is one second. Only the time-based
                modes use it: every Mode 0, 4 and 5 has one, and 94-97% of
                Mode 1 and 2 have none
    Security    unused. The documentation calls it "a hangover from the
                original version of AvP ... we don't tend to use this nowadays",
                and it is zero in all 690
    Targets     ObjectID plus a request. Request 0 is an off message and 1 is
                an on message -- "(on)/(off) represent the form of message that
                that switch will send to the target"
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

STRAT_MULTI_SWITCH = 66
STRAT_AREA_SWITCH = 68

# Switch modes, as the shipping data sorts them. The documentation names five
# (Timer, Time Delay, Time Delay Autoexec, Wait, Toggle) and retail's Mode
# splits into exactly the same two groups: three that always carry a Time and
# two that almost never do.
MODE_TIMER = 0
MODE_WAIT = 1
MODE_TOGGLE = 2
MODE_TIME_DELAY = 4
MODE_TIME_DELAY_AUTOEXEC = 5

MODE_NAMES = {
    MODE_TIMER: "Timer",
    MODE_WAIT: "Wait",
    MODE_TOGGLE: "Toggle",
    MODE_TIME_DELAY: "Time Delay",
    MODE_TIME_DELAY_AUTOEXEC: "Time Delay (Autoexec)",
}

# Which modes carry a duration. Read off the data rather than guessed: every
# Mode 0, 4 and 5 has a non-zero Time, and 537 of 548 Mode 1 have a zero one.
TIMED_MODES = (MODE_TIMER, MODE_TIME_DELAY, MODE_TIME_DELAY_AUTOEXEC)

# Requests a switch can send. The documentation displays these as (on)/(off).
REQUEST_OFF = 0
REQUEST_ON = 1

REQUEST_NAMES = {REQUEST_OFF: "off", REQUEST_ON: "on"}

# `Strachnk.hpp:461`.
MULTISWITCH_FLAG_SWITCH_UPDATED = 0x80000000
MULTISWITCH_FLAG_OFF_MESSAGE_SAME = 0x00000002
MULTISWITCH_FLAG_OFF_MESSAGE_NONE = 0x00000004

# Set in all 690 retail switches. It records that the strategy has been through
# the editor's update, so it is written rather than offered.
DEFAULT_SWITCH_FLAGS = MULTISWITCH_FLAG_SWITCH_UPDATED

# One second in the fixed point the engine uses for Time.
ONE_FIXED = 65536

# Retail's switches carry anything from none to one hundred and sixty-three
# targets: Invasion_A.RIF has a switch with 163, and its payload of 2008 bytes
# is exactly 8 + 44 + 163 * 12. This bound has been wrong twice -- first three,
# from a partial sample, then sixty-four, from a partial set -- so it is now set
# well clear of the data and says so. It exists to catch a runaway list, not to
# describe what retail does.
MAX_TARGETS = 4096


class SwitchTarget:
    """One object a switch sends to, and which message it sends."""

    __slots__ = ("target_id", "request")

    def __init__(self, target_id, request=REQUEST_ON):
        self.target_id = bytes(target_id)
        assert len(self.target_id) == 8, len(self.target_id)
        self.request = int(request)

    def pack(self) -> bytes:
        """Serialise the 12-byte pair."""
        return self.target_id + struct.pack("<i", self.request)


class MultiSwitchStrategyChunk(Chunk):
    """AVPSTRAT — a 66, with its target list and no volume.

    This is the base `AreaSwitchStrategy` builds on, so its fields are the ones
    a switch has whether or not it has a box to walk into.
    """

    identifier = "AVPSTRAT"
    strategy_type = STRAT_MULTI_SWITCH

    def __init__(self, targets=(), mode=MODE_WAIT, time_seconds=0.0,
                 rest_state=0, security=0,
                 flags=DEFAULT_SWITCH_FLAGS, links=()):
        self.targets = list(targets)
        self.mode = int(mode)
        self.time = int(round(float(time_seconds) * ONE_FIXED))
        self.rest_state = int(rest_state)
        self.security = int(security)
        self.flags = int(flags)
        self.links = list(links)
        assert len(self.targets) <= MAX_TARGETS, len(self.targets)

    def payload(self) -> bytes:
        """Serialise the strategy, whose length depends on the two lists."""
        buf = bytearray()
        buf += struct.pack("<2i", 0, 0)                     # index, spare2
        buf += struct.pack("<i", self.strategy_type)
        buf += struct.pack("<2i", 0, 0)                     # Type, ExtraData

        buf += struct.pack("<4i", self.rest_state, self.mode, self.time,
                           self.security)
        buf += struct.pack("<i", len(self.targets))
        for target in self.targets:
            buf += target.pack()

        buf += struct.pack("<i", len(self.links))
        for link in self.links:
            buf += bytes(link)

        # `pad1` was `UseRestStateAfter`; retail leaves it zero in all 690.
        # The flags go out unsigned: `SwitchUpdated` is `0x80000000`, which a
        # signed `i` refuses -- the same overflow `MODULE_FLAG_AIRDUCT` caused,
        # and the reason this is packed as a bit mask rather than a number.
        buf += struct.pack("<iI", 0, self.flags)

        return bytes(buf)


class AreaSwitchStrategyChunk(MultiSwitchStrategyChunk):
    """AVPSTRAT — a 68: the same switch, with a volume to walk into.

    The engine tests this box against an object's world position, so the values
    are absolute world millimetres scaled by `local_scale`
    (`Objsetup.cpp:1560`), in the same frame as a death volume's.
    """

    strategy_type = STRAT_AREA_SWITCH

    def __init__(self, low, high, targets=(), mode=MODE_WAIT, time_seconds=0.0,
                 rest_state=0, security=0,
                 flags=DEFAULT_SWITCH_FLAGS, links=()):
        super().__init__(targets, mode, time_seconds, rest_state, security,
                         flags, links)
        self.low = tuple(int(v) for v in low)
        self.high = tuple(int(v) for v in high)

    def payload(self) -> bytes:
        """Serialise the base, then the box and its two pads."""
        buf = bytearray(super().payload())
        buf += struct.pack("<3i", *self.low)
        buf += struct.pack("<3i", *self.high)
        buf += struct.pack("<2i", 0, 0)                     # area_pad1, pad2
        return bytes(buf)


class MultiSwitch:
    """A switch with no volume: a condition or a delay in a chain.

    `VirtualObjectChunk` carries it, and it is a marker rather than a box --
    685 of its 843 retail instances carry the default `size` of 1000 -- because
    nothing walks into it. Other switches drive it, which is how the game
    expresses "only do this when every condition is met".
    """

    def __init__(self, name, position, targets=(), mode=MODE_WAIT,
                 time_seconds=0.0, rest_state=0, flags=DEFAULT_SWITCH_FLAGS):
        self.name = str(name)
        self.position = tuple(int(v) for v in position)
        self.targets = list(targets)
        self.mode = int(mode)
        self.time_seconds = float(time_seconds)
        self.rest_state = int(rest_state)
        self.flags = int(flags)

    def chunk(self):
        """Return the VIOBJECT to write into SPECLOBJ."""
        from .objective import DEFAULT_OBJECTIVE_SIZE
        from .trigger import VirtualObjectChunk

        return VirtualObjectChunk(
            self.name,
            MultiSwitchStrategyChunk(
                self.targets, self.mode, self.time_seconds, self.rest_state,
                flags=self.flags),
            location=self.position,
            size=DEFAULT_OBJECTIVE_SIZE,
        )


class AreaSwitch:
    """A trigger volume, as one object to place and export.

    `VirtualObjectChunk` carries it, the same as a death volume and a mission
    objective. The box is what the engine tests, so the object is measured
    rather than marked.
    """

    def __init__(self, name, low, high, targets=(), mode=MODE_WAIT,
                 time_seconds=0.0, rest_state=0, flags=DEFAULT_SWITCH_FLAGS):
        self.name = str(name)
        self.low = tuple(int(v) for v in low)
        self.high = tuple(int(v) for v in high)
        self.targets = list(targets)
        self.mode = int(mode)
        self.time_seconds = float(time_seconds)
        self.rest_state = int(rest_state)
        self.flags = int(flags)

    def chunk(self):
        """Return the VIOBJECT to write into SPECLOBJ."""
        from .trigger import VirtualObjectChunk

        centre = tuple((self.low[i] + self.high[i]) // 2 for i in range(3))
        span = max(self.high[i] - self.low[i] for i in range(3))
        return VirtualObjectChunk(
            self.name,
            AreaSwitchStrategyChunk(
                self.low, self.high, self.targets, self.mode,
                self.time_seconds, self.rest_state, flags=self.flags),
            location=centre,
            size=max(span, 1),
        )
