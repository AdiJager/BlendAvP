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
    (CAT_PLAYER_START, "Player Start"),
    (CAT_GENERATOR, "NPC Generator"),
    (CAT_PICKUP, "Pickups"),
    (CAT_OBJECT, "Objects"),
    (CAT_SOUND, "Sound"),
    (CAT_EFFECTS, "Effects"),
    (CAT_MORPH, "Morphs"),
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
# Entries that exist so the menu is complete and the shape of the tool is
# visible, but write nothing yet. They are reported as unsupported on
# export rather than dropped in silence.
KIND_PLACEHOLDER = "PLACEHOLDER"

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
    can_crawl: bpy.props.BoolProperty(
        name="Alien can crawl here",
        description=(
            "wayflag_cancrawl. Marks a waypoint as reachable on walls and "
            "ceilings; 21% of retail waypoints have it"
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
                 pickup_type=None):
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
        # Set on objects the existing export path finds by marker rather than
        # by the new `rif_placeable` property.
        self.legacy_marker = legacy_marker

    @property
    def idname(self):
        """Return the Blender operator id this entry is generated as."""
        return f"object.rif_place_{self.key}"


# The whole menu, as data: one row per entry, each with its own tooltip.
PLACEABLES = (
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
        flags={"sub_type": 0}),
    _PlaceableSpec(
        "pickup_weapon_smartgun", "Weapon: Smartgun", (
            "IOT_Weapon (2), sub_type 2. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 2}),
    _PlaceableSpec(
        "pickup_weapon_flamethrower", "Weapon: Flamethrower", (
            "IOT_Weapon (2), sub_type 3. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 3}),
    _PlaceableSpec(
        "pickup_weapon_sadar", "Weapon: SADAR", (
            "IOT_Weapon (2), sub_type 5. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 5}),
    _PlaceableSpec(
        "pickup_weapon_grenadelauncher", "Weapon: Grenade Launcher", (
            "IOT_Weapon (2), sub_type 6. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 6}),
    _PlaceableSpec(
        "pickup_weapon_minigun", "Weapon: Minigun", (
            "IOT_Weapon (2), sub_type 7. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 7}),
    _PlaceableSpec(
        "pickup_weapon_marinepistol", "Weapon: Marine Pistol", (
            "IOT_Weapon (2), sub_type 22. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 22}),
    _PlaceableSpec(
        "pickup_weapon_frisbee", "Weapon: Frisbee Launcher", (
            "IOT_Weapon (2), sub_type 23. Only the eight marine weapons "
            "appear in retail - the Predator and Alien start armed, so no "
            "level places a pickup for them"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=2,
        flags={"sub_type": 23}),
    _PlaceableSpec(
        "pickup_ammo_10mm", "Ammo: 10mm Ammo", (
            "IOT_Ammo (3), sub_type 0. 10mm rounds, the commonest ammo retail places"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 0}),
    _PlaceableSpec(
        "pickup_ammo_smartgun", "Ammo: Smartgun Ammo", (
            "IOT_Ammo (3), sub_type 2. Smartgun rounds for the smartgun"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 2}),
    _PlaceableSpec(
        "pickup_ammo_flamethrower", "Ammo: Flamethrower Fuel", (
            "IOT_Ammo (3), sub_type 3. Fuel canisters for the flamethrower"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 3}),
    _PlaceableSpec(
        "pickup_ammo_sadar", "Ammo: SADAR Rockets", (
            "IOT_Ammo (3), sub_type 5. Rockets for the SADAR launcher"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 5}),
    _PlaceableSpec(
        "pickup_ammo_grenade", "Ammo: Grenades", (
            "IOT_Ammo (3), sub_type 6. Grenades for the grenade launcher"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 6}),
    _PlaceableSpec(
        "pickup_ammo_minigun", "Ammo: Minigun Ammo", (
            "IOT_Ammo (3), sub_type 7. Minigun rounds for the minigun"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 7}),
    _PlaceableSpec(
        "pickup_ammo_pulsegrenade", "Ammo: Pulse Grenades", (
            "IOT_Ammo (3), sub_type 8. Pulse grenades for the pulse rifle"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=3,
        flags={"sub_type": 8}),
    _PlaceableSpec(
        "pickup_health", "Medkit", (
            "IOT_Health (4). sub_type is always 0 in retail - the category "
            "holds a single item"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=4),
    _PlaceableSpec(
        "pickup_armour", "Armour", (
            "IOT_Armour (5). sub_type is always 0 in retail"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=5),
    _PlaceableSpec(
        "pickup_fieldcharge", "Energy Orb", (
            "IOT_FieldCharge (15) - the Predator's energy pickup. sub_type is "
            "always 0 in retail"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=15),
    _PlaceableSpec(
        "object_static", "Static", (
            "IOT_Static (0). Scenery the player cannot interact with: tanks, "
            "TVs, grills. 2453 of them in retail, the second largest category. "
            "Pick the model from the panel"),
        "MESH_CUBE", CAT_OBJECT, KIND_OBJECT, pickup_type=0),
    _PlaceableSpec(
        "object_furniture", "Furniture", (
            "IOT_Furniture (1). Crates, drums, seats - scenery that can be "
            "pushed. 922 in retail, mass 40-2000 g"),
        "MESH_CUBE", CAT_OBJECT, KIND_OBJECT, pickup_type=1),
    _PlaceableSpec(
        "object_screen", "Screens", (
            "IOT_ReservedForJohn (9) despite the placeholder-sounding name: "
            "the video screens, models screen1, screen2 and so on. 196 in "
            "retail"),
        "MESH_CUBE", CAT_OBJECT, KIND_OBJECT, pickup_type=9),
    _PlaceableSpec(
        "object_light", "Lights", (
            "Placed light objects, 2846 in retail - the largest category, all "
            "named light*. They need a PLOBJLIT chunk, which is not written "
            "yet, so for now a light here is scenery with no glow"),
        "MESH_CUBE", CAT_OBJECT, KIND_OBJECT, pickup_type=16),
    _PlaceableSpec(
        "placeholder_sound", "Sound", (
            "NOT IMPLEMENTED. A sound emitter - retail writes SOUNDOB2 with an "
            "inner radius at full volume and an outer radius at zero, wired "
            "into the strategy system. Placing one exports nothing yet"),
        "MESH_UVSPHERE", CAT_SOUND, KIND_PLACEHOLDER),
    _PlaceableSpec(
        "placeholder_effect", "Particle Effect", (
            "NOT IMPLEMENTED. One of the game's own particle systems, such as "
            "sparks or smoke, attached to a point in the level. Placing one "
            "exports nothing yet"),
        "MESH_UVSPHERE", CAT_EFFECTS, KIND_PLACEHOLDER),
    _PlaceableSpec(
        "placeholder_morph", "Morph", (
            "NOT IMPLEMENTED. Geometry that moves or changes shape: doors, "
            "lifts, fans, platforms. Twelve retail strategies cover these "
            "(StratDoor 50, StratTrack 72, StratFan 75, StratPlatLift 54 and "
            "others), each with its own chunk layout. Placing one exports "
            "nothing yet"),
        "MESH_CUBE", CAT_MORPH, KIND_PLACEHOLDER),
    _PlaceableSpec(
        "pickup_security", "Security Pass", (
            "IOT_SpecialPickupObject (13). Models bypass and secpass01. Only "
            "two in retail, but a working pickup"),
        "MESH_CUBE", CAT_PICKUP, KIND_PICKUP, pickup_type=13),
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
        if spec.kind in (KIND_PICKUP, KIND_OBJECT):
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
            # 2 m across, so a 4 m cube: a usable slab in a normal room.
            obj.scale = (2.0, 2.0, 2.0)
        else:
            obj = bpy.data.objects.new(spec.label.split("  (")[0], None)
            obj.empty_display_type = spec.display
            obj.empty_display_size = 1.0
        obj[PLACEABLE_PROP] = spec.kind
        if spec.legacy_marker is not None:
            obj[MARKER_PROP] = spec.legacy_marker
        obj.rif_placeable.kind = spec.kind
        obj.rif_placeable.npc_type = spec.npc_type
        if spec.pickup_type is not None:
            obj.rif_placeable.pickup_type = spec.pickup_type
        for key, value in spec.flags.items():
            setattr(obj.rif_placeable, key, value)
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
    entries = [spec for spec in PLACEABLES if spec.category == category]
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
            box.prop(settings, "inactive")
            box.prop(settings, "excluded_hard")

        if kind == KIND_WEIGHTED:
            box = layout.box()
            box.label(text="What should spawn here?", icon="MESH_UVSPHERE")
            box.prop(settings, "mix_drone")
            box.prop(settings, "mix_predalien")
            box.prop(settings, "mix_praetorian")
            box.label(text="Values are relative to each other", icon="INFO")

        if kind == KIND_GLOBAL_GENERATOR:
            box = layout.box()
            box.label(text="Spawning budget", icon="OPTIONS")
            box.prop(settings, "max_npcs")
            box.prop(settings, "npcs_per_minute")
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
            box.prop(settings, "can_crawl")

        if kind in (KIND_PICKUP, KIND_OBJECT):
            # The fields SimpleStrategy carries that nothing exposed until now.
            box = layout.box()
            box.label(text="Placed object", icon="CUBE")
            if kind == KIND_PICKUP:
                box.prop(settings, "sub_type")
            box.prop(settings, "object_mass")
            box.prop(settings, "object_integrity")
            box.prop(settings, "object_heavy")

        if kind == KIND_PATROL_PATH:
            layout.prop(settings, "patrol_closed")


def menu_draw(self, context):
    """Draw the AvP submenu: categories, then the existing helper entries."""
    layout = self.layout
    for category, label in CATEGORY_LABELS:
        layout.menu(CATEGORY_MENUS[category].bl_idname, text=label,
                    icon="COLLECTION_NEW")


CLASSES = (
    RIF_PlaceableSettings,
    RIF_PT_placeable,
    *PLACEABLE_OPERATORS.values(),
    *CATEGORY_MENUS.values(),
)


def register_placeables():
    """Register the placeable objects, their menus and their properties."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.rif_placeable = bpy.props.PointerProperty(
        type=RIF_PlaceableSettings)


def unregister_placeables():
    """Undo everything register_placeables() did."""
    del bpy.types.Object.rif_placeable
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
