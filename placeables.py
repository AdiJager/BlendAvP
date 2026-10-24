"""Placeable AvP gameplay objects: spawns, NPC generators, navigation.

This module is the **menu skeleton** for the Beta milestone. Every entry can be
placed in the scene and inspected, and each carries the properties the exporter
will eventually write into the RIF. It deliberately contains **no export
logic** — the point is to evaluate whether the menu is convenient to work with
before investing in the writer.

Layout, per the reconnaissance in `context/HANDOFF_S6_F18.md`:

    Add > AvP
      Player Start        single player (AVPSTART) and multiplayer spawns
      NPC Generator       the ten generator types, plus weighted / default
      Navigation          alien waypoints, their links, human patrol paths
      Gameplay            the one global generator data chunk per level

**Category first, then entry.** The first level answers "where does this work",
not "which chunk is it": the alien generator types are split from the
single-player-only ones because a skirmish map can never spawn the latter
(`Objsetup.cpp:2993`).

Menu entries are generated from the `PLACEABLES` table so that every one of them
carries its own descriptive tooltip. Blender takes a menu item's tooltip from
the operator class, so a single parameterised operator would have shown the same
text for every entry — the table exists to avoid exactly that.

`from __future__ import annotations` must NOT be used here: it turns the
property annotations into strings, and Blender registers properties by reading
the annotation value, so the fields would silently never exist. `operators.py`
avoids it for the same reason.
"""
import math
import bpy
from bpy.props import (
    BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty,
)

# Marker property: which kind of placeable this object is.
PLACEABLE_PROP = "rif_placeable"

# Legacy marker the export code already looks for when resolving the single
# player start. Kept in sync with operators.py; the new menu entries set it so
# the existing export path keeps finding the spawn without changes.
MARKER_PROP = "rif_marker_type"
PLAYER_START_MARKER = "PLAYER_START"

# Categories, in menu order.
CAT_MODULE = "MODULE"
CAT_PLAYER_START = "PLAYER_START"
CAT_GENERATOR = "GENERATOR"
CAT_NAVIGATION = "NAVIGATION"
CAT_GAMEPLAY = "GAMEPLAY"
CAT_PICKUP = "PICKUP"
CAT_OBJECT = "OBJECT"
CAT_SOUND = "SOUND"
CAT_EFFECTS = "EFFECTS"
CAT_MORPH = "MORPH"

CATEGORY_LABELS = (
    (CAT_MODULE, "Modules"),
    (CAT_PLAYER_START, "Player Start"),
    (CAT_GENERATOR, "NPC Generator"),
    (CAT_PICKUP, "Pickups"),
    (CAT_OBJECT, "Objects"),
    (CAT_SOUND, "Sound"),
    (CAT_EFFECTS, "Effects"),
    (CAT_NAVIGATION, "Navigation"),
    (CAT_GAMEPLAY, "Gameplay"),
)

# Generator kinds, matching the roles found in retail (`AVPGENER.type` + flags).
KIND_PLAYER_START_SP = "PLAYER_START_SP"
KIND_PLAYER_START_MP = "PLAYER_START_MP"
KIND_NPC = "NPC"
KIND_DEFAULT_ENEMY = "DEFAULT_ENEMY"
KIND_WEIGHTED = "WEIGHTED"
KIND_WAYPOINT = "WAYPOINT"
KIND_PATROL_PATH = "PATROL_PATH"
KIND_GLOBAL_GENERATOR = "GLOBAL_GENERATOR"
# A placed object carrying an AVPSTRAT strategy: not a module.
KIND_PICKUP = "PICKUP"
# Scenery and lights: same strategy 62, same placed-object path, a
# different INANIMATEOBJECT_TYPE.
KIND_OBJECT = "OBJECT"
# A mesh cube with the AvP Object switches preset, so the author starts from
# something that already behaves instead of from a cube with a label.
KIND_SIMPLE_OBJECT = "SIMPLE_OBJECT"
# Shell geometry: a room wound inward, and a door with its module flags on.
KIND_MODULE_ROOM = "MODULE_ROOM"
KIND_MODULE_DOOR = "MODULE_DOOR"
# Presets that make ordinary geometry rather than a placeholder marker.
# They must not carry PLACEABLE_PROP: the AvP Object panel polls on its
# absence, so setting it would hide the panel the author needs.
GEOMETRY_KINDS = (KIND_SIMPLE_OBJECT, KIND_MODULE_ROOM, KIND_MODULE_DOOR)
# Entries that exist so the menu is complete and the shape of the tool is
# visible, but write nothing yet. They are reported as unsupported on
# export rather than dropped in silence.
KIND_PLACEHOLDER = "PLACEHOLDER"
# The far end of an object's track (OBJTRAK2). An empty, not a mesh:
# it marks where movement stops and never reaches the level.
KIND_TRACK_END = "TRACK_END"
KIND_DEATH_VOLUME = "DEATH_VOLUME"
KIND_MISSION_OBJECTIVE = "MISSION_OBJECTIVE"
KIND_AREA_SWITCH = "AREA_SWITCH"
KIND_MULTI_SWITCH = "MULTI_SWITCH"
KIND_SELF_DESTRUCT = "SELF_DESTRUCT"
KIND_MESSAGE = "MESSAGE"
KIND_PARTICLE_GENERATOR = "PARTICLE_GENERATOR"
# A SOUNDOB2 emitter, drawn as a wireframe sphere whose size is its range.
KIND_SOUND = "SOUND"

# Empty display type per kind: a generator is a point, a waypoint is a volume.
DISPLAY_BY_KIND = {
    KIND_PLAYER_START_SP: "ARROWS",
    KIND_PLAYER_START_MP: "CONE",
    KIND_NPC: "SPHERE",
    KIND_DEFAULT_ENEMY: "SPHERE",
    KIND_WEIGHTED: "SPHERE",
    KIND_WAYPOINT: "CUBE",
    KIND_PATROL_PATH: "CIRCLE",
    KIND_GLOBAL_GENERATOR: "PLAIN_AXES",
    # A sphere reads as "a point in space", which is what the far end of a
    # track is. It has no volume and never reaches the level.
    KIND_TRACK_END: "SPHERE",
}

# The ten `AVPGENER.type` values, with the network eligibility that decides
# which menu group they belong to (`Objsetup.cpp:2993-3034`).
NPC_TYPES = (
    (1, "Alien Drone", "SKIRMISH", "SPHERE"),
    (5, "Predalien", "SKIRMISH", "SPHERE"),
    (8, "Praetorian", "SKIRMISH", "SPHERE"),
    (2, "Predator", "SOLO", "SPHERE"),
    (3, "Facehugger", "SOLO", "SPHERE"),
    (4, "Xenoborg", "SOLO", "SPHERE"),
    (6, "Queen", "SOLO", "CONE"),
    (7, "Marine", "SOLO", "SPHERE"),
    (9, "Sentry Turret", "SOLO", "CONE"),
)

# Race bits, independent of each other (`AVPGENFLAG_AVPGAMEMODE*`).
RACE_MARINE = 0x00000800
RACE_ALIEN = 0x00001000
RACE_PREDATOR = 0x00002000


class RIF_OT_switch_target_add(bpy.types.Operator):
    """Add a target row to the selected switch."""

    bl_idname = "rif.switch_target_add"
    bl_label = "Add target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Append an empty target to the active object's switch."""
        obj = context.active_object
        if obj is None or not hasattr(obj, "rif_placeable"):
            return {"CANCELLED"}
        obj.rif_placeable.switch_targets.add()
        obj.rif_placeable.switch_target_index =             len(obj.rif_placeable.switch_targets) - 1
        return {"FINISHED"}


class RIF_OT_switch_target_remove(bpy.types.Operator):
    """Remove the last target row from the selected switch."""

    bl_idname = "rif.switch_target_remove"
    bl_label = "Remove target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Drop the last target, so the panel and the file stay in step."""
        obj = context.active_object
        if obj is None or not hasattr(obj, "rif_placeable"):
            return {"CANCELLED"}
        targets = obj.rif_placeable.switch_targets
        if not len(targets):
            return {"CANCELLED"}
        targets.remove(len(targets) - 1)
        obj.rif_placeable.switch_target_index = len(targets) - 1
        return {"FINISHED"}


class RIF_ModuleLink(bpy.types.PropertyGroup):
    """One module this module is linked to, by name."""

    target_name: bpy.props.StringProperty(
        name="Module",
        description=(
            "Name of the module to link to. The engine matches on an id "
            "derived from the name, so renaming that object breaks the link"),
        default="")


class RIF_OT_module_link_add(bpy.types.Operator):
    """Append an empty link row to the active module."""

    bl_idname = "rif.module_link_add"
    bl_label = "Add link"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Add one row and leave the cursor on it."""
        obj = context.active_object
        if obj is None or not hasattr(obj, "rif_module_links"):
            return {"CANCELLED"}
        obj.rif_module_links.add()
        return {"FINISHED"}


class RIF_OT_module_link_remove(bpy.types.Operator):
    """Drop the last link row from the active module."""

    bl_idname = "rif.module_link_remove"
    bl_label = "Remove link"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Remove the last row, so the panel and the file stay in step."""
        obj = context.active_object
        if obj is None or not hasattr(obj, "rif_module_links"):
            return {"CANCELLED"}
        links = obj.rif_module_links
        if not len(links):
            return {"CANCELLED"}
        links.remove(len(links) - 1)
        return {"FINISHED"}


class RIF_SwitchTarget(bpy.types.PropertyGroup):
    """One object a switch sends to, named the way the artist named it."""

    target_name: bpy.props.StringProperty(
        name="Object",
        description=(
            "Name of the virtual object to send to -- a kill volume, a mission "
            "objective, another switch. It is the Blender object's name, and "
            "renaming that object breaks this link, because the engine matches "
            "on an id derived from the name"),
        default="")
    # `request` is `(code << 1) | on`. The code is read by the target's own
    # handler, and three kinds of target read it: a mission objective takes a
    # bitmask, a message takes 1 to arm and 2 to arm and show, and a track takes
    # 0, 1 and 2 for start, forward and backward. A death volume and the
    # self destruct ignore it and only look at the on bit.
    target_request: bpy.props.EnumProperty(
        name="Message",
        description=(
            "What to send. 'on' and 'off' are the whole vocabulary for a kill "
            "volume; the numbered codes mean something to a mission objective, "
            "a message or a track, and are ignored by a kill volume. The same "
            "code can mean different things depending on what it is sent to"),
        items=[
            ("1", "on", "Send on. Completes a mission objective, arms a kill "
                        "volume or the self destruct, plays a sound"),
            ("0", "off", "Send off. Disarms, or stops what was running"),
            ("3", "on, code 1",
             "Mission objective: make the objective visible. Message: arm it "
             "without showing it. Track: start forwards"),
            ("5", "on, code 2",
             "Mission objective: make it possible. Message: arm it AND show "
             "the text or the cutscene. Track: start backwards"),
            ("2", "off, code 1",
             "Off, with code 1: a mission objective stops being visible, a "
             "message stops being armed"),
            ("4", "off, code 2", "Off, with code 2"),
            ("17", "on, code 8",
             "On, with code 8. Vaults sends this from three of its multi "
             "switches, so the code is real, but what it does depends on the "
             "target and has not been traced for any of them"),
            ("16", "off, code 8", "Off, with code 8"),
        ],
        default="1")


# The four emitter types and the numbers retail uses for each, from a census
# of all 570 generators in the 63 shipped levels. `probability` is 0 in every
# one of them, so it is not listed.
PARGEN_DEFAULTS = {
    "0": {"frequency": 0.0, "rate": 80, "speed": 0.10},
    "1": {"frequency": 0.0, "rate": 0, "speed": 0.0},
    "2": {"frequency": 0.0, "rate": 0, "speed": 0.0},
    "3": {"frequency": 0.0, "rate": 0, "speed": 0.0},
}


def _apply_particle_defaults(settings, _context):
    """Write the numbers retail uses for the type just chosen.

    A rate of 0 is the game's own value for steam, smoke and flame: the
    engine divides by `max(quantity, 1)`, so it means one particle a second,
    and with speed 0 that particle does not move. That is what the game's
    steam looks like, and an artist who wants a plume has to raise both.
    """
    chosen = PARGEN_DEFAULTS.get(settings.particle_type)
    if not chosen:
        return
    settings.particle_frequency = chosen["frequency"]
    settings.particle_quantity = chosen["rate"]
    settings.particle_speed = chosen["speed"]


def _pickup_variant_items(self, context):
    """Return the variant list for whichever pickup category this is.

    Blender calls this on every draw, so the list follows the category without
    anything having to be reset by hand.
    """
    variants = PICKUP_VARIANTS.get(self.pickup_type)
    if not variants:
        return [("0", "- none -", "This category has one variant")]
    return [(str(number), label, "") for number, label in variants]


class RIF_PlaceableSettings(bpy.types.PropertyGroup):
    """Per-object settings for a placed AvP gameplay object."""

    kind: bpy.props.StringProperty(
        name="Kind",
        description="Which AvP chunk this object becomes on export",
        default=KIND_NPC,
    )
    npc_type: bpy.props.IntProperty(
        name="NPC Type",
        description=(
            "AVPGENER.type. 1 Drone, 5 Predalien, 8 Praetorian spawn in a "
            "network game; 2/3/4/6/7/9 are single player only"
        ),
        default=1,
        min=0,
        max=9,
    )
    sound_wav: bpy.props.EnumProperty(
        name="Sound",
        description=(
            "File played by this emitter, from the game's own library. The "
            "list is ordered so the sounds retail uses most are first; type to "
            "filter it"),
        # Deferred: the callback is defined below this class.
        items=lambda self, context: _sound_items(self, context))
    sound_inner_fraction: bpy.props.FloatProperty(
        name="Inner",
        description=(
            "How much of the sphere is at full volume, as a fraction of its "
            "radius: 0.25 means the nearest quarter is loud and it fades to "
            "silence by the edge. Cannot exceed the radius, which is what the "
            "engine expects"),
        default=0.25, min=0.0, max=1.0, subtype="FACTOR")
    sound_volume: bpy.props.IntProperty(
        name="Volume", description="max_volume, 0 to 127",
        default=127, min=0, max=127)
    sound_pitch: bpy.props.IntProperty(
        name="Pitch",
        description=(
            "Pitch shift in 1/1536ths of an octave: 128 units is one semitone"),
        default=0, min=-100000, max=100000)
    sound_not_at_start: bpy.props.BoolProperty(
        name="Not playing at start",
        description="Sets flag 0x1: the emitter stays silent until triggered",
        default=False)
    sound_no_loop: bpy.props.BoolProperty(
        name="No loop",
        description="Sets flag 0x2: play once rather than repeating",
        default=False)
    mission: bpy.props.EnumProperty(
        name="Mission",
        description=(
            "What the creatures this generator produces spend their time "
            "doing. Objsetup.cpp:2464 switches on this value. 'Patrol route' is "
            "the only one that needs a route, and the creature will not leave "
            "it"),
        items=[
            ("0", "Default", "Leave the creature's own behaviour alone"),
            ("1", "Wander", "Roam the level"),
            ("2", "Guard", "Hold position"),
            ("3", "Non-combatant", "Civilians: flee rather than fight"),
            ("4", "Local guard", "Guard a small area"),
            ("5", "Patrol route", "Walk a patrol route, back and forth"),
        ],
        default="0")
    patrol_path: bpy.props.EnumProperty(
        name="Route",
        description=(
            "The patrol route to walk. Only used when Mission is 'Patrol "
            "route'. Every point of the route has to lie inside a module, and "
            "this generator has to stand in one of them, or the engine reports "
            "'Error setting up path' and asserts"),
        items=lambda self, context: _patrol_items(self, context))
    objective_description: bpy.props.IntProperty(
        name="Description text",
        description=(
            "Index into the game's own english.txt, 1-based. 0 means blank, "
            "which is what every retail level uses -- the level's text comes "
            "from its mission file, not from the level"),
        default=0, min=0, max=449)
    objective_complete: bpy.props.IntProperty(
        name="Complete text",
        description="Index into english.txt for the completion message; 0 is blank",
        default=0, min=0, max=449)
    objective_order: bpy.props.IntProperty(
        name="Order",
        description=(
            "Objectives are sorted by this and the first is used, so with one "
            "objective it does nothing. Retail leaves every one at 0"),
        default=0, min=0, max=1000)
    switch_mode: bpy.props.EnumProperty(
        name="Mode",
        description=(
            "Wait is the commonest in the game (548 of 690) and simply turns "
            "on and stays on. Toggle lets the player work it. The other three "
            "are the time-based modes and use the Duration below"),
        items=[("1", "Wait", "Turns on and stays on"),
               ("2", "Toggle", "The player can work it"),
               ("0", "Timer", "Sends on, waits, then sends off"),
               ("4", "Time Delay", "Waits, then sends on, and resets"),
               ("5", "Time Delay (Autoexec)",
                "A time delay that starts counting when the level loads")],
        default="1")
    switch_time: bpy.props.FloatProperty(
        name="Duration (s)",
        description=(
            "How long the time-based modes count for. Retail's switches use "
            "whole seconds: one, two, three, five. Ignored by Wait and Toggle"),
        default=2.0, min=0.0, max=600.0)
    switch_rest_on: bpy.props.BoolProperty(
        name="On at rest",
        description=(
            "Where the switch sits before anything acts on it. Off in 689 of "
            "the game's 690 switches"),
        default=False)
    switch_targets: bpy.props.CollectionProperty(type=RIF_SwitchTarget)
    switch_target_index: bpy.props.IntProperty(default=-1)
    self_destruct_timer: bpy.props.IntProperty(
        name="Countdown (s)",
        description=(
            "Seconds from being armed to the level going up. Retail's five use "
            "0, 62 and 70, so 0 is a legal value and means it fires at once"),
        default=70, min=0, max=100000)
    message_string: bpy.props.IntProperty(
        name="Text number",
        description=(
            "Index into the game's own english.txt, counting from the level "
            "messages, 1-based. The engine insists on 1 to 449, so 0 is not a "
            "blank message here the way it is for a mission objective -- it is "
            "an assertion. The game's text file lists what each number says"),
        default=1, min=1, max=449)
    message_shown_at_start: bpy.props.BoolProperty(
        name="Armed at start",
        description=(
            "Whether the message starts active. This does NOT display it: a "
            "message has no per-frame behaviour at all (it appears in "
            "BH_TYPES.C only at init and in the request dispatch), so a switch "
            "has to send it code 2 to show it. What this decides is whether a "
            "switch using the plain code 0 will show it -- that branch tests "
            "active && state, and with this off the message stays silent"),
        default=False)
    particle_type: bpy.props.EnumProperty(
        name="Type",
        description=(
            "The four emitter types, in the engine's own order -- which is not "
            "the obvious one. Spark and Flame light the room as well"),
        items=[("0", "Spark", "Sparks, fifteen at a time, and a flash of light"),
               ("1", "Steam", "Steam, no light"),
               ("2", "Black smoke", "Black smoke, no light"),
               ("3", "Flame", "A flame that lights the room")],
        default="0",
        update=_apply_particle_defaults)
    particle_frequency: bpy.props.FloatProperty(
        name="Frequency (s)",
        description=(
            "Seconds between emissions. SPARK ONLY: the other three take their "
            "interval from Quantity instead, and ignore this"),
        default=0.0, min=0.0, max=600.0)
    particle_quantity: bpy.props.IntProperty(
        name="Rate",
        description=(
            "How many particles per second, for Steam, Black smoke and Flame. "
            "The engine divides the frame by it, so it is a rate and not a "
            "count: a bigger number means MORE particles, and 0 is treated as "
            "1, which is one a second. Spark ignores it and uses Frequency"),
        default=80, min=0, max=65535)
    particle_probability: bpy.props.IntProperty(
        name="Probability (%)",
        description=(
            "Chance of emitting at all, 0 to 100. Retail's commonest value is "
            "0, and the engine tests a random number against it, so 0 means "
            "almost never rather than always"),
        default=0, min=0, max=100)
    particle_speed: bpy.props.FloatProperty(
        name="Speed (m/s)",
        description=(
            "How fast the particles leave. At 0 they all stay at the emitter "
            "and pile up on top of each other, which looks like a single "
            "particle however many are being made -- retail writes 0 for "
            "steam, smoke and flame and 0.10 for sparks. Raise this before "
            "raising Rate"),
        default=0.10, min=0.0, max=600.0)
    particle_active: bpy.props.BoolProperty(
        name="Active",
        description=(
            "Emit from the start. All 570 of the game's generators are active, "
            "and a switch can turn one on or off"),
        default=True)
    particle_sound: bpy.props.EnumProperty(
        name="Sound",
        description=(
            "A sound from the level's Sound Directory. 444 of the game's 570 "
            "emitters have one; '(none)' is a silent emitter. The list is the "
            "same one the Sound object offers, so a file that a placed sound "
            "can play is a file an emitter can play"),
        items=lambda self, context: _sound_items(self, context))
    particle_sound_inner: bpy.props.FloatProperty(
        name="Sound inner (m)",
        description=(
            "Distance at which the sound is at full volume. Retail's sparks "
            "use 1.25 m and its steam 0"),
        default=0.0, min=0.0, max=600.0)
    particle_sound_outer: bpy.props.FloatProperty(
        name="Sound outer (m)",
        description=(
            "Distance at which it falls silent. Retail is consistent here: "
            "18.75 m for every emitter in the game"),
        default=18.75, min=0.0, max=600.0)
    particle_sound_loop: bpy.props.BoolProperty(
        name="Sound loops",
        description=(
            "Repeat while the emitter runs. 503 of the game's 1135 indexed "
            "sounds loop -- steam, fans and lifts do, switches do not"),
        default=True)
    mix_pulse_marine: bpy.props.IntProperty(
        name="Pulse rifle",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_flame_marine: bpy.props.IntProperty(
        name="Flamethrower",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_smart_marine: bpy.props.IntProperty(
        name="Smartgun",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_sadar_marine: bpy.props.IntProperty(
        name="SADAR",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_grenade_marine: bpy.props.IntProperty(
        name="Grenade launcher",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_minigun_marine: bpy.props.IntProperty(
        name="Minigun",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_pistol_marine: bpy.props.IntProperty(
        name="Pistol",
        description=(
            "Relative chance of a marine with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_shotgun_civilian: bpy.props.IntProperty(
        name="Shotgun",
        description=(
            "Relative chance of a civilian with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_pistol_civilian: bpy.props.IntProperty(
        name="Pistol",
        description=(
            "Relative chance of a civilian with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_flame_civilian: bpy.props.IntProperty(
        name="Flamethrower",
        description=(
            "Relative chance of a civilian with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_unarmed_civilian: bpy.props.IntProperty(
        name="Unarmed",
        description=(
            "Relative chance of a civilian with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    mix_molotov_civilian: bpy.props.IntProperty(
        name="Molotov",
        description=(
            "Relative chance of a civilian with this weapon. The engine rolls "
            "against the sum of all fifteen weights, so only the proportions "
            "matter -- 1 and 2 behave like 10 and 20"),
        default=0, min=0, max=10000)
    death_damage: bpy.props.IntProperty(
        name="Damage",
        description=(
            "Damage per second. 0 kills outright, which is what 53 of the 57 "
            "volumes in the game do; anything else is a rate the player can "
            "sometimes survive"),
        default=0, min=0, max=100000)
    death_starts_on: bpy.props.BoolProperty(
        name="On at start",
        description=(
            "Active when the level loads. Off means the volume does nothing "
            "until a switch turns it on -- 46 of the 57 retail volumes start on"),
        default=True)
    death_collision_required: bpy.props.BoolProperty(
        name="Needs contact",
        description=(
            "Only harm things that are touching something. Leave this OFF for "
            "a pit: the engine skips anything that did not collide this frame, "
            "and a falling player touches nothing, so an on volume never sees "
            "them. Retail clears it on 10 of its 57 volumes, which are the "
            "ones under a drop"),
        default=False)
    object_mass: bpy.props.IntProperty(
        name="Mass",
        description=(
            "SimpleStrategy.mass in grams. Retail uses 50 for pickups and "
            "40-2000 for furniture; 0 for scenery that cannot be moved"),
        default=50,
        min=0,
        max=1000000,
    )
    object_integrity: bpy.props.IntProperty(
        name="Integrity",
        description=(
            "SimpleStrategy.integrity, roughly 0-21. The top bit is a spares "
            "marker and is added automatically, so the value shown here is the "
            "real one"),
        default=2,
        min=0,
        max=1000,
    )
    object_heavy: bpy.props.BoolProperty(
        name="Too heavy to move",
        description=(
            "Sets flag 0x10. Retail uses it on furniture of 1000 g and above"),
        default=False,
    )
    pickup_type: bpy.props.IntProperty(
        name="Category",
        description=(
            "INANIMATEOBJECT_TYPE written to AVPSTRAT. Set by the menu entry "
            "that created this object; 2 weapon, 3 ammo, 4 health, 5 armour, "
            "15 field charge"),
        default=0,
        min=0,
    )
    sub_type: bpy.props.IntProperty(
        name="Weapon / Variant",
        description=(
            "AVPGENER.sub_type. Marine: 0 pulse, 5 pistol, 10 smartgun, "
            "20 grenade, 30-32 shotgun, 40 pistol, 50 flamer, 60 SADAR, "
            "70 minigun, 80 molotov, 100-120 civilian. Predator: low 3 bits "
            "primary, bits 3-5 secondary"
        ),
        default=0,
        min=0,
        max=255,
    )
    pickup_variant: bpy.props.EnumProperty(
        name="Variant",
        description=(
            "Which one of this pickup's kind. The list follows the category, "
            "so a weapon number never appears for a piece of ammo"),
        items=_pickup_variant_items,
    )

    texture_id: bpy.props.IntProperty(
        name="Model Variant",
        description="AVPGENER.textureID: which model/skin number to use",
        default=0,
        min=0,
        max=255,
    )
    race_marine: bpy.props.BoolProperty(
        name="Marine",
        description=(
            "Marine may use this spawn point. Only meaningful for multiplayer "
            "spawns: the engine ignores race bits on NPC generators"
        ),
        default=True,
    )
    race_alien: bpy.props.BoolProperty(
        name="Alien",
        description=(
            "Alien may use this spawn point. Single player only in retail - "
            "skirmish and coop set maxAlien to 0, so leave this off for "
            "skirmish maps"
        ),
        default=True,
    )
    race_predator: bpy.props.BoolProperty(
        name="Predator",
        description="Predator may use this spawn point",
        default=True,
    )
    inactive: bpy.props.BoolProperty(
        name="Inactive",
        description=(
            "AVPGENFLAG_GENERATORINACTIVE. Keeps the generator in the level "
            "but stops it spawning anything"
        ),
        default=False,
    )
    excluded_hard: bpy.props.BoolProperty(
        name="Not on hard difficulty",
        description=(
            "AVPGENFLAG_NOTDIFFICULTY*. Retail uses the three difficulty bits "
            "to thin out enemies on easier settings; this is a starter toggle"
        ),
        default=False,
    )
    link_target: bpy.props.PointerProperty(
        name="Target Waypoint",
        description="The waypoint this link points at",
        type=bpy.types.Object,
    )
    link_oneway: bpy.props.BoolProperty(
        name="One way",
        description=(
            "linkflag_oneway. Waypoint links form a directed graph; with this "
            "on the link is only usable in one direction"
        ),
        default=False,
    )
    link_reversed: bpy.props.BoolProperty(
        name="Reversed",
        description=(
            "linkflag_reversed_oneway. One way, but in the opposite direction "
            "to the object's own facing"
        ),
        default=False,
    )
    patrol_closed: bpy.props.BoolProperty(
        name="Closed loop",
        description=(
            "PathFlag_LoopPath. When off the patrol path is walked back and "
            "forth rather than looped"
        ),
        default=False,
    )
    mix_drone: bpy.props.IntProperty(
        name="Drone",
        description=(
            "How much of this generator's output should be Alien Drones. "
            "The three values are relative to each other, so 2/1/1 means "
            "half drones and a quarter each of the others"
        ),
        default=1,
        min=0,
        max=100,
    )
    mix_predalien: bpy.props.IntProperty(
        name="Predalien",
        description="Share of this generator's output that should be Predaliens",
        default=0,
        min=0,
        max=100,
    )
    mix_praetorian: bpy.props.IntProperty(
        name="Praetorian",
        description="Share of this generator's output that should be Praetorians",
        default=0,
        min=0,
        max=100,
    )
    enemy_kind: EnumProperty(
        name="Default Enemy",
        description=(
            "Which creature a generator with no weights falls back to. Only "
            "matters for a generator whose proportions are all zero"
        ),
        items=(
            ("aliens", "Aliens",
             "Generators without weights spawn alien drones"),
            ("marines", "Marines",
             "Generators without weights spawn marines with pulse rifles"),
        ),
        default="aliens",
    )
    max_npcs: IntProperty(
        name="Max NPCs",
        description=(
            "Hard cap on how many generated NPCs may be alive at once. The "
            "engine's own default is 25, and retail levels range from 0 to 25"
        ),
        default=25,
        min=0,
        max=255,
    )
    npcs_per_minute: IntProperty(
        name="NPCs per Minute",
        description=(
            "How fast NPCs are generated across the level. Retail uses "
            "anything from 4 to 60; the engine's own default is 4"
        ),
        default=4,
        min=0,
        max=255,
    )
    npc_acceleration: IntProperty(
        name="Acceleration",
        description=(
            "How quickly the generation rate climbs towards the cap over time"
        ),
        default=2,
        min=0,
        max=255,
    )
    hive_state_change_time: IntProperty(
        name="Hive State Change (s)",
        description=(
            "How long the hive spends attacking before it pulls back to "
            "regroup, and vice versa, IN SECONDS. The state flips on this "
            "timer and drives every alien at once: attacking they hunt the "
            "player, regrouping they retreat. Each period is randomised by up "
            "to an eighth, so the swarm does not move like clockwork.\n"
            "32000 (about 8.9 hours, the commonest retail value) effectively "
            "means 'always attacking'. The engine's own default is 60"
        ),
        default=32000,
        min=0,
    )


class _PlaceableSpec:
    """One menu entry: identity, placement, and the properties it presets."""

    def __init__(self, key, label, description, icon, category, kind,
                 npc_type=0, display=None, flags=None, legacy_marker=None,
                 pickup_type=None, object_flags=None, pieces=0,
                 shape_file=None):
        self.key = key
        self.label = label
        self.description = description
        self.icon = icon
        self.category = category
        self.kind = kind
        self.npc_type = npc_type
        self.pickup_type = pickup_type
        self.display = display or DISPLAY_BY_KIND.get(kind, "PLAIN_AXES")
        self.flags = dict(flags or {})
        # Switches on the Object rather than on rif_placeable: the object
        # type, the behaviour, the track. Set on the created object.
        self.object_flags = dict(object_flags or {})
        # Mesh children to give a fragmenting preset something to break
        # into. Zero means the object stands alone.
        self.pieces = int(pieces)
        # Model to read from shape_rifs when this entry is used. None
        # means the entry has no model of its own and stays a cube.
        self.shape_file = shape_file
        # Set on objects the existing export path finds by marker rather than
        # by the new `rif_placeable` property.
        self.legacy_marker = legacy_marker

    @property
    def idname(self):
        """Return the Blender operator id this entry is generated as."""
        return f"object.rif_place_{self.key}"


# The whole menu, as data: one row per entry, each with its own tooltip.
# Retail pickup name -> shape_rifs file, keyed by (INANIMATEOBJECT_TYPE,
# sub_type). Read from every pickup placed in the 63 shipped levels; the file is
# the object name with its trailing digits trimmed, ignoring case.
PICKUP_SHAPES = {
    (2, 0): "Pulse",
    (2, 2): "Smart",
    (2, 3): "flame",
    (2, 5): "SADAR",
    (2, 6): "Grenade",
    (2, 7): "Minigun",
    (2, 22): "Pistol",
    (2, 23): "Skeeter",
    (3, 0): "Pulam",
    (3, 2): "Smrtam01",
    (3, 3): "FLAM01",
    (3, 5): "Rocam01",
    (3, 6): "Grenbx01",
    (3, 7): "Miniamo",
    (3, 8): "Grenband",
    (4, 0): "Medbox01",
    (5, 0): "Armour01",
    (13, 0): "secpass",
    (15, 0): "Pboost",
}


# The variant list a pickup offers, keyed by its INANIMATEOBJECT_TYPE. Every
# entry is (sub_type, label); the number is what reaches the file, the label is
# what an author reads. Weapons and ammunition are `WEAPON_ID` and `AMMO_ID`
# from EQUIPMNT.H, which is why the numbers are not consecutive.
PICKUP_VARIANTS = {
    2: (  # IOT_Weapon
        (0, "Pulse Rifle"),
        (2, "Smartgun"),
        (3, "Flamethrower"),
        (5, "SADAR"),
        (6, "Grenade Launcher"),
        (7, "Minigun"),
        (22, "Marine Pistol"),
        (23, "Frisbee Launcher"),
    ),
    3: (  # IOT_Ammo
        (0, "10mm"),
        (2, "Smartgun"),
        (3, "Flamethrower"),
        (5, "SADAR"),
        (6, "Grenade"),
        (7, "Minigun"),
        (8, "Pulse Grenade"),
    ),
    4: ((0, "Medkit"),),
    5: ((0, "Armour"),),
    6: ((0, "Key"),),
    7: ((0, "Boxed Sentry Gun"),),
    8: ((0, "IR Goggles"),),
    10: ((0, "Data Tape"),),
    11: ((0, "Motion Tracker Upgrade"),),
    12: ((0, "Pheromone Pod"),),
    13: ((0, "Security Pass"),),
    14: ((0, "Destroy Base"),),
    15: ((0, "Field Charge"),),
}


PLACEABLES = (
    _PlaceableSpec(
        "module_room", "Room", (
            "A room as a module: a box with its faces pointing inward, so you "
            "stand inside it. Two materials, floor and the one shared by the "
            "walls and ceiling. Scale it to the size of the space"),
        "MESH_CUBE", CAT_MODULE, KIND_MODULE_ROOM,
        object_flags={"rif_object_type": "MODULE"}),
    _PlaceableSpec(
        "module_door", "Door", (
            "A door module with its door flags on. It morphs open rather than "
            "sliding, so it deforms in place: give it the thickness of the "
            "wall it sits in and shape it as the doorway"),
        "MESH_CUBE", CAT_MODULE, KIND_MODULE_DOOR,
        object_flags={"rif_object_type": "MODULE",
                      "rif_module_door": True}),
    # -- Player Start ------------------------------------------------------
    _PlaceableSpec(
        "player_start_sp", "Single Player", (
            "AVPSTART: where the campaign or single-player level begins. "
            "Carries a full orientation matrix, unlike the multiplayer spawn. "
            "Not required on a skirmish map."),
        "EMPTY_ARROWS", CAT_PLAYER_START, KIND_PLAYER_START_SP,
        legacy_marker=PLAYER_START_MARKER),
    _PlaceableSpec(
        "player_start_mp", "Multiplayer Spawn", (
            "AVPGENER with AVPGENFLAG_MULTIPLAYERSTART: one start position per "
            "player slot. Retail maps use 6 of these. Tick the races allowed to "
            "use it; 'Marine + Predator' is the most common combination."),
        "EMPTY_SINGLE_ARROW", CAT_PLAYER_START, KIND_PLAYER_START_MP,
        flags={"race_alien": False}),

    # -- Alien generators, the only ones a skirmish map can use -----------
    _PlaceableSpec(
        "gen_drone", "Alien Drone", (
            "AVPGENER type 1. Spawns wandering drones. Works in every mode: "
            "single player, skirmish and coop."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=1),
    _PlaceableSpec(
        "gen_predalien", "Predalien", (
            "AVPGENER type 5. A heavier alien that can also crawl on walls and "
            "ceilings. Works in single player, skirmish and coop."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=5),
    _PlaceableSpec(
        "gen_praetorian", "Praetorian", (
            "AVPGENER type 8. Alien guard, tough and slow. Works in single "
            "player, skirmish and coop."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=8),
    _PlaceableSpec(
        "gen_predator", "Predator  (single player)", (
            "AVPGENER type 2. Enemy Predator; sub_type selects primary and "
            "secondary weapon. Never spawns in a network game, so it is "
            "useless on a skirmish map."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=2),
    _PlaceableSpec(
        "gen_facehugger", "Facehugger  (single player)", (
            "AVPGENER type 3. Hunts the player only. Single player only."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=3),
    _PlaceableSpec(
        "gen_xenoborg", "Xenoborg  (single player)", (
            "AVPGENER type 4. Hostile to everything, including other aliens. "
            "Single player only."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=4),
    _PlaceableSpec(
        "gen_queen", "Queen  (single player)", (
            "AVPGENER type 6. Alien boss. Single player only."),
        "MESH_CONE", CAT_GENERATOR, KIND_NPC, npc_type=6),
    _PlaceableSpec(
        "gen_marine", "Marine  (single player)", (
            "AVPGENER type 7. Weapon chosen by sub_type: 0 pulse, 5 pistol, "
            "10 smartgun, 20 grenade, 30-32 shotgun, 50 flamer, 60 SADAR, "
            "70 minigun. 100-120 are civilians. Single player only."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_NPC, npc_type=7),
    _PlaceableSpec(
        "gen_sentry", "Sentry Turret  (single player)", (
            "AVPGENER type 9. Shoots anything not human. Single player only, "
            "so it cannot be used to test a skirmish or coop level."),
        "MESH_CONE", CAT_GENERATOR, KIND_NPC, npc_type=9),

    # -- Generator roles that are not a fixed NPC -------------------------
    _PlaceableSpec(
        "gen_default", "Default Enemy Generator", (
            "Spawns whatever the level names as its default enemy - aliens or "
            "humans, set in Global Generator Data. The simplest way to "
            "populate a level: place it, and enemies come to you."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_DEFAULT_ENEMY,
        # Every proportion zero, so the generator carries no weights and the
        # engine falls back to GLOGENDC's creature. Leaving the shared default
        # of 1 here would make it roll for drones instead, which ignores the
        # level setting the entry exists to honour.
        flags={"mix_drone": 0, "mix_predalien": 0, "mix_praetorian": 0}),
    _PlaceableSpec(
        "gen_weighted", "Mixed Enemy Generator", (
            "Spawns several kinds of enemy in proportions you choose, rather "
            "than just one. This is what skirmish and coop maps use for their "
            "bots. Only Alien Drones, Predaliens and Praetorians ever appear "
            "in a network game, so the other kinds are not offered here."),
        "MESH_UVSPHERE", CAT_GENERATOR, KIND_WEIGHTED),

    # -- Navigation --------------------------------------------------------
    _PlaceableSpec(
        "waypoint", "Alien Waypoint", (
            "One node of the aliens' navigation graph, stored in WAYPOINT "
            "under the owning object's MODULEDT. It is a BOX, not a point: the "
            "exporter takes its centre and half-size from this object's "
            "position and scale. Tick 'Alien can crawl here' for wall and "
            "ceiling nodes."),
        "MESH_CUBE", CAT_NAVIGATION, KIND_WAYPOINT),
    _PlaceableSpec(
        "patrol_path", "Patrol Path  (single player)", (
            "AVPPATH2: an ordered list of points a human NPC walks along, with "
            "an optional closed loop. Points may cross module boundaries. Rare "
            "in retail - only 69 exist across 17 levels."),
        "CURVE_PATH", CAT_NAVIGATION, KIND_PATROL_PATH),

    # -- Gameplay ----------------------------------------------------------
    _PlaceableSpec(
        "death_volume", "Kill Volume  (single player)", (
            "Everything inside this box dies. The volume is the object's "
            "bounds, so there is no geometry to model and nothing else to set "
            "but the damage. It only harms creatures and the player -- "
            "scenery inside it is left alone"),
        "MESH_CUBE", CAT_GAMEPLAY, KIND_DEATH_VOLUME),

    _PlaceableSpec(
        "mission_objective", "Mission Objective  (single player)", (
            "Names the level's goal. This does NOT complete the level by "
            "itself: the engine builds a separate completion block for it "
            "(Objsetup.cpp:3329), and a SWITCH has to send that block a "
            "request to finish. One per level -- retail has exactly one in "
            "every mission it ships, and the engine sorts them by order "
            "number. The box only says where the object sits. Switches are "
            "not written yet, so a goal placed now waits for one"),
        "EMPTY_SINGLE_ARROW", CAT_GAMEPLAY, KIND_MISSION_OBJECTIVE),

    _PlaceableSpec(
        "area_switch", "Area Switch  (trigger volume)", (
            "Everything that walks into this box sends a message to the "
            "objects named below. The commonest trigger in the game -- 690 of "
            "them across 40 levels -- and the means of ending a level, opening "
            "a door, or arming a kill volume. Walk into it yourself to test"),
        "MESH_CUBE", CAT_GAMEPLAY, KIND_AREA_SWITCH),

    _PlaceableSpec(
        "multi_switch", "Multi Switch  (logic)", (
            "A switch with no box: other switches drive it, and it drives the "
            "objects named below. This is how a level says 'only do this when "
            "every condition is met', and it is the second commonest trigger "
            "in the game -- 1040 of them across 45 levels. Its usual mode is "
            "Time Delay, because it is a step in a chain rather than "
            "somewhere to walk"),
        "EMPTY_SINGLE_ARROW", CAT_GAMEPLAY, KIND_MULTI_SWITCH),

    _PlaceableSpec(
        "self_destruct", "Self Destruct  (countdown)", (
            "Arms a countdown that destroys the level -- the escape sequence. A "
            "switch starts it, the same way a switch arms a kill volume, so "
            "point one at this. The timer is how long the player has. A level "
            "may carry more than one"),
        "EMPTY_SINGLE_ARROW", CAT_GAMEPLAY, KIND_SELF_DESTRUCT),

    _PlaceableSpec(
        "message", "Message  (text or cutscene)", (
            "Shows a line of the game's own text, and can start a cutscene -- "
            "the storyline beats, and the mission briefing that appears as the "
            "level loads. A switch sends it code 2 to arm it AND show it; "
            "plain 'on' only arms it. 314 of them across 33 levels"),
        "EMPTY_SINGLE_ARROW", CAT_GAMEPLAY, KIND_MESSAGE),

    _PlaceableSpec(
        "particle_generator", "Particle Generator  (spark, steam, smoke, "
        "flame)", (
            "An emitter. It runs only while its module is VISIBLE, which is the "
            "opposite of an enemy generator -- one the player can never see does "
            "nothing, silently. 570 of them across 44 levels. Spark and flame "
            "also light the room; steam and black smoke only emit"),
        "EMPTY_SINGLE_ARROW", CAT_EFFECTS, KIND_PARTICLE_GENERATOR),

    _PlaceableSpec(
        "global_generator", "Global Generator Data", (
            "GLOGENDC: one per level. Sets the default enemy kind, the NPC "
            "limit, spawns per minute, and the hive state change time. Placing "
            "a second one is a mistake - the game reads a single chunk."),
        "EMPTY_AXIS", CAT_GAMEPLAY, KIND_GLOBAL_GENERATOR),
    _PlaceableSpec(
        "pickup_weapon", "Weapon  (choose none)", (
            "Placeholder entry kept for the category; use one of the specific "
            "weapons below, which preset the sub_type"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 0}),
    _PlaceableSpec(
        "pickup_weapon_pulserifle", "Weapon: Pulse Rifle", (
            "IOT_Weapon (2), sub_type 0. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 0}, shape_file="Pulse"),
    _PlaceableSpec(
        "pickup_weapon_smartgun", "Weapon: Smartgun", (
            "IOT_Weapon (2), sub_type 2. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 2}, shape_file="Smart"),
    _PlaceableSpec(
        "pickup_weapon_flamethrower", "Weapon: Flamethrower", (
            "IOT_Weapon (2), sub_type 3. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 3}, shape_file="flame"),
    _PlaceableSpec(
        "pickup_weapon_sadar", "Weapon: SADAR", (
            "IOT_Weapon (2), sub_type 5. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 5}, shape_file="SADAR"),
    _PlaceableSpec(
        "pickup_weapon_grenadelauncher", "Weapon: Grenade Launcher", (
            "IOT_Weapon (2), sub_type 6. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 6}, shape_file="Grenade"),
    _PlaceableSpec(
        "pickup_weapon_minigun", "Weapon: Minigun", (
            "IOT_Weapon (2), sub_type 7. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 7}, shape_file="Minigun"),
    _PlaceableSpec(
        "pickup_weapon_marinepistol", "Weapon: Marine Pistol", (
            "IOT_Weapon (2), sub_type 22. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 22}, shape_file="Pistol"),
    _PlaceableSpec(
        "pickup_weapon_frisbee", "Weapon: Frisbee Launcher", (
            "IOT_Weapon (2), sub_type 23. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 23}, shape_file="Skeeter"),
    _PlaceableSpec(
        "pickup_ammo_10mm", "Ammo: 10mm Ammo", (
            "IOT_Ammo (3), sub_type 0. 10mm rounds, the commonest ammo retail places"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 0}, shape_file="Pulam"),
    _PlaceableSpec(
        "pickup_ammo_smartgun", "Ammo: Smartgun Ammo", (
            "IOT_Ammo (3), sub_type 2. Smartgun rounds for the smartgun"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 2}, shape_file="Smrtam01"),
    _PlaceableSpec(
        "pickup_ammo_flamethrower", "Ammo: Flamethrower Fuel", (
            "IOT_Ammo (3), sub_type 3. Fuel canisters for the flamethrower"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 3}, shape_file="FLAM01"),
    _PlaceableSpec(
        "pickup_ammo_sadar", "Ammo: SADAR Rockets", (
            "IOT_Ammo (3), sub_type 5. Rockets for the SADAR launcher"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 5}, shape_file="Rocam01"),
    _PlaceableSpec(
        "pickup_ammo_grenade", "Ammo: Grenades", (
            "IOT_Ammo (3), sub_type 6. Grenades for the grenade launcher"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 6}, shape_file="Grenbx01"),
    _PlaceableSpec(
        "pickup_ammo_minigun", "Ammo: Minigun Ammo", (
            "IOT_Ammo (3), sub_type 7. Minigun rounds for the minigun"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 7}, shape_file="Miniamo"),
    _PlaceableSpec(
        "pickup_ammo_pulsegrenade", "Ammo: Pulse Grenades", (
            "IOT_Ammo (3), sub_type 8. Pulse grenades for the pulse rifle"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 8}, shape_file="Grenband"),
    _PlaceableSpec(
        "pickup_health", "Medkit", (
            "IOT_Health (4). sub_type is always 0 in retail - the category "
            "holds a single item"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=4, shape_file="Medbox01"),
    _PlaceableSpec(
        "pickup_armour", "Armour", (
            "IOT_Armour (5). sub_type is always 0 in retail"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=5, shape_file="Armour01"),
    _PlaceableSpec(
        "pickup_fieldcharge", "Energy Orb", (
            "IOT_FieldCharge (15) - the Predator's energy pickup. sub_type is "
            "always 0 in retail"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=15, shape_file="Pboost"),
    _PlaceableSpec(
        "object_simple", "Simple Object", (
            "A placed object with every option off: plain decoration, which "
            "is the commonest placed object in the shipped levels. Put the "
            "mesh in the shape you want and switch on what it should do"),
        "MESH_CUBE", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT"}),
    _PlaceableSpec(
        "object_lift", "Platform Lift", (
            "A platform lift with a track. Add an End Point and set the "
            "Movement section to it, and the platform travels between the "
            "two"),
        "SORT_ASC", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT",
                      "rif_module_plat_lift": True,
                      "rif_track_enabled": True}),
    _PlaceableSpec(
        "object_fan", "Fan", (
            "A fan with a track. It pushes the player away from itself, and "
            "with an End Point it moves along the way as well"),
        "FORCE_FORCE", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT",
                      "rif_module_fan": True,
                      "rif_track_enabled": True}),
    _PlaceableSpec(
        "object_lever", "Lever", (
            "A lever with a track: aim at it and press the operate key. Give "
            "it an End Point so the handle moves, and set its Target in the "
            "Switch section to what it should work"),
        "HAND", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT",
                      "rif_placed_switch": True,
                      "rif_track_enabled": True}),
    _PlaceableSpec(
        "object_lamp", "Lamp", (
            "A lamp that emits light, with the pieces its housing breaks into "
            "already set up. Turn on Light and mark a material as a corona to "
            "give it a flare"),
        "LIGHT_POINT", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT",
                      "rif_placed_light": True,
                      "rif_fragments": True,
                      "rif_destructible": True,
                      "rif_integrity": 16},
        pieces=2),
    _PlaceableSpec(
        "object_destructible", "Destructible", (
            "An object that breaks into pieces, with the pieces already there "
            "as children. Set Integrity and an Explosion in the Trigger "
            "section, and a Target if breaking it should do something"),
        "MOD_EXPLODE", CAT_OBJECT, KIND_SIMPLE_OBJECT,
        object_flags={"rif_object_type": "SIMPLE_OBJECT",
                      "rif_fragments": True,
                      "rif_destructible": True,
                      "rif_integrity": 3},
        pieces=3),
    _PlaceableSpec(
        "sound", "Sound", (
            "SOUNDOB2: one emitter. inner_range is at full volume, outer_range "
            "is silence, and the sphere's size shows outer_range. Pick the file "
            "in the panel - the folder it comes from is the level's Sound "
            "Directory, set once for the whole level"),
        "MESH_UVSPHERE", CAT_SOUND, KIND_SOUND),
    # The Effects category's particle entry used to be a placeholder here, so the
    # menu offered two of them: a real emitter under Gameplay and one that
    # exported nothing under Effects. The real one has moved into this category
    # and the placeholder is gone.
    _PlaceableSpec(
        "track_end", "End Point", (
            "Marks where an object's track stops. Point rif_track_end at it and "
            "the run is the distance between them; leave it unused and the "
            "track stands still, which is what a fan wants because its "
            "direction comes from the rotation. An empty, so it adds no "
            "geometry to the level"),
        "MESH_UVSPHERE", CAT_GAMEPLAY, KIND_TRACK_END),
    _PlaceableSpec(
        "pickup_security", "Security Pass", (
            "IOT_SpecialPickupObject (13). Models bypass and secpass01. Only "
            "two in retail, but a working pickup"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=13, shape_file="secpass"),
)


def _make_waypoint_material():
    """Return a fully transparent material, reused across waypoints.

    A wireframe `display_type` is a viewport-only setting: Render and Material
    Preview ignore it and shade the mesh with its material, which would put a
    solid box over the level being built. An alpha of zero keeps the waypoint
    visible as edges in the viewport and invisible everywhere else.

    The node tree is touched defensively because the alpha socket and the blend
    mode are spelled differently across Blender versions, and a waypoint that
    fails to be made transparent must not stop the operator.
    """
    name = "RIF Waypoint (transparent)"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    material = bpy.data.materials.new(name)
    # The viewport colour carries the alpha too, so Solid shading is covered.
    material.diffuse_color = (1.0, 1.0, 1.0, 0.0)
    try:
        material.use_nodes = True
        tree = getattr(material, "node_tree", None)
        for node in getattr(tree, "nodes", []) or []:
            if getattr(node, "type", "") != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs.get("Alpha")
            if socket is not None:
                socket.default_value = 0.0
        # Blender 4.2 moved blending into the render method; older versions use
        # `blend_method`. Set whichever exists and ignore the rest.
        for attribute, value in (("blend_method", "BLEND"),
                                 ("shadow_method", "NONE"),
                                 ("surface_render_method", "BLENDED")):
            if hasattr(material, attribute):
                try:
                    setattr(material, attribute, value)
                except (TypeError, AttributeError):
                    pass
    except Exception:  # noqa: BLE001 - never block creating the waypoint
        pass
    return material


class RIF_PT_module(bpy.types.Panel):
    """Per-module flags, for any mesh in the level."""

    bl_label = "AvP Object"
    bl_idname = "RIF_PT_module"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        """Any mesh can be a module; nothing else can."""
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and PLACEABLE_PROP not in obj

    def draw(self, context):
        """Draw the appearance flags and the AI ones, kept apart."""
        obj = context.active_object

        layout = self.layout

        # The type first: it decides which path the engine takes, and therefore
        # which of the sections below can mean anything at all.
        box = layout.box()
        box.label(text="AvP object", icon="OUTLINER_OB_EMPTY")
        box.prop(obj, "rif_object_type")

        object_type = getattr(obj, "rif_object_type", "MODULE")

        if object_type == "MODULE":
            box = layout.box()
            box.label(text="Appearance", icon="WORLD")
            box.prop(obj, "rif_module_sky")
            box.prop(obj, "rif_module_fog")

            box = layout.box()
            box.label(text="AI navigation", icon="OUTLINER_OB_ARMATURE")
            box.prop(obj, "rif_module_airduct")
            box.prop(obj, "rif_module_stairs")
            box.label(text="These change how bots move, not how it looks",
                      icon="INFO")

            box = layout.box()
            box.label(text="Links", icon="LINKED")
            for index, link in enumerate(obj.rif_module_links):
                row = box.row(align=True)
                row.prop(link, "target_name", text=f"Module {index + 1}")
            row = box.row(align=True)
            row.operator("rif.module_link_add", icon="ADD")
            row.operator("rif.module_link_remove", icon="REMOVE")
            if not len(obj.rif_module_links):
                box.label(text="Used when Link Mode is By List", icon="INFO")


            box = layout.box()
            box.label(text="Door", icon="MODIFIER")
            box.prop(obj, "rif_module_door")
            if getattr(obj, "rif_module_door", False):
                inner = box.box()
                inner.prop(obj, "rif_module_door_locked")
                inner.prop(obj, "rif_module_door_proximity")
                inner.prop(obj, "rif_module_door_open")
                inner.prop(obj, "rif_module_door_lift")
                inner.prop(obj, "rif_module_door_horizontal")
                inner.prop(obj, "rif_module_door_open_seconds")
                inner.prop(obj, "rif_module_door_close_seconds")

        elif object_type == "SIMPLE_OBJECT":

            box = layout.box()
            box.label(text="Switch", icon="HAND")
            box.prop(obj, "rif_placed_switch")
            if getattr(obj, "rif_placed_switch", False):
                target = obj.rif_switch_target
                pressed, released = switch_action_names(target)

                inner = box.box()
                inner.prop(obj, "rif_switch_target")
                if target is None:
                    inner.label(text="No target: nothing to send to",
                                icon="ERROR")
                else:
                    inner.label(text=f"Target: {target.name}",
                                icon="OBJECT_DATA")

                # The two actions are always opposites; only the pressed
                # one is chosen, so the panel names the state instead of
                # showing a bit.
                acts = box.box()
                acts.label(text="When operated", icon="PLAY")
                state_one = obj.rif_switch_press_state
                first, second = (pressed, released) if state_one \
                    else (released, pressed)
                acts.label(text=f"Press sends:   {first}", icon="FORWARD")
                acts.label(text=f"Release sends: {second}", icon="BACK")
                acts.prop(obj, "rif_switch_press_state",
                          text=f"Press sends state 1 ({pressed})")
                acts.prop(obj, "rif_switch_extra")

                timing = box.box()
                timing.label(text="Behaviour", icon="TIME")
                timing.prop(obj, "rif_switch_mode")
                if obj.rif_switch_mode != "1":
                    timing.prop(obj, "rif_switch_time")
                timing.prop(obj, "rif_switch_security")

                flags = box.box()
                flags.label(text="Flags", icon="OPTIONS")
                flags.prop(obj, "rif_switch_starts_on")
                flags.prop(obj, "rif_switch_off_same")
                flags.prop(obj, "rif_switch_off_none")
            box = layout.box()
            box.label(text="Fragments", icon="MOD_EXPLODE")
            box.prop(obj, "rif_fragments")
            if getattr(obj, "rif_fragments", False):
                inner = box.box()
                pieces = [child for child in obj.children
                          if child.type == "MESH"]
                inner.label(text=f"{len(pieces)} pieces", icon="MESH_DATA")
                if not pieces:
                    inner.label(text="No mesh children to break into",
                                icon="ERROR")
                for child in pieces[:8]:
                    inner.prop(child, "rif_fragment_copies", text=child.name)
                inner.label(text="A piece placed to one side flies that way",
                            icon="INFO")



            box = layout.box()
            box.label(text="Pickup", icon="PACKAGE")
            box.prop(obj, "rif_pickup")
            if getattr(obj, "rif_pickup", False):
                inner = box.box()
                inner.prop(obj, "rif_pickup_kind")
                inner.prop(obj, "rif_pickup_subtype")
                if obj.rif_pickup_kind == "3":
                    # The amount is not in the file: the engine takes it from
                    # TemplateAmmo[ammo].AmmoPerMagazine (Inventry.c:1200), so
                    # one pickup is always one magazine's worth.
                    inner.label(text="Gives one magazine, as fixed by the ammo",
                                icon="INFO")
                elif obj.rif_pickup_kind == "6":
                    inner.label(text="Sets the clearance for switches "
                                "at or below this level", icon="INFO")
            box = layout.box()
            box.label(text="Trigger", icon="FORWARD")
            box.prop(obj, "rif_destructible")
            if getattr(obj, "rif_destructible", False):
                # Destruction is the event these three belong to, so they live
                # with the switch that makes the object destructible at all.
                box.prop(obj, "rif_integrity")
                box.prop(obj, "rif_trigger_explosion")
                box.prop(obj, "rif_trigger_on_destroy")
            box.prop(obj, "rif_trigger_on_pickup")
            if (getattr(obj, "rif_trigger_on_destroy", False)
                    or getattr(obj, "rif_trigger_on_pickup", False)):
                inner = box.box()
                inner.prop(obj, "rif_trigger_target")
                target = obj.rif_trigger_target
                if target is None:
                    inner.label(text="No target: nothing to send to",
                                icon="ERROR")
                else:
                    pressed, released = switch_action_names(target)
                    first, second = (pressed, released) \
                        if obj.rif_trigger_state else (released, pressed)
                    inner.label(text=f"Sends: {first}", icon="FORWARD")
                    inner.prop(obj, "rif_trigger_state",
                               text=f"Send state 1 ({pressed})")
            box = layout.box()
            box.label(text="Platform lift", icon="SORT_ASC")
            box.prop(obj, "rif_module_plat_lift")
            if getattr(obj, "rif_module_plat_lift", False):
                inner = box.box()
                inner.prop(obj, "rif_plat_lift_disabled")
                inner.prop(obj, "rif_plat_lift_one_use")


            box = layout.box()
            box.label(text="Light", icon="LIGHT_POINT")
            box.prop(obj, "rif_placed_light")
            if getattr(obj, "rif_placed_light", False):
                inner = box.box()
                inner.prop(obj, "rif_light_brightness")
                inner.prop(obj, "rif_light_range")
                inner.prop(obj, "rif_light_colour")
                inner.prop(obj, "rif_light_off_colour")
                inner.prop(obj, "rif_light_on_at_start")

                shape = box.box()
                shape.label(text="Behaviour", icon="PLAY")
                shape.prop(obj, "rif_light_type")
                shape.prop(obj, "rif_light_on_off_type")

                timing = box.box()
                timing.label(text="Timing", icon="TIME")
                if obj.rif_light_on_off_type == "1":
                    timing.prop(obj, "rif_light_fade_up_ms")
                    timing.prop(obj, "rif_light_fade_down_ms")
                if obj.rif_light_type in ("1", "2"):
                    timing.prop(obj, "rif_light_on_ms")
                    timing.prop(obj, "rif_light_off_ms")
                timing.prop(obj, "rif_light_start_ms")

                coronas = [material for material in obj.data.materials
                           if material is not None
                           and getattr(material,
                                       "rif_material_light_corona", False)]
                if coronas:
                    box.label(text=f"{len(coronas)} corona material(s)",
                              icon="LIGHT_SUN")
            box = layout.box()
            box.label(text="Fan", icon="FORCE_FORCE")
            box.prop(obj, "rif_module_fan")
            if getattr(obj, "rif_module_fan", False):
                inner = box.box()
                inner.prop(obj, "rif_fan_strength")
                inner.prop(obj, "rif_fan_speed_seconds")
                inner.prop(obj, "rif_fan_slow_seconds")

            # A track is not a kind of object, it is a way of carrying one, so
            # every Simple Object may have one. Only when nothing else claims
            # the object does it become a StratTrack; a fan or a lever read
            # the same track from their own tools data.
            _draw_movement(layout, obj)



# `EQUIPMNT.H:57` and `:116`, both numbered from zero with no gaps.
PICKUP_WEAPONS = (
    "Pulse rifle",
    "Auto shotgun",
    "Smartgun",
    "Flamethrower",
    "Plasma gun",
    "SADAR",
    "Grenade launcher",
    "Minigun",
    "Sonic cannon",
    "Beam cannon",
    "Mystery gun",
    "Predator wristblade",
    "Predator pistol",
    "Predator rifle",
    "Predator shoulder cannon",
    "Predator disc",
    "Predator medicomp",
    "Predator staff",
    "Alien claw",
    "Alien grab",
    "Alien spit",
    "Cudgel",
    "Marine pistol",
    "Frisbee launcher",
    "Two pistols",
)

PICKUP_AMMO = (
    "10mm",
    "Shotgun",
    "Smartgun",
    "Flamethrower",
    "Plasma",
    "SADAR",
    "Grenade",
    "Minigun",
    "Pulse grenade",
    "Flare",
    "Fragmentation grenade",
    "Proximity grenade",
    "Particle beam",
    "Sonic pulse",
    "Predator wristblade",
    "Predator pistol",
    "Predator rifle",
    "Predator energy bolt",
    "Predator disc",
    "Alien claw",
    "Alien tail",
    "Alien spit",
    "Autogun",
    "Xenoborg",
    "Facehugger",
)

PICKUP_CLEARANCES = (
    "None",
    "Level 1",
    "Level 2",
    "Level 3",
    "Level 4",
    "Level 5",
)


def pickup_subtype_items(self, context):
    """Return the subtype list for the kind this object is.

    Blender calls this on every draw, so the list follows the Kind without
    anything having to be reset by hand.
    """
    kind = str(getattr(self, "rif_pickup_kind", "2"))

    if kind == "2":
        names = PICKUP_WEAPONS
    elif kind == "3":
        names = PICKUP_AMMO
    elif kind == "6":
        names = PICKUP_CLEARANCES
    else:
        # The kind has nothing to choose between.
        return [("0", "- none -", "This kind has one variant")]

    return [(str(index), name, "") for index, name in enumerate(names)]

# ---- object type ----------------------------------------------------------
# The one switch that decides which path the engine takes. It is authoritative:
# changing it clears the switches that belong to the old type, because a door
# flag on a placed object is not a door, it is a module flag on something the
# module branch never sees.

# `Projload.cpp:2078` picks the branch from the header flag, and every
# strategy lives in exactly one branch (`Objsetup.cpp`):
#   module   StratDoor (50), StratSwitchDoor (59), StratLift (51), Lighting
#   placed   PlatLift (54), SimpleObject, NewSimpleObject (62),
#            Track (72), TrackDestruct (80), Fan (75)
# Track is placed, which is why it gets its own entry rather than living under
# Module -- exported as a module its strategy would never be dispatched.
OBJECT_TYPE_MODULE = "MODULE"
OBJECT_TYPE_SIMPLE = "SIMPLE_OBJECT"

TYPE_SWITCHES = {
    OBJECT_TYPE_MODULE: (
        "rif_module_sky", "rif_module_fog", "rif_module_airduct",
        "rif_module_stairs", "rif_module_door",
        "rif_module_door_locked", "rif_module_door_proximity",
        "rif_module_door_open", "rif_module_door_lift",
        "rif_module_door_horizontal",
    ),
    OBJECT_TYPE_SIMPLE: (
        "rif_fragments", "rif_module_plat_lift", "rif_module_fan",
        "rif_plat_lift_disabled", "rif_plat_lift_one_use",
    ),
}


def _on_object_type(self, context):
    """Clear the switches that do not belong to the new type."""
    keep = TYPE_SWITCHES.get(self.rif_object_type, ())
    for group in TYPE_SWITCHES.values():
        for name in group:
            if name not in keep and hasattr(self, name):
                try:
                    setattr(self, name, False)
                except (AttributeError, TypeError):
                    pass


bpy.types.Object.rif_placed_light = bpy.props.BoolProperty(
    name="Emits light",
    description=(
        "The object is a lamp: it carries a PLOBJLIT record, and the light "
        "comes out of the object's origin, so moving the object moves the "
        "light. A light that has to go out when the lamp is shot must not be "
        "baked into the vertices, which is why every retail lamp is marked "
        "Runtime and NoPreLight"),
    default=False)

bpy.types.Object.rif_light_brightness = bpy.props.FloatProperty(
    name="Brightness",
    description=("Fraction of full brightness. Stored as 16.16, so 1.0 is "
                 "65536 -- the value 461 of the retail lamps use"),
    default=1.0, min=0.0, soft_max=4.0, precision=2)

bpy.types.Object.rif_light_range = bpy.props.FloatProperty(
    name="Range",
    description="How far the light reaches, in metres",
    default=10.0, min=0.1, soft_max=100.0, precision=2)

bpy.types.Object.rif_light_colour = bpy.props.FloatVectorProperty(
    name="Colour",
    description="Colour while the light is on",
    subtype="COLOR", default=(1.0, 1.0, 1.0), min=0.0, max=1.0, size=3)

bpy.types.Object.rif_light_off_colour = bpy.props.FloatVectorProperty(
    name="Off colour",
    description=("Colour while the light is off. The engine fades between "
                 "the two, so this is what a dying lamp cools to"),
    subtype="COLOR", default=(0.0, 0.0, 0.0), min=0.0, max=1.0, size=3)

bpy.types.Object.rif_light_type = bpy.props.EnumProperty(
    name="Type",
    description=("How the light behaves. Visible in game only -- the "
                 "viewport shows a steady light"),
    items=(
        ("0", "Standard", "Steady light"),
        ("1", "Strobe", "Blinks at a fixed rate"),
        ("2", "Flicker", "Random, like a failing tube"),
    ), default="0")

bpy.types.Object.rif_light_on_off_type = bpy.props.EnumProperty(
    name="Switching",
    description="How the light turns on and off",
    items=(
        ("0", "Standard", "Snaps on and off"),
        ("1", "Fade", "Ramps up and down over time"),
        ("2", "Flicker", "Random switching"),
    ), default="0")

bpy.types.Object.rif_light_on_at_start = bpy.props.BoolProperty(
    name="On at start",
    description="The lamp is lit when the level loads", default=True)

bpy.types.Object.rif_light_fade_up_ms = bpy.props.IntProperty(
    name="Fade up",
    description="Milliseconds to reach full brightness", default=0,
    min=0, soft_max=10000)

bpy.types.Object.rif_light_fade_down_ms = bpy.props.IntProperty(
    name="Fade down",
    description="Milliseconds to reach the off colour", default=0,
    min=0, soft_max=10000)

bpy.types.Object.rif_light_on_ms = bpy.props.IntProperty(
    name="On for",
    description="Milliseconds lit in a blink cycle", default=0,
    min=0, soft_max=10000)

bpy.types.Object.rif_light_off_ms = bpy.props.IntProperty(
    name="Off for",
    description="Milliseconds dark in a blink cycle", default=0,
    min=0, soft_max=10000)

bpy.types.Object.rif_light_start_ms = bpy.props.IntProperty(
    name="Start delay",
    description="Milliseconds before the light starts", default=0,
    min=0, soft_max=10000)

# ---- the corona is a surface, not an object ----------------------------
bpy.types.Material.rif_material_light_corona = bpy.props.BoolProperty(
    name="Light corona",
    description=(
        "Draw the polygons using this material as a light flare: a billboard "
        "that turns to face the camera and scales with distance. Its position "
        "is the centre of its polygon, so it can sit away from the lamp's "
        "origin. Only one corona per object takes effect, and it does nothing "
        "unless the object is a light"),
    default=False)

bpy.types.Object.rif_placed_switch = bpy.props.BoolProperty(
    name="Lever",
    description=(
        "A switch the player operates: aim at it and press the operate key. "
        "The engine takes the nearest operable object in front within three "
        "metres and with a clear line of sight (TRIGGERS.C:20-22). Give it a "
        "track and the lever itself moves when pulled"),
    default=False)

bpy.types.Object.rif_switch_target = bpy.props.PointerProperty(
    name="Target",
    description=("What the switch sends its request to. A pointer rather "
                 "than a name, so deleting the target clears the link"),
    type=bpy.types.Object)

bpy.types.Object.rif_switch_press_state = bpy.props.BoolProperty(
    name="Press sends state 1",
    description=(
        "The state bit sent when the lever is pressed. The release always "
        "sends the opposite. 0 and 1 are not off and on: each target reads "
        "them its own way, so the labels above are what matters"),
    default=False)

bpy.types.Object.rif_switch_extra = bpy.props.IntProperty(
    name="Extra info",
    description=("The bits above the state bit. Carried to the target "
                 "unchanged; 0 in every retail switch"),
    default=0, min=0, soft_max=255)

bpy.types.Object.rif_switch_mode = bpy.props.EnumProperty(
    name="Mode",
    description="How the switch responds to being operated",
    items=(
        ("1", "Wait", "Wait for a request, then act -- what all four retail "
               "switches do"),
        ("0", "Timer", "Fire once a timer expires"),
        ("2", "Toggle", "Flip state on every press"),
        ("4", "Time delay", "Act after a delay"),
        ("5", "Time delay, autoexec", "The delay starts when the level loads"),
    ), default="1")

bpy.types.Object.rif_switch_time = bpy.props.IntProperty(
    name="Time",
    description=("Milliseconds, used by the timer modes. Zero in a timed "
                 "mode means it never fires"),
    default=0, min=0, soft_max=60000)

bpy.types.Object.rif_switch_security = bpy.props.IntProperty(
    name="Security",
    description=("Clearance needed to operate it. Zero means anyone can; "
                 "the clearance comes from the Security Pass pickup"),
    default=0, min=0, max=255)

bpy.types.Object.rif_switch_starts_on = bpy.props.BoolProperty(
    name="Starts on",
    description="The switch begins in its unusual state", default=False)

bpy.types.Object.rif_switch_off_same = bpy.props.BoolProperty(
    name="Release sends the same",
    description=("Send the same request on release instead of the opposite. "
                 "BinSwitchFlag_OffMessageSame"),
    default=False)

bpy.types.Object.rif_switch_off_none = bpy.props.BoolProperty(
    name="Release sends nothing",
    description="Send no request on release. BinSwitchFlag_OffMessageNone",
    default=False)

bpy.types.Object.rif_destructible = bpy.props.BoolProperty(
    name="Destructible",
    description=(
        "The object can be destroyed. Off means it takes no damage at all, "
        "which is what 2453 of the shipped objects are"),
    default=False)

bpy.types.Object.rif_integrity = bpy.props.IntProperty(
    name="Integrity",
    description=("How many hits it takes. 16 is what every lamp in the "
                 "shipped levels uses"),
    default=1, min=1, soft_max=100)

bpy.types.Object.rif_trigger_on_destroy = bpy.props.BoolProperty(
    name="Notify when destroyed",
    description=("Send the request below once the object is destroyed. "
                 "1388 shipped objects do"),
    default=False)

bpy.types.Object.rif_trigger_on_pickup = bpy.props.BoolProperty(
    name="Notify when picked up",
    description=("Send the request below when the object is picked up. "
                 "703 shipped objects do"),
    default=False)

bpy.types.Object.rif_trigger_explosion = bpy.props.EnumProperty(
    name="Explosion",
    description="What it looks like when it goes",
    items=(
        ("0", "None", "No explosion"),
        ("1", "Small", "A barrel going up -- what the retail lamps use"),
        ("2", "Big", "A large blast"),
        ("3", "Molotov", "A burning pool"),
    ), default="0")

bpy.types.Object.rif_trigger_target = bpy.props.PointerProperty(
    name="Target",
    description="What receives the request",
    type=bpy.types.Object)

bpy.types.Object.rif_trigger_state = bpy.props.BoolProperty(
    name="Send state 1",
    description=("The state bit of the request. Its meaning depends on the "
                 "target, so the panel names the action instead"),
    default=False)

bpy.types.Object.rif_pickup = bpy.props.BoolProperty(
    name="Is a pickup",
    description=(
        "The player can pick this up by touching it. What happens then is "
        "decided by the kind and subtype, not by the trigger"),
    default=False)

bpy.types.Object.rif_pickup_kind = bpy.props.EnumProperty(
    name="Kind",
    description="What the object is (INANIMATEOBJECT_TYPE, PVISIBLE.H:16)",
    items=(
        ("2", "Weapon", "A weapon, with the subtype choosing which"),
        ("3", "Ammo", "Ammunition"),
        ("4", "Health", "A medkit"),
        ("5", "Armour", "Armour"),
        ("6", "Key", "A key card, which sets the security clearance"),
        ("7", "Boxed sentry gun", "A sentry gun in a box"),
        ("8", "IR goggles", "The infrared goggles"),
        ("10", "Data tape", "A mission data tape"),
        ("11", "Motion tracker upgrade", "Upgrades the tracker"),
        ("12", "Pheromone pod", "Lets the Alien command drones"),
        ("13", "Special pickup", "The catch-all used for the security pass"),
        ("14", "Destroy base", "Touching it destroys the base"),
        ("15", "Field charge", "The field charge"),
    ), default="2")

bpy.types.Object.rif_pickup_subtype = bpy.props.EnumProperty(
    name="Subtype",
    description=("Which one of that kind. The list follows the kind, so a "
                 "weapon number never appears for a piece of ammo"),
    items=pickup_subtype_items)

bpy.types.Object.rif_object_type = bpy.props.EnumProperty(
    name="Type",
    description="What this object is to the engine",
    items=(
        (OBJECT_TYPE_MODULE, "Module",
         "Part of the level's shell: rooms, walls and doors. Loaded down the "
         "module path, so it reaches the visibility and AI graphs"),
        (OBJECT_TYPE_SIMPLE, "Simple Object",
         "A placed object: detail, a pickup, something that breaks, a lift or "
         "a fan. Not part of the shell, so it has no visibility list and no "
         "AI adjacency. With every option off it is plain decoration, which "
         "is the commonest placed object in the shipped levels. Give it a "
         "track and it also moves, which is all the old Track type ever meant"),
    ),
    default=OBJECT_TYPE_MODULE,
    update=_on_object_type,
)


# ---- track (OBJTRAK2) ----------------------------------------------------
# Four strategies read this chunk: Track (72), TrackDestruct (80), Fan (75)
# and PlatLift (54). It belongs to the object rather than to the strategy, so
# the panel groups it with the object instead of with a behaviour.
bpy.types.Object.rif_track_enabled = bpy.props.BoolProperty(
    name="Has a track",
    description=(
        "Give this object a track. Track, TrackDestruct, Fan and PlatLift "
        "all read it, and Fan does not merely misbehave without one: "
        "Objsetup.cpp:1159 asserts and the level will not load"),
    default=False)

bpy.types.Object.rif_track_end = bpy.props.PointerProperty(
    name="End point",
    description=(
        "An empty marking where the movement stops. Leave it unset and the "
        "track does not move -- which is what 481 of the 1312 retail "
        "sections do, and what a fan wants: its direction comes from the "
        "rotation, not from the distance"),
    type=bpy.types.Object)

bpy.types.Object.rif_track_seconds = bpy.props.FloatProperty(
    name="Seconds",
    description=(
        "How long the run takes. Stored as 16.16 seconds, the same unit as "
        "the fan's spin-up time. Retail uses 0.25, 0.5, 1, 2, 3, 5, 7 and "
        "10 seconds"),
    default=1.0, min=0.01, soft_max=30.0, precision=2)


bpy.types.Object.rif_module_fan = bpy.props.BoolProperty(
    name="Fan",
    description=(
        "Push things away along its track. A fan is a placed object with no "
        "geometry of its own; the direction comes from the first section of "
        "its track, so Has a track is required as well -- without one the "
        "engine asserts at Objsetup.cpp:1159 and the level will not load"),
    default=False)

bpy.types.Object.rif_fan_strength = bpy.props.FloatProperty(
    name="Strength",
    description=(
        "Wind strength as a fraction of full, so 1.0 is full and -0.5 blows "
        "the other way. Retail runs between 0 and +-10. Stored in per mille, "
        "and the engine replaces anything outside +-10 with full strength"),
    default=1.0, min=-10.0, max=10.0, precision=2)

bpy.types.Object.rif_fan_speed_seconds = bpy.props.FloatProperty(
    name="Spin up",
    description=(
        "Seconds to reach full speed. Every retail fan uses 10. Stored as "
        "16.16 seconds, which is why the file holds 655360 for this value"),
    default=10.0, min=0.1, soft_max=60.0, precision=2)

bpy.types.Object.rif_fan_slow_seconds = bpy.props.FloatProperty(
    name="Spin down",
    description="Seconds to come to rest. Every retail fan uses 10",
    default=10.0, min=0.1, soft_max=60.0, precision=2)

bpy.types.Object.rif_module_plat_lift = bpy.props.BoolProperty(
    name="Platform lift",
    description=(
        "Rise and fall along its track on demand. Needs Has a track like a "
        "fan does: the movement comes from the track, and the lift only "
        "needs its endpoint"),
    default=False)

bpy.types.Object.rif_plat_lift_disabled = bpy.props.BoolProperty(
    name="Starts disabled",
    description=(
        "PlatformLiftFlags_Disabled (0x1). 22 of the 74 retail lifts carry "
        "it, so a lift that waits to be switched on is the commoner case"),
    default=False)

bpy.types.Object.rif_plat_lift_one_use = bpy.props.BoolProperty(
    name="One use",
    description=(
        "PlatformLiftFlags_OneUse (0x2): the lift makes one trip and stops. "
        "Only 2 of the 74 retail lifts set it"),
    default=False)

bpy.types.Object.rif_fragments = bpy.props.BoolProperty(
    name="Breaks into pieces",
    description=(
        "The object shatters, and its mesh children are the pieces. This "
        "is what the player sees and shoots; it is replaced by the pieces "
        "only when destroyed. Exported as a placed object, because 6575 of "
        "the 6582 fragmenting objects in the shipped levels are placed and "
        "only seven are modules"),
    default=False)

bpy.types.Object.rif_fragment_copies = bpy.props.IntProperty(
    name="Copies",
    description=(
        "How many times this piece appears in the break (FRAGDATA). One is "
        "the default and what the engine assumes when the count is absent"),
    default=1, min=1, soft_max=20)

bpy.types.Object.rif_track_loop = bpy.props.BoolProperty(
    name="Loop",
    description="Start again from the beginning when the end is reached",
    default=False)

bpy.types.Object.rif_track_playing_at_start = bpy.props.BoolProperty(
    name="Moving at start",
    description="Begin moving as soon as the level loads",
    default=False)

bpy.types.Object.rif_track_loop_back_forth = bpy.props.BoolProperty(
    name="Loop back and forth",
    description="Reverse at the end instead of jumping back to the start",
    default=False)

bpy.types.Object.rif_track_smoothing = bpy.props.BoolProperty(
    name="Smooth",
    description="Ease in and out rather than moving at a constant speed",
    default=False)

bpy.types.Object.rif_track_object_offset = bpy.props.IntVectorProperty(
    name="Object offset",
    description=(
        "Shift the object relative to its track, in millimetres. 615 of "
        "retail's 663 tracks leave this at zero, so it is a correction "
        "rather than part of the design"),
    default=(0, 0, 0), subtype="XYZ", size=3)

def _draw_movement(layout, obj):
    """Draw the track a moving object reads.

    Shared by the lever, the fan, the lift and the Track type: all four store the
    same OBJTRAK2, so all four show the same fields in the same order.
    """
    box = layout.box()
    box.label(text="Movement", icon="CURVE_PATH")
    box.prop(obj, "rif_track_enabled")
    if not getattr(obj, "rif_track_enabled", False):
        return
    inner = box.box()
    inner.prop(obj, "rif_track_end")
    inner.prop(obj, "rif_track_seconds")
    if obj.rif_track_end is None:
        inner.label(text="No end point: the track stays put", icon="INFO")
    flags = box.box()
    flags.label(text="Behaviour", icon="PLAY")
    flags.prop(obj, "rif_track_loop")
    flags.prop(obj, "rif_track_playing_at_start")
    flags.prop(obj, "rif_track_loop_back_forth")
    flags.prop(obj, "rif_track_smoothing")
    advanced = box.box()
    advanced.label(text="Advanced", icon="PREFERENCES")
    advanced.prop(obj, "rif_track_object_offset")


def switch_action_names(target):
    """Return (state 1 label, state 0 label) for what this target does.

    `RequestState` reads the low bit of a request as a state and lets each
    target interpret it (`BH_TYPES.C:2180-2400`): a proximity door unlocks on
    1, a platform lift enables on 1, a switch calls it the unusual state. The
    labels here are that table, chosen from what the target object is.
    """
    if target is None:
        return ("State 1", "State 0")
    if getattr(target, "rif_module_door", False):
        if getattr(target, "rif_module_door_lift", False):
            return ("Open", "Close")
        return ("Unlock", "Lock")
    if getattr(target, "rif_module_plat_lift", False):
        return ("Enable", "Disable")
    if getattr(target, "rif_placed_light", False):
        return ("Brighten", "Dim")
    if getattr(target, "rif_placed_switch", False):
        return ("Unusual state", "Rest state")
    return ("State 1", "State 0")


def _patrol_items(_self, context):
    """Return the scene's patrol routes, named, for the generator's list."""
    # A non-empty identifier: Blender discards an enum row whose id is "".
    # `sounds.NONE_CHOICE` is the same sentinel the sound lists use.
    from .sounds import NONE_CHOICE
    entries = [(NONE_CHOICE, "(none)", "No route chosen")]
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) == KIND_PATROL_PATH:
            entries.append((obj.name, obj.name, "Walk this route"))
    return entries


def _sound_folder_items(_self, context):
    """Return the env folders as (identifier, label, description).

    Generated from the fastfiles and shipped, so the panel does not have to open
    109 files to fill a dropdown.
    """
    from . import sounds

    # An explicit empty choice, first: an enum with no declared default selects
    # its first item, which would count as a folder the artist never picked.
    entries = [(sounds.NONE_CHOICE, "(none)",
                "Sounds will not resolve without a folder")]
    folders = sounds.folder_list()
    if not folders:
        # No shipped table: the empty entry alone, rather than a folder name
        # that may not exist on this install.
        return entries
    entries.extend((name, name, f"{count} sounds") for name, count in folders)
    return entries


def _sound_items(_self, context):
    """Return (identifier, label, description) for every sound in the library.

    Blender calls this whenever the list is drawn, so it reads the shipped table
    rather than scanning the game: the scan takes minutes, the table takes
    milliseconds. The order is the library's own -- the env catalogue first,
    then most used -- and the counts stay out of the labels.
    """
    from . import sounds

    # First, an explicit empty choice; without one Blender selects the first
    # sound in the list, which looks like a deliberate choice and is not.
    entries = [(sounds.NONE_CHOICE, "(none)", "No sound chosen yet")]
    root = sounds.game_root(context)
    if not root:
        return entries + [(sounds.NONE_CHOICE,
                       "(set the game directory in preferences)", "")]
    names = sounds.sound_names(root, sounds.directory_of(context))
    if not names:
        return entries + [(sounds.NONE_CHOICE,
                       "(set Sound Directory for this scene)", "")]
    entries.extend((name, name, f"Play {name}") for name in names)
    return entries


def _warn_shape(operator, spec, reason):
    """Report that an entry could not build its model.

    `operator` is optional, so this can be called from a place that has none;
    the caller still gets its cube either way.
    """
    message = (f"{spec.label}: {reason}. A plain box was placed instead -- set "
               "the Game Directory in the add-on preferences")
    if operator is not None and hasattr(operator, "report"):
        operator.report({"WARNING"}, message)
    else:
        print(f"[RIF] WARNING: {message}")


def _sync_pickup_variant(obj, spec):
    """Point the variant list at the sub_type the entry presets.

    lags sets sub_type, the number the file wants; the panel shows
    pickup_variant, so without this the list sat on its first entry and every
    pickup read as a pulse rifle. The list is a view of the number, so it is set
    from it here rather than repeated in every row of the table.
    """
    if spec.kind != KIND_PICKUP:
        return
    sub_type = getattr(obj.rif_placeable, "sub_type", 0)
    try:
        obj.rif_placeable.pickup_variant = str(sub_type)
    except (TypeError, ValueError):
        # A sub_type the list does not offer: leave the list where it is rather
        # than raising while placing an object.
        pass


def _shape_mesh_for(spec, context, operator=None):
    """Return a mesh read from shape_rifs for this entry, or None.

    `None` means the entry has no model or the file could not be read; the
    caller then keeps the cube. A missing model is reported rather than passed
    over, because the entry still says "Pulse Rifle" and the cube that appears
    instead looks like a bug in the menu rather than a missing file.
    """
    if not spec.shape_file:
        return None
    preferences = context.preferences.addons.get(__package__)
    if preferences is None:
        return None
    game_directory = preferences.preferences.game_directory.strip()
    if not game_directory:
        _warn_shape(operator, spec,
                    "no Game Directory is set, so the model could not be read")
        return None

    from pathlib import Path
    import struct

    from .rif import shape_reader

    path = Path(game_directory) / "shape_rifs" / f"{spec.shape_file}.rif"
    if not path.is_file():
        # Windows does not care about case, so one more try costs nothing.
        folder = path.parent
        if folder.is_dir():
            wanted = path.name.lower()
            for candidate in folder.iterdir():
                if candidate.name.lower() == wanted:
                    path = candidate
                    break
    if not path.is_file():
        folder = Path(game_directory) / "shape_rifs"
        if not folder.is_dir():
            _warn_shape(operator, spec, f"no shape_rifs folder in {game_directory}")
        else:
            _warn_shape(operator, spec, f"{spec.shape_file}.rif is not in {folder}")
        return None

    try:
        shapes = shape_reader.read_shapes(path)
    except (OSError, ValueError, struct.error) as exc:
        _warn_shape(operator, spec, f"{path.name} could not be read: {exc}")
        return None
    if not shapes:
        _warn_shape(operator, spec, f"{path.name} holds no usable shape")
        return None

    shape = shapes[0]
    # One call builds geometry, winding, smoothing, UVs and materials together:
    # doing only the geometry here is what made pickups arrive flat.
    return shape_reader.build_mesh(shape, spec.shape_file)


def _make_operator(spec):
    """Build the operator class for one menu entry.

    A class per entry is what gives each menu item its own tooltip: Blender
    reads the tooltip from the operator, so a single shared operator would show
    one text for every entry.
    """

    def execute(self, context):
        """Create the placeable object and select it.

        A waypoint is a **wireframe cube mesh**. Its volume is functional —
        neighbouring waypoints are linked by how their boxes meet — so the box
        has to be seen and adjusted, but a shaded cube would hide the level
        being built. `display_type = WIRE` draws only the edges, and unlike an
        empty it still has a real `bound_box`, so the exported size needs no
        guesswork about how Blender interprets a display size.
        """
        shape_mesh = _shape_mesh_for(spec, context, self)
        if shape_mesh is not None:
            # A pickup with a model is that mesh plus everything the ordinary
            # path sets. Leaving the marker off made it look like a plain mesh,
            # so the AvP Object panel appeared instead of the AvP Placeable one
            # that belongs to a pickup.
            obj = bpy.data.objects.new(spec.shape_file, shape_mesh)
            context.collection.objects.link(obj)
            obj[PLACEABLE_PROP] = spec.kind
            obj.rif_placeable.kind = spec.kind
            for key, value in spec.object_flags.items():
                setattr(obj, key, value)
            if spec.pickup_type is not None:
                obj.rif_placeable.pickup_type = spec.pickup_type
            for key, value in spec.flags.items():
                setattr(obj.rif_placeable, key, value)
            _sync_pickup_variant(obj, spec)
            for other in context.selected_objects:
                other.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            return {"FINISHED"}

        if spec.kind == KIND_SOUND:
            # A sphere reads as "a radius", which is what the object is, so the
            # mesh is generated rather than fetched from a Blender operator:
            # that keeps this branch like the others and testable offline.
            segments, rings = 16, 8
            verts = []
            for ring in range(rings + 1):
                phi = math.pi * ring / rings
                for seg in range(segments):
                    theta = 2.0 * math.pi * seg / segments
                    verts.append((math.sin(phi) * math.cos(theta),
                                  math.sin(phi) * math.sin(theta),
                                  math.cos(phi)))
            faces = []
            for ring in range(rings):
                for seg in range(segments):
                    a = ring * segments + seg
                    b = ring * segments + (seg + 1) % segments
                    c = (ring + 1) * segments + (seg + 1) % segments
                    d = (ring + 1) * segments + seg
                    faces.append((a, b, c, d))
            mesh = bpy.data.meshes.new("Sound")
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label.split("  (")[0], mesh)
            obj.display_type = "WIRE"
            # 20 m, the commonest reach in retail: the mesh has a radius of one
            # metre, so the scale in metres is the radius in metres.
            obj.scale = (20.0, 20.0, 20.0)
            mesh.materials.append(_make_waypoint_material())
        elif spec.kind in (KIND_PICKUP, KIND_OBJECT):
            # A pickup is a placed object with its own REBSHAPE, so it has to
            # be a mesh. A small cube stands in until the artist swaps in the
            # real model; the dimensions here are only a placement aid and are
            # not written to the strategy, which carries no size.
            mesh = bpy.data.meshes.new("Pickup")
            verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1)
                     for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label.split("  (")[0], mesh)
            obj.scale = (0.15, 0.15, 0.15)
        elif spec.kind == KIND_AREA_SWITCH:
            # A box, not a marker: the engine tests this volume, so it is the
            # trigger and not a note about where something is.
            mesh = bpy.data.meshes.new("AreaSwitch")
            verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1)
                     for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label.split("  (")[0], mesh)
            obj.display_type = "WIRE"
            mesh.materials.append(_make_waypoint_material())
            obj.scale = (2.0, 2.0, 2.0)
        elif spec.kind == KIND_DEATH_VOLUME:
            # A real box: its bounds ARE the volume, so a viewport-only empty
            # would leave nothing to write.
            mesh = bpy.data.meshes.new("KillVolume")
            verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1)
                     for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label.split("  (")[0], mesh)
            obj.display_type = "WIRE"
            mesh.materials.append(_make_waypoint_material())
            # 4 m on a side, a room-sized trap.
            obj.scale = (2.0, 2.0, 2.0)
        elif spec.kind == KIND_PATROL_PATH:
            # A real curve, not an empty with a curve-shaped display: the
            # control points ARE the route, and the collector reads them.
            curve = bpy.data.curves.new("PatrolPath", type="CURVE")
            curve.dimensions = "3D"
            spline = curve.splines.new("POLY")
            spline.points.add(1)
            for step, handle in enumerate(spline.points):
                handle.co = (float(step) * 8.0, 0.0, 0.0, 1.0)
            obj = bpy.data.objects.new(spec.label.split("  (")[0], curve)
        elif spec.kind == KIND_WAYPOINT:
            mesh = bpy.data.meshes.new("Waypoint")
            verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1)
                     for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label.split("  (")[0], mesh)
            obj.display_type = "WIRE"
            mesh.materials.append(_make_waypoint_material())
            # 8.5 m on a side. Retail's median waypoint in 	rapped measures
            # 8550 mm, and a smaller volume makes a graph that is technically
            # connected and too sparse to travel along -- the bots stand in it
            # and never leave, which looks like a broken exporter and is not.
            obj.scale = (4.25, 4.25, 4.25)
        elif spec.kind in (KIND_MODULE_ROOM, KIND_MODULE_DOOR):
            mesh = bpy.data.meshes.new(spec.label)
            half = 1.0
            verts = [(x * half, y * half, z * half)
                     for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            # A room's faces look at whoever stands inside it. Flipping is
            # clearer than reversing six winding orders by hand, and it cannot
            # be got wrong one face at a time.
            mesh.flip_normals()
            if spec.kind == KIND_MODULE_ROOM:
                # Three surfaces, because a ceiling is not a wall: it is lit
                # from below, it is never walked on, and the levels give it its
                # own material. Backface culling is on for all three, matching
                # the rest of the exporter -- the engine draws single-sided
                # polygons with no culling, so leaving it off would show the
                # author the inside of the walls from outside.
                for slot, name in enumerate(("Floor", "Walls", "Ceiling")):
                    material = bpy.data.materials.new(name)
                    material.use_backface_culling = True
                    mesh.materials.append(material)
                # Chosen by position rather than by index: flipping the normals
                # may renumber the faces.
                for polygon in mesh.polygons:
                    height = polygon.center.z
                    if height < 0:
                        polygon.material_index = 0
                    elif height > 0:
                        polygon.material_index = 2
                    else:
                        polygon.material_index = 1
            obj = bpy.data.objects.new(spec.label, mesh)
        elif spec.kind == KIND_SIMPLE_OBJECT:
            mesh = _shape_mesh_for(spec, context)
            if mesh is not None:
                obj = bpy.data.objects.new(spec.shape_file, mesh)
                context.collection.objects.link(obj)
                for key, value in spec.object_flags.items():
                    setattr(obj, key, value)
                for other in context.selected_objects:
                    other.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
                return {"FINISHED"}
            mesh = bpy.data.meshes.new(spec.label)
            half = 0.5
            verts = [(x * half, y * half, z * half)
                     for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                     (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(spec.label, mesh)
            for index in range(spec.pieces):
                # Pieces are children, which is how the exporter reads them:
                # the parent is what the player shoots, they are what is left.
                piece = bpy.data.objects.new(
                    f"{spec.label} piece {index + 1}", mesh.copy())
                piece.parent = obj
                piece.location = ((index - 1) * 0.6, 0.3, 0.0)
                piece.scale = (0.35, 0.35, 0.35)
                context.collection.objects.link(piece)
        else:
            obj = bpy.data.objects.new(spec.label.split("  (")[0], None)
            obj.empty_display_type = spec.display
            obj.empty_display_size = 1.0
        # A preset makes an ordinary object with the switches already set, not
        # a placeable marker: the AvP Object panel polls on PLACEABLE_PROP being
        # absent, so setting it here would hide the very panel the author needs.
        if spec.kind not in GEOMETRY_KINDS:
            obj[PLACEABLE_PROP] = spec.kind
            obj.rif_placeable.kind = spec.kind
        if spec.legacy_marker is not None:
            obj[MARKER_PROP] = spec.legacy_marker
        obj.rif_placeable.npc_type = spec.npc_type
        if spec.pickup_type is not None:
            obj.rif_placeable.pickup_type = spec.pickup_type
        for key, value in spec.flags.items():
            setattr(obj.rif_placeable, key, value)
        for key, value in spec.object_flags.items():
            setattr(obj, key, value)
        _sync_pickup_variant(obj, spec)
        context.collection.objects.link(obj)

        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}

    return type(
        f"RIF_OT_place_{spec.key}",
        (bpy.types.Operator,),
        {
            "__doc__": spec.description,
            "bl_idname": spec.idname,
            "bl_label": spec.label,
            "bl_description": spec.description,
            "bl_options": {"REGISTER", "UNDO"},
            "execute": execute,
        },
    )


# One generated operator class per table row, keyed for the menu to look up.
PLACEABLE_OPERATORS = {spec.key: _make_operator(spec) for spec in PLACEABLES}


def _draw_category(layout, category):
    """Draw every entry of one category into a submenu layout."""
    entries = sorted((spec for spec in PLACEABLES if spec.category == category),
                     key=lambda spec: spec.label.lower())
    previous_group = None
    for spec in entries:
        # Separate the network-legal generators from the single-player ones,
        # because the distinction decides whether an entry is usable at all.
        group = "solo" if "(single player)" in spec.label else "any"
        if previous_group is not None and group != previous_group:
            layout.separator()
        previous_group = group
        layout.operator(spec.idname, text=spec.label, icon=spec.icon)


def _make_category_menu(category, label):
    """Build the submenu class for one category."""

    def draw(self, context):
        """List this category's placeable entries."""
        _draw_category(self.layout, category)

    return type(
        f"RIF_MT_add_{category.lower()}",
        (bpy.types.Menu,),
        {
            "__doc__": f"AvP {label} objects.",
            "bl_idname": f"RIF_MT_add_{category.lower()}",
            "bl_label": label,
            "draw": draw,
        },
    )


CATEGORY_MENUS = {category: _make_category_menu(category, label)
                  for category, label in CATEGORY_LABELS}


class RIF_PT_placeable(bpy.types.Panel):
    """Properties of the selected AvP gameplay object."""

    bl_idname = "RIF_PT_placeable"
    bl_label = "AvP Placeable"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        """Show only when an AvP placeable is selected."""
        obj = context.active_object
        return obj is not None and PLACEABLE_PROP in obj

    def draw(self, context):
        """Draw the fields that matter for the selected object's kind."""
        layout = self.layout
        obj = context.active_object
        settings = obj.rif_placeable
        kind = settings.kind

        layout.label(text=f"Kind: {kind}", icon="OBJECT_DATAMODE")

        if kind in (KIND_PLAYER_START_MP,):
            box = layout.box()
            box.label(text="Allowed races", icon="ARMATURE_DATA")
            row = box.row(align=True)
            row.prop(settings, "race_marine", toggle=True)
            row.prop(settings, "race_alien", toggle=True)
            row.prop(settings, "race_predator", toggle=True)
            box.label(text="Alien is off in skirmish (maxAlien = 0)",
                      icon="INFO")

        if kind in (KIND_NPC, KIND_DEFAULT_ENEMY, KIND_WEIGHTED):
            layout.prop(settings, "npc_type")
            if settings.npc_type == 7:
                layout.prop(settings, "sub_type")
            if settings.npc_type == 2:
                layout.prop(settings, "sub_type")
            layout.prop(settings, "texture_id")
            box = layout.box()
            box.label(text="Behaviour", icon="OPTIONS")
            box.prop(settings, "mission")
            if settings.mission == "5":
                # Only a patrol reads a route, so it is offered only then:
                # a picker that is ignored is worse than no picker.
                box.prop(settings, "patrol_path")
                if not settings.patrol_path:
                    box.label(text="A patrol needs a route",
                              icon="ERROR")
            box.prop(settings, "inactive")
            box.prop(settings, "excluded_hard")

        if kind == KIND_WEIGHTED:
            # The fifteen weights belong to THIS object: it is the one that
            # writes a GENEXSET. They were drawn under Global Generator Data,
            # which carries the budget and no weights at all, so the panel was
            # editing fields on a marker that the engine never reads them from.
            box = layout.box()
            box.label(text="What should spawn here?", icon="MESH_UVSPHERE")
            box.label(text="The engine rolls against the sum", icon="INFO")
            box.label(text="Aliens")
            for field in ("mix_drone", "mix_predalien", "mix_praetorian"):
                box.prop(settings, field)
            box.label(text="Marines")
            for field in ("mix_pulse_marine", "mix_flame_marine",
                          "mix_smart_marine", "mix_sadar_marine",
                          "mix_grenade_marine", "mix_minigun_marine",
                          "mix_pistol_marine"):
                box.prop(settings, field)
            box.label(text="Civilians")
            for field in ("mix_shotgun_civilian", "mix_pistol_civilian",
                          "mix_flame_civilian", "mix_unarmed_civilian",
                          "mix_molotov_civilian"):
                box.prop(settings, field)
            if not any(getattr(settings, f)
                       for f in ("mix_drone", "mix_predalien",
                                 "mix_praetorian", "mix_pulse_marine")):
                box.label(text="No weights: the level's default creature", 
                          icon="ERROR")

        if kind == KIND_MISSION_OBJECTIVE:
            box = layout.box()
            box.label(text="Mission objective", icon="CHECKMARK")
            box.prop(settings, "objective_description")
            box.prop(settings, "objective_complete")
            box.prop(settings, "objective_order")
            box.label(text="One objective per level", icon="INFO")
            # Said in the panel, not only in the tooltip: an objective that
            # never completes looks like a broken export, and the missing
            # piece is a switch, which is not offered yet.
            box.label(text="Needs a switch to complete it", icon="ERROR")

        if kind in (KIND_AREA_SWITCH, KIND_MULTI_SWITCH):
            box = layout.box()
            box.label(
                text=("Area switch" if kind == KIND_AREA_SWITCH
                      else "Multi switch"), icon="TRIA_RIGHT")
            box.prop(settings, "switch_mode")
            if settings.switch_mode in ("0", "4", "5"):
                box.prop(settings, "switch_time")
            box.prop(settings, "switch_rest_on")

            targets = layout.box()
            targets.label(text=f"Targets ({len(settings.switch_targets)})")
            row = targets.row(align=True)
            row.operator("rif.switch_target_add", icon="ADD", text="Add")
            row.operator("rif.switch_target_remove", icon="REMOVE",
                         text="Remove")
            for index, target in enumerate(settings.switch_targets):
                row = targets.row(align=True)
                row.prop(target, "target_request", text="")
                row.prop(target, "target_name", text="")
            if not settings.switch_targets:
                targets.label(text="A switch with no targets does nothing",
                              icon="INFO")

        if kind == KIND_SELF_DESTRUCT:
            box = layout.box()
            box.label(text="Self destruct", icon="ERROR")
            box.prop(settings, "self_destruct_timer")
            box.label(text="Needs a switch to arm it", icon="INFO")

        if kind == KIND_MESSAGE:
            box = layout.box()
            box.label(text="Message", icon="INFO")
            box.prop(settings, "message_string")
            box.prop(settings, "message_shown_at_start")
            # Said in the panel, because the name of the field suggests the
            # opposite and the engine will not show anything on its own.
            box.label(text="A switch shows it, with code 2", icon="ERROR")


        if kind == KIND_PARTICLE_GENERATOR:
            box = layout.box()
            box.label(text="Particle generator", icon="PARTICLES")
            # The emitter is directional: the engine rotates every particle's
            # velocity by this object's own rotation, so where the arrow points
            # is where the sparks go. Said here because nothing on screen shows
            # it and the field has no widget of its own.
            box.label(text="Particles leave along the object's rotation",
                      icon="ORIENTATION_GLOBAL")
            box.prop(settings, "particle_type")
            if settings.particle_type == "0":
                box.prop(settings, "particle_frequency")
            else:
                box.prop(settings, "particle_quantity")
            box.prop(settings, "particle_probability")
            box.prop(settings, "particle_speed")
            box.prop(settings, "particle_active")

            sound = layout.box()
            sound.label(text="Sound", icon="SPEAKER")
            sound.prop(settings, "particle_sound")
            # Imported here and not at the top of draw: an unconditional
            # relative import runs for every kind of object, and the tests load
            # this module standalone, where there is no package to be relative
            # to. _sound_items has always imported it lazily for that reason.
            from . import sounds

            if settings.particle_sound != sounds.NONE_CHOICE:
                # The same button a placed sound offers, and the same operator:
                # it reads whichever field the object's kind uses.
                sound.operator("object.rif_audition_sound",
                               icon="PLAY", text="Play")
                sound.prop(settings, "particle_sound_inner")
                sound.prop(settings, "particle_sound_outer")
                sound.prop(settings, "particle_sound_loop")
            if settings.particle_type in ("0", "3"):
                box.label(text="This one lights the room", icon="INFO")
            if settings.particle_probability == 0:
                box.label(text="0% means almost never", icon="ERROR")
            if settings.particle_speed <= 0:
                # Every particle stays put and they overlap, so the result reads
                # as one particle no matter how many are made.
                box.label(text="Speed 0 piles them up: looks like one",
                          icon="ERROR")

        if kind == KIND_DEATH_VOLUME:
            box = layout.box()
            box.label(text="Kill volume", icon="ERROR")
            box.prop(settings, "death_damage")
            if settings.death_damage == 0:
                box.label(text="0 kills outright", icon="INFO")
            box.prop(settings, "death_starts_on")
            box.prop(settings, "death_collision_required")
            box.label(text="The object's bounds are the volume", icon="INFO")

        if kind == KIND_GLOBAL_GENERATOR:
            box = layout.box()
            box.label(text="Spawning budget", icon="OPTIONS")
            box.prop(settings, "max_npcs")
            box.prop(settings, "npcs_per_minute")
            # `BH_GENER.C:189` turns the whole generator system off when the
            # rate is zero, and nothing in the game says so -- the level simply
            # has no enemies. Said here, where the number is typed, as well as
            # by the validator, which only speaks once the level is exported.
            if settings.npcs_per_minute <= 0:
                box.label(text="0 switches every generator off",
                          icon="ERROR")
            if settings.max_npcs <= 0:
                box.label(text="0 lets nothing spawn at all", icon="ERROR")
            box.prop(settings, "npc_acceleration")
            box.prop(settings, "hive_state_change_time")

            box = layout.box()
            box.label(text="Creature for generators without weights",
                      icon="ARMATURE_DATA")
            box.prop(settings, "enemy_kind")
            box = layout.box()
            box.label(text="One per level", icon="ERROR")
            box.label(text="The game reads a single GLOGENDC")

        if kind == KIND_WAYPOINT:
            box = layout.box()
            box.label(text="Volume: use position and scale", icon="CUBE")

        if kind == KIND_SOUND:
            box = layout.box()
            box.label(text="Sound", icon="SPEAKER")
            box.prop(settings, "sound_wav", text="")
            # The radius is the object's size, so it is reported rather than
            # typed: scale the sphere in the viewport and this follows.
            scale = obj.matrix_world.to_scale()
            radius = (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0
            box.label(text=f"Outer range: {radius * 1000.0:.0f} mm "
                           f"(the sphere's size)")
            box.prop(settings, "sound_inner_fraction")
            box.prop(settings, "sound_volume")
            box.prop(settings, "sound_pitch")
            row = box.row(align=True)
            row.prop(settings, "sound_not_at_start")
            row.prop(settings, "sound_no_loop")
            box.operator("object.rif_audition_sound", icon="PLAY")

        if kind in (KIND_PICKUP, KIND_OBJECT):
            # The fields SimpleStrategy carries that nothing exposed until now.
            box = layout.box()
            box.label(text="Placed object", icon="CUBE")
            if kind == KIND_PICKUP:
                box.prop(settings, "pickup_variant")
            box.prop(settings, "object_mass")
            box.prop(settings, "object_integrity")
            box.prop(settings, "object_heavy")

        if kind == KIND_PATROL_PATH:
            layout.prop(settings, "patrol_closed")


def menu_draw(self, context):
    """Draw the AvP submenu: categories, then the existing helper entries."""
    layout = self.layout
    # Alphabetical, because a menu is looked at far more often than it is
    # reasoned about, and a fixed order means the eye can learn where things are.
    for category, label in sorted(CATEGORY_LABELS, key=lambda row: row[1]):
        layout.menu(CATEGORY_MENUS[category].bl_idname, text=label,
                    icon="COLLECTION_NEW")


class RIF_PT_material_avp(bpy.types.Panel):
    """AvP properties of a material, on the Material tab.

    Kept separate from the object panel because the corona is a property of the
    surface, not of the object: one mesh can be part housing and part glow, and
    the engine asks the polygon, not the object, which it is.
    """

    bl_label = "AvP Material"
    bl_idname = "RIF_PT_material_avp"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        """Only shown when a material is being edited."""
        return context.material is not None

    def draw(self, context):
        """Draw the surface flags this format understands."""
        material = context.material
        layout = self.layout

        box = layout.box()
        box.label(text="Light corona", icon="LIGHT_SUN")
        box.prop(material, "rif_material_light_corona")
        if getattr(material, "rif_material_light_corona", False):
            box.label(text="Drawn as a flare that faces the camera",
                      icon="INFO")
            box.label(text="Its position is the centre of its polygon",
                      icon="INFO")
            box.label(text="Needs a light on the object to do anything",
                      icon="INFO")


class RIF_PT_world_audio(bpy.types.Panel):
    """Audio settings for the current level, kept on the scene's World.

    The folder belongs to the level rather than to the add-on, and a .blend can
    hold several scenes, so the World -- which is stored per scene -- is where
    it goes. The audio itself lives in the game's shared fastfile, so any of the
    listed folders works and a custom level needs none of its own.
    """

    bl_label = "Level Settings"
    bl_idname = "RIF_PT_world_audio"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "world"

    @classmethod
    def poll(cls, context):
        """Only meaningful once the scene has a World to store it on."""
        return context.scene.world is not None

    def draw(self, context):
        """Draw the folder, and say what happens without one."""
        from .sounds import SOUND_DIRECTORY_PROP

        layout = self.layout
        world = context.scene.world
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(world, SOUND_DIRECTORY_PROP)
        if not getattr(world, SOUND_DIRECTORY_PROP, ""):
            layout.label(text="Sounds will not resolve without a folder",
                         icon="ERROR")

        box = layout.box()
        box.label(text="Sky", icon="WORLD")
        box.prop(world, "rif_sky_colour")
        box.prop(world, "rif_stars_in_sky")

        box = layout.box()
        box.label(text="Predator and Marine", icon="OUTLINER_OB_ARMATURE")
        box.prop(world, "rif_predator_grappling_hook")
        box.prop(world, "rif_marine_jetpack")
        box.label(text="The other weapons are always available", icon="INFO")



CLASSES = (
    RIF_PT_module,
    # Before the settings that carry a collection of them.
    RIF_SwitchTarget,
    RIF_ModuleLink,
    RIF_OT_module_link_add,
    RIF_OT_module_link_remove,
    RIF_OT_switch_target_add,
    RIF_OT_switch_target_remove,
    RIF_PlaceableSettings,
    RIF_PT_placeable,
    *PLACEABLE_OPERATORS.values(),
    *CATEGORY_MENUS.values(),
    RIF_PT_world_audio,
    # The corona is a surface, so it lives on the Material tab.
    RIF_PT_material_avp,
)


def register_placeables():
    """Register the placeable objects, their menus and their properties."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Registered here rather than at module scope: the collection needs
    # RIF_ModuleLink to have an rna type, which only exists once the class
    # above has been registered. At module scope it fails to register and every
    # following test sees an Object without the property.
    bpy.types.Object.rif_module_links = bpy.props.CollectionProperty(
        type=RIF_ModuleLink,
        description=(
            "Modules this one is linked to, used when Link Mode is By List. A "
            "link is mutual: the engine derives the visibility graph from the "
            "tree, so a one-sided entry still joins both ways"))
    # The sound folder belongs to the level, and a .blend holds several scenes,
    # so it lives on the World rather than in the addon preferences.
    bpy.types.World.rif_sky_colour = bpy.props.FloatVectorProperty(
        name="Sky",
        description=(
            "The level's sky, as a colour: the engine has no sky texture, it "
            "fills the void with this. Retail's commonest value is a very dark "
            "grey, 30 of 255, which is what this default is"),
        subtype="COLOR", size=3, min=0.0, max=1.0,
        default=(0.0129, 0.0129, 0.0129))
    bpy.types.World.rif_stars_in_sky = bpy.props.BoolProperty(
        name="Stars",
        description=(
            "Scatter a starfield across the sky. Retail sets it on 25 of its "
            "57 levels"),
        default=False)
    bpy.types.World.rif_predator_grappling_hook = bpy.props.BoolProperty(
        name="Grappling hook",
        description=(
            "Give the Predator its grappling hook. Off by default, and off in "
            "the engine's own defaults -- it changes how the race plays more "
            "than the other weapons do"),
        default=False)
    bpy.types.World.rif_marine_jetpack = bpy.props.BoolProperty(
        name="Marine jetpack",
        description="Give the Marine a jetpack",
        default=False)
    bpy.types.World.rif_sound_directory = bpy.props.EnumProperty(
        name="Sound Directory",
        description=(
            "Folder this level's sounds come from. The audio itself is in the "
            "shared common.ffl, so any folder works - a level that sets none "
            "has sounds that cannot resolve"),
        items=lambda self, context: _sound_folder_items(self, context),
    )
    # Per-module flags, read from the engine rather than guessed. Kept on the
    # object because that is what the engine checks.
    bpy.types.Object.rif_module_sky = bpy.props.BoolProperty(
        name="Open sky",
        description=(
            "This module is under open sky. The engine draws the sky only when "
            "a module the player is standing in carries this, so without it the "
            "sky colour in World > Level Settings does nothing. Note that a "
            "starfield, if enabled for the level, replaces the clouds rather "
            "than joining them"),
        default=False)
    bpy.types.Object.rif_module_fog = bpy.props.BoolProperty(
        name="Fog",
        description="Fog fills this module",
        default=False)
    bpy.types.Object.rif_module_airduct = bpy.props.BoolProperty(
        name="Air duct",
        description=(
            "An air duct. Aliens always crawl here, the far AI treats it as a "
            "duct rather than a room, and the clearance a bot needs is halved "
            "-- which is what lets one fit through a space the player cannot"),
        default=False)
    bpy.types.Object.rif_module_stairs = bpy.props.BoolProperty(
        name="Stairs",
        description=(
            "Stairs. The engine makes a bot here walk straight at its target "
            "and ignore waypoints, because it otherwise snags on the small "
            "polygons of a staircase. The engine's own comment calls this a "
            "hack, so mark only actual staircases"),
        default=False)
    bpy.types.Object.rif_module_door = bpy.props.BoolProperty(
        name="Door",
        description=(
            "This module is a door. The engine moves it along the shape it "
            "already has, so the geometry is the door and the strategy only "
            "says how it behaves"),
        default=False)
    bpy.types.Object.rif_module_door_locked = bpy.props.BoolProperty(
        name="Locked",
        description=(
            "Takes the locked path (Objsetup.cpp:1831), so the door sits still "
            "until a switch opens it. A locked door does not react to the player "
            "walking up to it; that is what Proximity is for, and it only works "
            "on an unlocked door"),
        default=True)
    bpy.types.Object.rif_module_door_proximity = bpy.props.BoolProperty(
        name="Opens on approach",
        description=(
            "Opens when something comes near (Objsetup.cpp:1919). This is the "
            "branch taken when Locked is OFF; a locked door ignores it. 136 of "
            "the 417 retail doors carry this bit alongside Locked, where it "
            "takes effect only once the door is unlocked"),
        default=True)
    bpy.types.Object.rif_module_door_open = bpy.props.BoolProperty(
        name="Starts open",
        description=(
            "The door begins in the open state. Objsetup.cpp:1870 reads this "
            "bit as exactly that: state = I_door_open when it is set, I_door "
            "when it is not. Only 6 of the 417 retail doors set it"),
        default=False)
    bpy.types.Object.rif_module_door_lift = bpy.props.BoolProperty(
        name="Lift door",
        description=(
            "Takes the lift path (Objsetup.cpp:1915): the door moves with the "
            "car rather than on its own. 211 of the 417 retail doors carry "
            "this, so more than half of them are lift doorways"),
        default=False)
    bpy.types.Object.rif_module_door_horizontal = bpy.props.BoolProperty(
        name="Horizontal",
        description=(
            "The door lies flat, like a hatch (Objsetup.cpp:1836). Only 6 of "
            "the 417 retail doors carry it"),
        default=False)
    bpy.types.Object.rif_module_door_open_seconds = bpy.props.FloatProperty(
        name="Open time (s)",
        description=(
            "How long it takes to open. Retail uses 1.0 for 74 per cent of "
            "its doors. The field is one byte of tenths, so 25.5 s is the "
            "ceiling"),
        default=1.0, min=0.0, max=25.5)
    bpy.types.Object.rif_module_door_close_seconds = bpy.props.FloatProperty(
        name="Close time (s)",
        description=(
            "How long it takes to close. Retail uses 0.5 for 89 per cent of "
            "its doors"),
        default=0.5, min=0.0, max=25.5)
    bpy.types.Object.rif_placeable = bpy.props.PointerProperty(
        type=RIF_PlaceableSettings)


def unregister_placeables():
    """Undo everything register_placeables() did."""
    del bpy.types.World.rif_sound_directory
    del bpy.types.Object.rif_placeable
    # The module flags, in the reverse order to their creation.
    del bpy.types.Object.rif_module_sky
    del bpy.types.Object.rif_module_fog
    del bpy.types.Object.rif_module_airduct
    del bpy.types.Object.rif_module_stairs
    del bpy.types.Object.rif_module_door
    del bpy.types.Object.rif_module_door_locked
    del bpy.types.Object.rif_module_door_proximity
    del bpy.types.Object.rif_module_door_open
    del bpy.types.Object.rif_module_door_lift
    del bpy.types.Object.rif_module_door_horizontal
    del bpy.types.Object.rif_module_door_open_seconds
    del bpy.types.Object.rif_module_door_close_seconds
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
