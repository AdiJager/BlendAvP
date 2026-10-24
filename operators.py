"""Blender operators and menus for RIF export and AvP marker creation.

Exports a Blender scene as an AvP (1999) .rif file in one of three modes:
    SHAPE       — single active mesh, identity transform
    COLLECTION  — all selected meshes, no environment
    LEVEL       — all selected meshes + REBENVDT level skeleton

Also registers an "AvP" submenu under Object > Add so the user can drop
in game-specific marker objects (currently only Player Start). Markers
are plain Blender Empties tagged with the custom property
`rif_marker_type`; the exporter scans for them in LEVEL mode.

Fix history:

  S6 F6 — H45: Safe Spawn places AVPSTART at the AABB centre (inside
    geometry). AllNewModuleHandler (Module.c) only creates DISPLAYBLOCKs
    for modules reachable from the player's module; spawning outside all
    modules means no geometry renders.

  S6 F6 — H53: Texture Override. chnkload.cpp::copy_to_shapeheader
    degrades any textured polygon whose bitmap_idx does not resolve to
    a loaded image into I_Polyline with iflag_nolight. Filling Texture
    Override with an existing .RIM path forces all polygons to
    reference bitmap_idx 0 → that entry.

  S6 F7 — Prelight via SHPVTINT (all white by default).

  S6 F8 — the S6 F7 diagnosis was wrong: `ShapeFlag_3DS_AxisFlip` (0x01) is
    only *defined* in shape.h and no game source ever reads it.

  S6 F9 — ROOT CAUSE of "empty level with Include Prelight on":
    ShapeFlag_PreLit (shape.h:421) and SHAPE_FLAG_EXTERNALFILE
    (SHPCHUNK.HPP:15) are BOTH 0x00001000. Writing the prelight bit into
    SHPHEAD1.flags made Projload.cpp:1974 classify our level shapes as
    imported/external shapes, which calls copy_to_mainshapelist() with a
    stale object pointer (line 1986) and routes objects to
    deal_with_placed_object() instead of copy_to_module(). No module is
    registered, so AllNewModuleHandler (MODULE.C:123) never creates a
    DISPLAYBLOCK and the level renders empty — while still validating and
    loading without an error message. The exporter now leaves
    SHPHEAD1.flags at 0 and relies on the loader to set ShapeFlag_PreLit
    from SHPVTINT (Projload.cpp:1455/2067).

  S6 F11 — point lights. AvP has no dynamic per-object lighting for static
    geometry: Kshape.c::VertexIntensity_Standard_Opt starts from the vertex
    colour stored in SHPVTINT and adds runtime lights, but Empty_Module_Map
    sets ObFlag3_PreLit (comp_map.c:457) and Avpview.c:170 skips every light
    carrying LFlag_PreLitSource — which is exactly what setup_light_data
    marks STDLIGHT chunks with (Objsetup.cpp:3367). So static geometry has to
    be baked, while the same STDLIGHT chunks are what light the player and
    NPCs, whose display blocks never carry ObFlag3_PreLit.

  S6 F13 — the whole lighting configuration now comes from Blender: World
    Color/Strength for ambient, the light objects themselves for Power and
    Color. The exporter's own light fields are gone, and two real bugs went
    with them:

      * `_build_lightset` used to substitute a fabricated white light at the
        origin when a caller passed no lights. That light not only lit
        characters on its own, it also made every lighting test meaningless.
      * the falloff radius was read from Blender's `shadow_soft_size`, whose
        default is 0.25 m — so a default point light got a 250 mm radius and
        left entire rooms unlit. It is now `Reach` in Object Properties >
        RIF Light, otherwise 25 m.

  S6 F14 — units and discoverability. Blender's Power is now converted with a
    measured relation (see `rif/lighting.py`), Temperature is honoured, and
    the two engine-only settings moved onto the light object as a real panel
    instead of a hidden custom property.

  S6 F17 — textures. The library of game materials is built by
    "Load Game Textures" in the add-on preferences, which unpacks `Tex*.FFL`,
    converts every `.RIM` to a sibling `.png` and creates one material per
    texture carrying its game-side name in `RIM_PATH`. The export reads those
    names to build BMPNAMES, so the whole-level texture override is gone.
"""
from pathlib import Path
import math
import traceback

import pathlib

import bpy
from bpy.props import (
    StringProperty, FloatProperty, IntProperty, EnumProperty, BoolProperty,
)
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
from .rif.builder import build_rif_bytes, build_rif_multi, build_rif_level
from .rif.chunks.environment import (
    ORIENTATION_UNITS_PER_TURN, generator_flags, GENERATE_ALIENS,
    GENERATE_MARINES,
)
from .rif import lighting as rif_lighting
from .rif.types import blender_to_rif_axis, meters_to_rif
from .rif.texture_library import RIM_LIBRARY_PROP, rim_name_for_material

# The engine works in millimetres; Blender works in metres.
MM = 0.001


MARKER_PROP = "rif_marker_type"
PLAYER_START_MARKER = "PLAYER_START"
BITMAP_IDX_PROP = "rif_bitmap_idx"

# RIM path used for a material slot that has no material, and as the fallback
# BMPNAMES entry. A slot with no material still gets an index, and BMPNAMES must
# never be empty, or every textured polygon would degrade to an unlit one.
DUMMY_TEXTURE = "dummy.RIM"

# Light reach and brightness live on the light object itself, not in the export
# dialog: the engine needs an explicit falloff radius and Blender has no field
# for one, so these are per-light settings shown in Object Properties > RIF Light.
RIF_RANGE_PROP = "rif_range"        # float, RIF millimetres (legacy scenes)

# AvP's own level editor shipped lights with ranges around 3.6-13 m; the game's
# largest reference value is 20000 mm, so anything much beyond that is wasted.
DEFAULT_LIGHT_RANGE_MM = 25000

# Ambient written when the scene has no World at all. There is no longer a
# field for it: a World is the supported way to set the ambient, and a manual
# value only ever hid the fact that a scene had no World. This is dim on
# purpose, so an unlit level looks like one and the warning makes sense.
AMBIENT_WITHOUT_WORLD = 32


class RIF_LightSettings(bpy.types.PropertyGroup):
    """Per-light RIF settings, editable in Object Properties > RIF Light.

    Stored on the light object so they travel with the scene file and are
    visible without reading a script. Both fields treat 0 as "not set", which
    is what keeps the exporter's defaults working for untouched lights.
    """

    brightness_scale: FloatProperty(
        name="Brightness Scale",
        description=(
            "Multiplier on this light's Power when it is converted to the "
            "engine's brightness. 0 or 1 = use Power as-is (Blender watts, "
            "with 500 W reading as mid-grey at 5 m).\n"
            "Useful to lift or dim a single lamp without touching its Power, "
            "which also affects Blender's own preview."
        ),
        default=0.0, min=0.0, soft_max=10.0,
    )


def _draw_export_settings(layout, settings):
    """Lay the export settings out in sections.

    Module-level and taking `settings` rather than being a method, because a
    Blender operator cannot be instantiated from Python — `RIF_OT_export()`
    raises "bpy_struct.__new__(struct): expected a single argument" — so a
    method could not be exercised by a test at all. This can.

    Without sections Blender lists every property in declaration order with no
    grouping, so an advanced override sits next to the mode selector. The
    sections also hide what a mode does not use: `Level Name` is written only in
    Level mode, so showing it in the others invites filling in a dead field.
    """
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.label(text="Level", icon="SCENE_DATA")
    box = layout.box()
    # The mode first: it decides what the rest of the panel can offer, so it
    # should be the thing the eye lands on.
    box.prop(settings, "export_mode")
    box.prop(settings, "export_selected_only")
    if settings.export_mode == "LEVEL":
        box.prop(settings, "link_mode")
        if settings.link_mode == "LIST":
            box.label(text="Fill the Links list on each module",
                      icon="INFO")
        elif settings.link_mode == "AUTO":
            box.label(text="Modules link to whatever they touch",
                      icon="INFO")
        box.prop(settings, "use_custom_name")
        if settings.use_custom_name:
            box.prop(settings, "level_name")
        else:
            scene = bpy.context.scene
            box.label(text=f"Named after the scene: {scene.name}",
                      icon="INFO")
    else:
        note = box.row()
        note.label(text="No environment is written in this mode",
                   icon="INFO")

    layout.separator()
    layout.label(text="Advanced", icon="PREFERENCES")
    box = layout.box()
    box.prop(settings, "global_scale")
    box.prop(settings, "override_shape_flags")
    # The override only matters when enabled, so grey it out rather than hiding
    # it: a field that disappears looks like a bug.
    flag_row = box.row()
    flag_row.enabled = settings.override_shape_flags
    flag_row.prop(settings, "shape_flags_override")


class RIF_OT_export(bpy.types.Operator, ExportHelper):
    """Export selected meshes as a REBINFF2 .rif file."""

    bl_idname = "export_scene.rif"
    bl_label = "Export RIF"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".rif"
    filter_glob: StringProperty(default="*.rif", options={"HIDDEN"})

    export_mode: EnumProperty(
        name="Mode",
        items=[
            ("SHAPE", "Single Shape",
             "Active mesh only, identity transform"),
            ("COLLECTION", "Multi-Object",
             "All selected mesh objects, no environment"),
            ("LEVEL", "Level Skeleton",
             "Selected meshes + REBENVDT environment skeleton"),
        ],
        default="LEVEL",
    )

    link_mode: EnumProperty(
        name="Link Mode",
        description=(
            "Where the module tree comes from. The tree is written as OBJCHIER "
            "and also feeds the visibility graph"),
        items=(
            ("OUTLINER", "By Outliner",
             "A module is linked to its parent and children, as arranged in "
             "the Outliner. The original behaviour"),
            ("AUTO", "Auto",
             "Link each module to the nearest one it touches, worked out from "
             "the bounding boxes. Needs no parenting and no list"),
            ("LIST", "By List",
             "Link each module to the modules named on its own panel. Use "
             "when the shape of the tree matters"),
        ),
        default="OUTLINER",
    )

    use_custom_name: BoolProperty(
        name="Custom Name",
        description=(
            "Use a name other than the scene. Off by default: a scene is what "
            "a level is, so the name follows it and one .blend can hold "
            "several levels without renaming anything"),
        default=False,
    )

    level_name: StringProperty(
        name="Level Name",
        description=(
            "Value written into RIFFNAME. The engine shows this name in its "
            "level list, so it is worth matching the exported file"
        ),
        default="custom",
    )

    # Textures come from the materials, not from a setting here. Every material
    # carries the game-side RIM path in its `RIM_PATH` property, filled in by
    # "Load Game Textures"; the exporter collects those paths to build BMPNAMES.
    # A single whole-level override used to live here, which could only ever
    # give every wall the same texture.
    #
    # The player start has no fields either. It comes from a Player Start object
    # placed through `Add > AvP`, and the exporter warns when there is none.
    # Three settings used to describe the same position — an AABB centre toggle,
    # a fallback position and X/Y/Z fields — and the AABB toggle was on by
    # default, so typing coordinates did nothing at all.

    # -------------------------------------------------------- prelight

    shape_flags_override: IntProperty(
        name="ShapeFlag_PreLit Override",
        description=(
            "Advanced / testing only. When 'Use Flags Override' is enabled, "
            "this exact value replaces the shape flags on every SHPHEAD1 — "
            "including 0. WARNING: 0x00001000 is both ShapeFlag_PreLit and "
            "SHAPE_FLAG_EXTERNALFILE, so setting it on a level shape makes "
            "the loader skip module registration and the level renders "
            "empty. Leave the override OFF for normal exports; the builder "
            "writes 0 and prelight is activated from SHPVTINT instead."
        ),
        default=0, min=0,
    )

    override_shape_flags: BoolProperty(
        name="Use Flags Override",
        description=(
            "Enable shape_flags_override. Off = the builder writes 0 "
            "(prelight is activated by the SHPVTINT chunk, not by a flag)."
        ),
        default=False,
    )

    export_selected_only: BoolProperty(
        name="Export Selected Only",
        description=(
            "Export just the selected meshes instead of the whole scene.\n"
            "Off by default: a level is the scene, and a stray selection "
            "silently dropping rooms from the export is a mistake that is "
            "invisible until the level is loaded in game"
        ),
        default=False,
    )

    # ------------------------------------------------ environment settings

    global_scale: FloatProperty(
        name="Global Scale",
        description=(
            "Multiplies every position in the level when the game loads it: "
            "the loader does `local_scale = GlobalScale * ENVSDSCL.scale` "
            "(Projload.cpp:336), so this scales the whole map — geometry "
            "placement, generators, waypoints, lights.\n"
            "1.0 means unscaled and writes no ENVSDSCL chunk at all, which is "
            "what 58 of the 63 retail levels do. Retail uses 0.8 and 0.9 in "
            "the five that do scale. Useful for fitting a level built at the "
            "wrong size onto the game's expectations without rebuilding it"
        ),
        default=1.0, min=0.05, max=20.0, precision=3,
    )

    # -------------------------------------------------------- execution

    def draw(self, context):
        _draw_export_settings(self.layout, self)

    def execute(self, context):
        """Run export based on export_mode; write result to self.filepath."""
        try:
            if self.export_mode == "SHAPE":
                data = self._export_shape(context)
            elif self.export_mode == "COLLECTION":
                data = self._export_collection(context)
            else:
                data = self._export_level(context)
            if data is None:
                return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001 - reported to the artist
            # A one-line message is not enough to act on: the previous wording
            # ("index 0 out of range, size 0") named neither the object nor the
            # line. Log the traceback and the selection so a failure can be
            # diagnosed from the console instead of by guessing.
            traceback.print_exc()
            for obj in getattr(context, "selected_objects", []) or []:
                print(f"[RIF] selected: {obj.name} type={obj.type} "
                      f"slots={len(getattr(obj.data, 'materials', []) or [])}")
            _report(self, {"ERROR"},
                        f"Export failed: {type(exc).__name__}: {exc} "
                        "(full traceback in the console)")
            return {"CANCELLED"}

        with open(self.filepath, "wb") as handle:
            handle.write(data)
        _report(self, {"INFO"},
                    f"Wrote {len(data)} bytes to {self.filepath} "
                    f"({self.export_mode})")
        return {"FINISHED"}

    def _export_shape(self, context):
        """Single-mesh export; returns bytes or None on validation failure."""
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            _report(self, {"ERROR"}, "Active object must be a mesh")
            return None
        return build_rif_bytes(
            obj.data,
            object_name=obj.name,
            include_prelight=True,
        )

    def _export_collection(self, context):
        """Multi-object export without environment."""
        objs = _gather_meshes(context, self.export_selected_only)
        if not objs:
            _report(self, {"ERROR"}, "No mesh objects to export")
            return None
        return build_rif_multi(
            objs,
            include_prelight=True,
        )

    def _export_level(self, context):
        """Level export: multi-object + REBENVDT skeleton."""
        objs = _gather_meshes(context, self.export_selected_only)
        if not objs:
            _report(self, {"ERROR"}, "No mesh objects to export")
            return None

        player_start = _resolve_player_start(context)

        # Every material slot gets one texture index, and BMPNAMES is built from
        # the same list, so the polygon indices and the chunk can never disagree.
        placed_specs = _collect_placed_objects(context, objs)
        tex_paths, bitmap_mapping = _build_bitmap_mapping(
            objs, [spec["mesh"] for spec in placed_specs])

        hierarchy = _build_hierarchy(objs, self.link_mode)
        visibility = _visibility_links(objs, self.link_mode)
        _report_layout(self, context, objs, hierarchy, visibility)

        placed_objs = [obj for obj in context.scene.objects
                       if any(obj.data is spec["mesh"] for spec in placed_specs)]
        originals = _apply_bitmap_indices(objs + placed_objs, bitmap_mapping)

        # The manual slider packs to 0x00RRGGBB (bits 0..7 blue, 8..15 green,
        flags_override = _resolve_shape_flags_override(self)

        # ---- lighting: World gives the ambient, light objects give the lights
        lights = []
        object_intensities = None
        world_colour, world_strength = _world_color_strength(context)
        world = _scene_world(context)
        if world is not None:
            prelight_colour = rif_lighting.world_ambient_colour(
                world_colour, world_strength)
            ambient_level = rif_lighting.world_ambient_level(
                world_colour, world_strength)
        else:
            # No World assigned. There is no field for a manual value any more,
            # so the level gets a dim constant and the user is told why: with no
            # ambient every surface that no lamp reaches renders black, which
            # reads as a broken export rather than a missing World.
            ambient_level = AMBIENT_WITHOUT_WORLD
            prelight_colour = ((ambient_level << 16)
                               | (ambient_level << 8) | ambient_level)
            _report(self, 
                {"WARNING"},
                "No World in the scene, so the level has no ambient light "
                f"(using {AMBIENT_WITHOUT_WORLD}/255). Add a World with "
                "Color and Strength, or every unlit surface renders black")

        lights, skipped, dark = _collect_scene_lights(
            context, DEFAULT_LIGHT_RANGE_MM / 1000.0)
        if skipped:
            _report(self, {"WARNING"},
                        "Skipped unsupported light types: "
                        + ", ".join(sorted(skipped)))
        if dark:
            _report(self, {"WARNING"},
                        f"{dark} light(s) had zero Power and were ignored")
        if not lights:
            _report(self, {"INFO"},
                        "No point/spot lights found; World ambient only")

        # Prelight is always baked. It used to be a toggle, but the "off" state
        # only ever produced a black level: with no prelight and no placed-light
        # object system (bh_light.c), every vertex renders black.
        # Placed objects are lit too: without their share the pickups and
        # levers come out with no SHPVTINT and the game draws them unlit.
        object_intensities = _bake_object_lights(
            objs + placed_objs, lights, prelight_colour)
        # The builder indexes `objects` alone, so the placed objects' share is
        # handed over by name rather than by position.
        by_name = {obj.name: object_intensities[index]
                   for index, obj in enumerate(objs + placed_objs)
                   if index < len(object_intensities)}
        for spec in placed_specs:
            spec["prelight"] = by_name.get(spec["name"])

        _report_bake_quality(self, object_intensities, prelight_colour)

        try:
            spawns, skipped = _collect_spawns(context, objs)
            _report_spawns(self, spawns, skipped)
            _report_player_start(self, player_start, spawns)
            (waypoint_specs, orphaned_waypoints,
             rotated_waypoints) = _collect_waypoints(context, objs)
            if orphaned_waypoints:
                _report(self,
                    {"WARNING"},
                    "Waypoint(s) outside every module, so they belong to no AI "
                    "module and will be ignored by the game: "
                    + ", ".join(orphaned_waypoints[:4]))
            if rotated_waypoints:
                _report(self,
                    {"WARNING"},
                    "Waypoint(s) are rotated, and the game's waypoint is an "
                    "axis-aligned box with no rotation. The exported box uses "
                    "the object's scale and ignores the rotation, so it will "
                    "not match the rotated cube on screen: "
                    + ", ".join(rotated_waypoints[:4]))
            return build_rif_level(
                objs,
                (self.level_name if self.use_custom_name
                 else context.scene.name),
                texture_paths=tex_paths,
                player_start=player_start,
                hierarchy=hierarchy,
                visibility=visibility,
                # 1.0 means "unscaled", which is the absence of the chunk.
                global_scale=(self.global_scale
                              if abs(self.global_scale - 1.0) > 1e-9 else None),
                ambient=ambient_level,
                # LITSCALE is never written: the loader does not read it and
                # none of the 63 retail levels contain the chunk at all.
                include_litscale=False,
                include_prelight=True,
                prelight_colour=prelight_colour,
                object_intensities=object_intensities,
                lights=lights,
                emit_runtime_lights=True,
                shape_flags_override=flags_override,
                spawns=spawns,
                global_generator=_collect_global_generator(context),
                waypoints=waypoint_specs,
                placed_objects=placed_specs,
                sounds=_collect_sounds(context),
                sound_directory=_sound_directory(context),
                paths=_collect_paths(context, objs)[0],
                envir=_envir_settings(context),
                death_volumes=_collect_death_volumes(
                    context, objs)[0],
                mission_objective=_collect_mission_objective(
                    context, objs)[0],
                area_switches=_collect_area_switches(context, objs)[0],
                multi_switches=_collect_multi_switches(context, objs)[0],
                self_destructs=_collect_self_destructs(context, objs)[0],
                messages=_collect_messages(context, objs)[0],
                particles=_collect_particles(context, objs)[0],
            )
        finally:
            _restore_bitmap_indices(originals)


def _material_texture_path(mat):
    """Return the game-side RIM name for a material slot.

    Thin wrapper over `texture_library.rim_name_for_material`, which is the one
    place that decides this name. It must be the same function the chunk builder
    uses: the builder looks this name up in the BMPNAMES list produced here, so
    any difference in normalisation would silently repoint a polygon at another
    texture.
    """
    return rim_name_for_material(mat, DUMMY_TEXTURE)


def _build_bitmap_mapping(objs, extra_meshes=()):
    """Return ``(paths, mapping)`` linking materials to BMPNAMES indices.

    ``paths`` is the BMPNAMES name list in index order; ``mapping`` maps each
    material (and ``None`` for an empty slot) to the index of its name.

    This is the fix for a real bug: the polygon texture index used to default to
    the material's slot number *on its own object*, while BMPNAMES is numbered
    globally across the level. With two objects the second one therefore
    pointed at the first one's texture. Deriving both from one list keeps them
    consistent by construction.
    """
    # Placed objects are not in `objs`: `_gather_meshes` drops them so they stay
    # out of the collision system. Their materials still need an index, or their
    # polygons keep index zero and draw the first module's texture.
    meshes = [obj.data for obj in objs] + list(extra_meshes)

    paths = []
    index_of = {}
    for mesh in meshes:
        for mat in mesh.materials:
            path = _material_texture_path(mat)
            if path not in index_of:
                index_of[path] = len(paths)
                paths.append(path)

    mapping = {}
    for mesh in meshes:
        for mat in mesh.materials:
            mapping[mat] = index_of[_material_texture_path(mat)]
    return paths, mapping


def _apply_bitmap_indices(objs, mapping):
    """Write each material's RIF texture index; return the previous values.

    The values are stored on the materials so the builder's existing
    `_resolve_bitmap_indices` picks them up, and the caller restores them in a
    `finally` block. That mirrors how the texture override already worked, and
    it means an artist's own `rif_bitmap_idx` is never permanently overwritten.
    """
    originals = []
    for obj in objs:
        for mat in obj.data.materials:
            if mat is None:
                continue
            originals.append((mat, mat.get(BITMAP_IDX_PROP)))
            mat[BITMAP_IDX_PROP] = mapping.get(mat, 0)
    return originals


def _restore_bitmap_indices(originals):
    """Put back the `rif_bitmap_idx` values recorded by the apply step."""
    for mat, original in originals:
        if original is None:
            if BITMAP_IDX_PROP in mat:
                del mat[BITMAP_IDX_PROP]
        else:
            mat[BITMAP_IDX_PROP] = original


class RIF_AddonPreferences(bpy.types.AddonPreferences):
    """Where the game lives, plus the texture library controls."""

    bl_idname = __package__

    game_directory: StringProperty(
        name="Game Directory",
        description=(
            "Folder containing the game, e.g. the one holding 'fastfile' and "
            "'avp_rifs'. The texture archives Tex1.FFL..Tex58.FFL are read "
            "from its 'fastfile' subfolder and unpacked into "
            "'fastfile\\_ffl_unpacked'"
        ),
        subtype="DIR_PATH",
        default="",
    )

    unpack_mode: EnumProperty(
        name="FFL's Unpack Directory",
        description=(
            "Where the texture archives are unpacked to, and where the texture "
            "import reads them back from. One setting, so the two cannot "
            "disagree"),
        items=(
            ("FFL", "FFL Dir",
             "fastfile/_ffl_unpacked inside the game folder. What the add-on "
             "did before, and the default"),
            ("GAME", "Game Dir",
             "The game folder itself, one level up from fastfile"),
            ("CUSTOM", "Custom Dir",
             "A folder of your own. Use this when the game folder is read-only "
             "or shared"),
        ),
        default="FFL",
    )

    custom_unpack_directory: StringProperty(
        name="Custom Directory",
        description=(
            "Where to put the unpacked textures. The archives are unpacked "
            "into an _ffl_unpacked subfolder here"),
        subtype="DIR_PATH",
        default="",
    )

    keep_packed: BoolProperty(
        name="Pack Images Into .blend",
        description=(
            "Store the game textures inside the .blend file. On: the file is "
            "self-contained but larger. Off: the .blend references the PNGs "
            "under the game directory, so it only opens on a machine that has "
            "them"
        ),
        default=False,
    )

    def draw(self, context):
        """Draw the game directory, the load button and the library status."""
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, "game_directory")
        layout.prop(self, "unpack_mode")
        if self.unpack_mode == "CUSTOM":
            layout.prop(self, "custom_unpack_directory")
            if not self.custom_unpack_directory.strip():
                layout.label(text="Pick a folder, or the game's default is used",
                             icon="INFO")
        layout.prop(self, "keep_packed")



        row = layout.row()
        row.operator(RIF_OT_load_game_textures.bl_idname, icon="FILE_FOLDER")
        row.operator(RIF_OT_load_shapes.bl_idname, icon="MESH_MONKEY")

        if not self.game_directory:
            layout.label(text="Point this at the game folder first",
                         icon="INFO")

        row = layout.row()
        row.operator(RIF_OT_build_texture_gallery.bl_idname, icon="IMAGE_REFERENCE")
        row.operator(RIF_OT_clear_texture_library.bl_idname, icon="TRASH")

        library = [m for m in bpy.data.materials if m.get(RIM_LIBRARY_PROP)]
        layout.label(text=f"Materials in library: {len(library)}",
                     icon="MATERIAL")


class RIF_OT_load_game_textures(bpy.types.Operator):
    """Unpack the game's texture archives and build the material library."""

    bl_idname = "object.rif_load_game_textures"
    bl_label = "Load Game Textures"
    bl_description = (
        "Unpack the game's texture archives into a material library.\n"
        "\n"
        "Takes several minutes on a first run: 1028 textures in 58 archives, "
        "each one unpacked, converted and turned into a material. The progress "
        "bar moves in fits because each archive is a single step.\n"
        "\n"
        "Let it finish. Stopping part-way leaves a partial library, and Clear "
        "Texture Library then starts you clean."
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        """Run the whole pipeline: unpack, convert, import, report."""
        from .rif import fastfile, texture_import, texture_tools

        preferences = context.preferences.addons[__package__].preferences
        game_directory = preferences.game_directory.strip()
        if not game_directory:
            _report(self, {"ERROR"},
                        "Set Game Directory in the add-on preferences first")
            return {"CANCELLED"}

        game_path = Path(game_directory)
        if not (game_path / "fastfile").is_dir():
            _report(self, {"ERROR"},
                        f"No 'fastfile' folder in {game_path}")
            return {"CANCELLED"}

        # Unpacking and converting are plain Python and report through the
        # window manager, which is the only progress channel available without
        # a modal operator.
        window_manager = context.window_manager

        def progress(done, total, name):
            window_manager.progress_update(done * 100 // max(1, total))

        try:
            window_manager.progress_begin(0, 100)
            extraction, conversion = texture_tools.load_game_textures(
                game_path, progress=progress,
                mode=preferences.unpack_mode,
                custom_dir=preferences.custom_unpack_directory)
        except fastfile.FastFileError as exc:
            window_manager.progress_end()
            _report(self, {"ERROR"}, f"Texture archives: {exc}")
            return {"CANCELLED"}
        except OSError as exc:
            window_manager.progress_end()
            _report(self, {"ERROR"}, f"Cannot write unpacked textures: {exc}")
            return {"CANCELLED"}

        report, materials = texture_import.import_texture_library(
            texture_tools.unpacked_root_for(
                game_path, preferences.unpack_mode,
                preferences.custom_unpack_directory),
            progress=progress)
        window_manager.progress_end()

        if preferences.keep_packed:
            for image in bpy.data.images:
                if image.source == "FILE" and not image.packed_file:
                    try:
                        image.pack()
                    except RuntimeError:
                        pass

        _report(self, {"INFO"},
                    f"Unpacked {extraction.written} file(s), converted "
                    f"{conversion.converted} texture(s) to PNG, built "
                    f"{len(materials)} material(s)")
        if conversion.failed:
            # Reported, not fatal: one unusual texture must not stop the rest.
            names = ", ".join(p.name for p, _why in conversion.failed[:3])
            _report(self, {"WARNING"},
                        f"{len(conversion.failed)} texture(s) skipped: {names}")
        if extraction.duplicates:
            _report(self, {"WARNING"},
                        f"{len(extraction.duplicates)} duplicate name(s) "
                        "between archives")
        return {"FINISHED"}


def _apply_shape_smoothing(mesh, shape):
    """Smooth the faces whose vertices disagree about which way is out.

    SHPVNORM carries a normal per vertex, and a face whose corners all share
    one normal is a flat surface; where they differ, the surface is meant to be
    curved. Without this every shape is faceted, which is what makes a chair look
    carved from blocks instead of moulded.
    """
    normals = shape.vertex_normals
    if not normals:
        return
    for polygon in mesh.polygons:
        shared = [normals[i] for i in polygon.vertices if i < len(normals)]
        if len(shared) < 3:
            continue
        first = shared[0]
        # Same direction to within a degree: the face is flat and stays flat.
        smooth = any(first[0] * other[0] + first[1] * other[1]
                     + first[2] * other[2] < 0.9998 for other in shared[1:])
        polygon.use_smooth = smooth
    mesh.update()


def _fix_shape_winding(mesh, shape):
    """Turn each face so its normal agrees with the one the engine stored.

    SHPPNORM holds a real normal per polygon, and the engine's winding does not
    have to match Blender's -- it does not, and the result is meshes that are lit
    from behind and look unshaded. Reading the stored normal is cheaper and more
    reliable than guessing a global rule, because a shape can mix both.
    """
    for face_index, wanted in enumerate(shape.polygon_normals):
        if wanted is None or face_index >= len(mesh.polygons):
            continue
        polygon = mesh.polygons[face_index]
        current = polygon.normal
        if (current.x * wanted[0] + current.y * wanted[1]
                + current.z * wanted[2]) < 0.0:
            polygon.flip()
    mesh.update()


def _assign_shape_materials(mesh, shape, stem, number):
    """Give the mesh its materials; the reader owns the rule."""
    shape_reader.assign_materials(mesh, shape, stem, number)


class RIF_OT_load_shapes(bpy.types.Operator):
    """Import the game's model library into a scene of its own.

    The game keeps its models in shape_rifs, one file per model. They go into
    a scene named after the folder so they never mix with a level: a level is
    what you export, these are what you place in one.
    """

    bl_idname = "object.rif_load_shapes"
    bl_label = "Load Shapes"
    bl_description = (
        "Import every model from the game's shape_rifs folder into a scene of "
        "its own")

    def execute(self, context):
        """Import the shape library, one scene per call."""
        from .rif import shape_reader

        preferences = context.preferences.addons[__package__].preferences
        game_directory = preferences.game_directory.strip()
        if not game_directory:
            _report(self, {"ERROR"},
                    "Set Game Directory in the add-on preferences first")
            return {"CANCELLED"}

        folder = Path(game_directory) / "shape_rifs"
        if not folder.is_dir():
            _report(self, {"WARNING"},
                    f"No shape_rifs folder in {Path(game_directory)}")
            return {"CANCELLED"}

        files = sorted(set(folder.glob("*.rif")) | set(folder.glob("*.RIF")))
        if not files:
            _report(self, {"WARNING"}, f"No .rif files in {folder}")
            return {"CANCELLED"}

        scene_name = "shape_rifs"
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            scene = bpy.data.scenes.new(scene_name)
        scene["rif_shape_import"] = True

        window_manager = context.window_manager
        window_manager.progress_begin(0, len(files))
        imported = 0
        skipped = []
        try:
            for index, path in enumerate(files):
                window_manager.progress_update(index)
                try:
                    shapes = shape_reader.read_shapes(path)
                except (OSError, ValueError, struct.error):
                    skipped.append(path.name)
                    continue
                for number, shape in enumerate(shapes):
                    # One call builds geometry, winding, smoothing, UVs and
                    # materials together: doing only some of it is what made
                    # pickups arrive flat while this path looked right.
                    mesh = shape_reader.build_mesh(
                        shape, f"{path.stem}_{number}",
                        stem=path.stem, number=number)
                    # Zero-fill rather than skip: a shape whose UV list is one
                    # short is still mostly right, and dropping all of it makes
                    # the whole model use one texel.
                    if shape.uvs:
                        layer = mesh.uv_layers.new(name="UVMap")
                        for face_index, corners in enumerate(shape.uvs):
                            if face_index >= len(faces):
                                break
                            for corner_index, (u, v) in enumerate(corners):
                                if corner_index >= len(faces[face_index]):
                                    break
                                loop_index = mesh.polygons[face_index].loop_start \
                                    + corner_index
                                if loop_index < len(mesh.loops):
                                    layer.data[loop_index].uv = (u, v)

                    obj = bpy.data.objects.new(mesh.name, mesh)
                    scene.collection.objects.link(obj)
                    imported += 1
        finally:
            window_manager.progress_end()

        message = (f"Imported {imported} shape(s) from {len(files)} file(s) "
                   f"into '{scene_name}'")
        if skipped:
            message += f"; skipped {len(skipped)}: " + ", ".join(skipped[:3])
        _report(self, {"INFO"}, message)
        return {"FINISHED"}


class RIF_OT_build_texture_gallery(bpy.types.Operator):
    """Create a scene of planes showing every game material."""

    bl_idname = "object.rif_build_texture_gallery"
    bl_label = "Build Texture Gallery"
    bl_description = (
        "Lay every imported texture out in a scene, for finding one by eye.\n"
        "\n"
        "Blender will stutter while scrolling: each material has to be compiled "
        "before it can be drawn, and that happens the first time a texture is "
        "looked at. It settles on a second pass - nothing is wrong."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Make the gallery scene from the imported library."""
        from .rif import texture_import

        materials = [m for m in bpy.data.materials if m.get(RIM_LIBRARY_PROP)]
        if not materials:
            _report(self, {"WARNING"},
                        "No game materials yet - run Load Game Textures first")
            return {"CANCELLED"}

        existing = bpy.data.scenes.get(texture_import.GALLERY_SCENE)
        if existing is not None:
            bpy.data.scenes.remove(existing)

        scene = texture_import.build_gallery_scene(materials)
        context.window.scene = scene
        _report(self, {"INFO"},
                    f"Gallery with {len(materials)} plane(s) in scene "
                    f"'{scene.name}'; press 0 for the camera view")
        return {"FINISHED"}


class RIF_OT_clear_texture_library(bpy.types.Operator):
    """Remove every material the texture import created."""

    bl_idname = "object.rif_clear_texture_library"
    bl_label = "Clear Texture Library"
    bl_description = (
        "Remove the imported materials, to start the import over. Use this "
        "after a run that was interrupted part-way, which leaves a partial "
        "library behind"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Delete the library materials and any images left unused."""
        from .rif import texture_import

        count = len([m for m in bpy.data.materials
                     if m.get(RIM_LIBRARY_PROP)])
        if not count:
            _report(self, {"INFO"}, "Texture library is already empty")
            return {"CANCELLED"}
        materials, images = texture_import.delete_library()
        _report(self, {"INFO"},
                    f"Removed {materials} material(s) and {images} image(s)")
        return {"FINISHED"}


def _object_yaw_to_rif(obj):
    """Return the object's yaw as AVPGENER orientation units.

    An `AVPGENER` stores only a rotation about its Y axis, 4096 units per full
    turn (`Objsetup.cpp:3229`), and zeroes the other two. Blender's Z axis maps
    to RIF Y, so the object's Z rotation is the one that matters. The result is
    masked to 12 bits exactly as the engine does on load.
    """
    yaw = float(getattr(obj.rotation_euler, "z", 0.0))
    units = int(round(yaw / (2.0 * math.pi) * ORIENTATION_UNITS_PER_TURN))
    return units & (ORIENTATION_UNITS_PER_TURN - 1)


def _module_index_for(obj, objs):
    """Return the index of the module containing `obj`'s origin, or None.

    The index is the object's position in `objs`, which is the order the builder
    writes them and therefore the number the engine's `get_object_by_index`
    expects. A path start is looked up that way, so it has to be that number.
    """
    origin = obj.matrix_world.translation
    for index, candidate in enumerate(objs):
        box = _world_box(candidate)
        if box is None:
            continue
        low, high = box
        if all(low[axis] <= origin[axis] <= high[axis] for axis in range(3)):
            return index
    return None


def _collect_spawns(context, objs=None):
    """Return (generator_specs, skipped) gathered from the scene.

    Handles the generator kinds whose chunk is a plain `AVPGENER`:

      * `PLAYER_START_MP`  type 0 with MULTIPLAYERSTART, one per player slot
      * `KIND_NPC`         a fixed creature, types 1..9, flags 0x00003801
      * `DEFAULT_ENEMY`    type 0, the level's default creature from GLOGENDC
      * `WEIGHTED`         type 0 with ADVANCEDGENERATOR, weights to follow

    The fixed-NPC form needs no weight table: retail writes it as a bare
    `AVPGENER` with flags `0x00003801` (614 of 1064 use exactly that), so an
    Alien Drone generator works with nothing but this chunk and GLOGENDC.

    The two type-0 forms are written with ADVANCEDGENERATOR because that flag
    is what makes the engine look for their extended settings at all
    (`AVPCHUNK.CPP:201`). Their `GENEXSET` weight table is the next piece of
    work; until it exists the engine finds no settings and falls back.

    Everything else is reported as `skipped` rather than dropped in silence:
    placing a waypoint and exporting without a word looked like success.

    `AVPSTART` is deliberately untouched: 20 of the 28 retail levels with
    multiplayer spawns have no single-player start at all.
    """
    from . import placeables
    # The patrol route's list uses a sentinel for "nothing chosen", because
    # Blender discards an enum row whose identifier is empty. It has to be
    # translated back here or the sentinel reads as a route that does not exist.
    from .sounds import NONE_CHOICE

    specs = []
    skipped = []
    # The scene, not `bpy.data.objects`: the datablock list also holds
    # objects the artist unlinked or deleted, and exporting those puts
    # generators in the level that are nowhere in the scene.
    for obj in context.scene.objects:
        kind = obj.get(placeables.PLACEABLE_PROP)
        if kind is None:
            continue
        settings = obj.rif_placeable
        loc = obj.matrix_world.translation
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        location = (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))

        if kind == placeables.KIND_PLAYER_START_MP:
            races = [race for race, on in (("marine", settings.race_marine),
                                           ("alien", settings.race_alien),
                                           ("predator",
                                            settings.race_predator)) if on]
            if not races:
                # No race could use this spawn, so it could never place anyone.
                skipped.append((obj.name, "no race allowed"))
                continue
            flags = generator_flags(races, multiplayer_start=True,
                                    inactive=settings.inactive)
            npc_type = 0
        elif kind == placeables.KIND_NPC:
            flags = generator_flags(inactive=settings.inactive)
            npc_type = settings.npc_type
        elif kind in (placeables.KIND_DEFAULT_ENEMY,
                      placeables.KIND_WEIGHTED):
            flags = generator_flags(advanced=True, inactive=settings.inactive)
            npc_type = 0
        elif kind == placeables.KIND_GLOBAL_GENERATOR:
            # Not a generator itself: it is the marker that asks for a
            # GLOGENDC, which `_collect_global_generator` handles. Counting it
            # as skipped would report a working feature as unsupported.
            continue
        elif kind in (placeables.KIND_PLAYER_START_SP,
                      placeables.KIND_WAYPOINT,
                      placeables.KIND_PICKUP,
                      placeables.KIND_OBJECT,
                      placeables.KIND_SOUND,
                      placeables.KIND_PATROL_PATH,
                      placeables.KIND_DEATH_VOLUME,
                      placeables.KIND_MISSION_OBJECTIVE,
                      placeables.KIND_AREA_SWITCH,
                      placeables.KIND_MULTI_SWITCH,
                      placeables.KIND_SELF_DESTRUCT,
                      placeables.KIND_MESSAGE,
                      placeables.KIND_PARTICLE_GENERATOR):
            # Handled elsewhere, so not skips: a single-player start
            # becomes AVPSTART, a waypoint goes into its module WAYPOINT
            # chunk via _collect_waypoints, a patrol route into its own
            # AVPPATH2, and a kill volume into a VIOBJECT. Reporting any of
            # them as unsupported would be a lie, and this message has to
            # mean what it says.
            #
            # The patrol route was missing from this list when it shipped, so
            # the export warned about a feature that was working. The same
            # omission then hit the kill volume -- which is why the list is
            # worth reading whenever a new kind is added.
            continue
        else:
            skipped.append((obj.name, kind))
            continue

        spec = {
            "location": location,
            "orientation": _object_yaw_to_rif(obj),
            "type": npc_type,
            "flags": flags,
            "name": obj.name,
            "sub_type": settings.sub_type if kind == placeables.KIND_NPC
            else 0,
            "texture_id": settings.texture_id,
        }
        mission = int(settings.mission or 0)
        if mission:
            spec["mission"] = mission
        if mission == 5:
            route = settings.patrol_path
            if route == NONE_CHOICE:
                route = ""
            routes = sorted(obj.name for obj in context.scene.objects
                            if obj.get(placeables.PLACEABLE_PROP)
                            == placeables.KIND_PATROL_PATH)
            if not route:
                skipped.append((obj.name,
                                "patrol with no route chosen"))
            elif route not in routes:
                skipped.append((obj.name,
                                f"route {route!r} is not in the scene"))
            else:
                spec["path_id"] = routes.index(route)
                # Which step the creature starts at is decided by looking a
                # module up ON THE ROUTE (`Projload.cpp:279`), and a module the
                # route does not contain makes the engine assert while the
                # creature starts somewhere unrelated -- which is what a
                # creature picking a point at random looks like from outside.
                # The route's own first point is on the route by construction,
                # so that lookup cannot fail. The generator's module was
                # written here before, and it is the wrong question: the
                # creature is placed on the route, not in the room the
                # generator happens to sit in.
                route_obj = next((o for o in context.scene.objects
                                  if o.name == route), None)
                first = None
                if route_obj is not None:
                    route_points = _curve_points(route_obj)
                    if route_points:
                        bounds = [(o.name, _world_box(o)) for o in objs]
                        first = _module_index_at(objs, bounds,
                                                 route_points[0])
                if first is None:
                    skipped.append((
                        obj.name,
                        f"route {route!r} has no point inside any module, so "
                        f"there is no step to start the creature at"))
                else:
                    spec["start_object_index"] = first

        if kind in (placeables.KIND_DEFAULT_ENEMY,
                    placeables.KIND_WEIGHTED):
            # The weight table is written even when every value is zero, which
            # is how the default-enemy fallback is reached: with no weights the
            # engine uses the creature named by GLOGENDC, but only if it can
            # find the extended settings at all.
            # All fifteen, not the three a skirmish needs. `Objsetup.cpp:3085`
            # copies the whole table without a network check -- the gate at
            # `:2993` is about generator types, not weights -- so the other
            # twelve are what a single-player level fields marines with.
            spec["weights"] = {
                "Alien_Wt": settings.mix_drone,
                "PredAlien_Wt": settings.mix_predalien,
                "Praetorian_Wt": settings.mix_praetorian,
                "PulseMarine_Wt": settings.mix_pulse_marine,
                "FlameMarine_Wt": settings.mix_flame_marine,
                "SmartMarine_Wt": settings.mix_smart_marine,
                "SadarMarine_Wt": settings.mix_sadar_marine,
                "GrenadeMarine_Wt": settings.mix_grenade_marine,
                "MinigunMarine_Wt": settings.mix_minigun_marine,
                "PistolMarine_Wt": settings.mix_pistol_marine,
                "ShotgunCiv_Wt": settings.mix_shotgun_civilian,
                "PistolCiv_Wt": settings.mix_pistol_civilian,
                "FlameCiv_Wt": settings.mix_flame_civilian,
                "UnarmedCiv_Wt": settings.mix_unarmed_civilian,
                "MolotovCiv_Wt": settings.mix_molotov_civilian,
            }
        specs.append(spec)
    return specs, skipped


def _collect_global_generator(context):
    """Return the GLOGENDC overrides, or None to let the builder decide.

    The placed marker carries the level's spawning budget: the NPC cap, the
    rate, and which creature generators without weights fall back to. Two
    markers would be a mistake the engine cannot report, so the first is used
    and the count is recorded for the report.

    Returning `{}` when a marker exists but has no overrides would be wrong —
    it would silently replace the builder's defaults with nothing — so the
    fields are always read from the marker.
    """
    from . import placeables

    found = [obj for obj in context.scene.objects
             if obj.get(placeables.PLACEABLE_PROP)
             == placeables.KIND_GLOBAL_GENERATOR]
    if not found:
        return None
    settings = found[0].rif_placeable
    if len(found) > 1:
        context.scene["rif_duplicate_glogendc"] = [o.name for o in found]
    return {
        "enemy_generated": (
            GENERATE_MARINES if settings.enemy_kind == "marines"
            else GENERATE_ALIENS),
        "max_npcs": settings.max_npcs,
        "npcs_per_minute": settings.npcs_per_minute,
        "npc_acceleration": settings.npc_acceleration,
        "hive_state_change_time": settings.hive_state_change_time,
    }


def _report(operator, levels, message):
    """Send a message to the UI **and** the console.

    The status bar shows one line and it disappears on the next action, so a
    user who wants to report a problem has nothing to copy. Everything our
    operators say therefore goes to both, tagged so it can be found in a wall of
    Blender output.
    """
    operator.report(levels, message)
    if "ERROR" in levels:
        tag = "ERROR"
    elif "WARNING" in levels:
        tag = "WARNING"
    else:
        tag = "INFO"
    print(f"[RIF] {tag}: {message}")


def _gather_meshes(context, selected_only=False):
    """Return the meshes to export, in a stable order.

    The whole scene is the default because a level IS the scene: exporting only
    what happened to be selected drops rooms silently, and the level looks
    broken in game with nothing in Blender to explain why. Sorting by name
    keeps the object indices, and therefore the module links, reproducible
    between runs — a set-ordered export would shuffle them.
    """
    if selected_only:
        candidates = list(getattr(context, "selected_objects", None) or [])
    else:
        scene = getattr(context, "scene", None)
        candidates = list(getattr(scene, "objects", None) or [])
    from .placeables import PLACEABLE_PROP

    meshes = [obj for obj in candidates
              if obj.type == "MESH" and PLACEABLE_PROP not in obj]
    meshes.sort(key=lambda obj: obj.name)
    return meshes


def _sound_directory(context):
    """Return this scene's Sound Directory, or None.

    Read from the World rather than the addon preferences: one .blend can hold
    several scenes, and each is a level with its own folder.
    """
    from . import sounds
    return sounds.directory_of(context)


def _curve_points(obj):
    """Return a curve object's control points in world metres, in order.

    Works for both curve types: a Bezier has handles and a NURBS or poly curve
    has plain points. Handles are ignored because the engine's path is a
    straight line between modules -- there is nothing for a curve to smooth into.
    """
    curve = getattr(obj, "data", None)
    if curve is None or not hasattr(curve, "splines"):
        return None
    points = []
    for spline in curve.splines:
        if hasattr(spline, "bezier_points") and len(spline.bezier_points):
            local = [p.co for p in spline.bezier_points]
        else:
            local = [p.co for p in spline.points]
        for point in local:
            local = Vector((point[0], point[1], point[2]))
            world = obj.matrix_world @ local
            points.append((world.x, world.y, world.z))
    return points


def _module_index_at(objs, bounds, position):
    """Return the index of the module containing a point, or None.

    The index is the position in `objs`, which is the order the builder writes
    them, so it is the same number the engine's `get_object_by_index` expects.
    """
    for index, (_name, box) in enumerate(bounds):
        if box is None:
            continue
        low, high = box
        if all(low[axis] <= position[axis] <= high[axis] for axis in range(3)):
            return index
    return None


def _collect_paths(context, objs):
    """Return (path specs, problems) for every patrol curve in the scene.

    Each spec carries `name`, `path_id`, `flags` and `points`, where a point is
    `(module_index, location_mm)`. Paths whose points fall outside every module
    are reported rather than exported with a guessed index, because a wrong
    index points an NPC at a module it was never meant to walk.
    """
    from .placeables import KIND_PATROL_PATH, PLACEABLE_PROP
    from .rif.chunks.path import PATH_FLAG_BACK_AND_FORTH

    placed = [obj for obj in context.scene.objects
              if obj.get(PLACEABLE_PROP) == KIND_PATROL_PATH]
    if not placed:
        return [], []

    bounds = [(obj.name, _world_box(obj)) for obj in objs]
    specs = []
    problems = []

    for slot, obj in enumerate(sorted(placed, key=lambda o: o.name)):
        points = _curve_points(obj)
        if not points:
            problems.append(
                f"Patrol route {obj.name!r} has no points. The engine has no "
                f"path to walk, and a route with no modules is an error rather "
                f"than an empty patrol")
            continue

        converted = []
        outside = []
        for point in points:
            # The owning module is worked out in Blender's frame, against boxes
            # that are also in Blender's frame. Converting first would move the
            # point out of the box it belongs to.
            index = _module_index_at(objs, bounds, point)
            if index is None:
                outside.append(point)
                continue
            # The stored location, though, is world millimetres the engine
            # reads directly, so it has to be in the engine's frame -- the same
            # conversion the module positions and the vertices go through.
            # Without it a marine walks the right route in the wrong place.
            rif_point = blender_to_rif_axis(*point)
            converted.append((index, tuple(int(round(v * 1000.0))
                                           for v in rif_point)))
        if outside:
            problems.append(
                f"Patrol route {obj.name!r} has {len(outside)} point(s) outside "
                f"every module. A path is a list of modules, so each point has "
                f"to lie inside one; the export leaves those points out, which "
                f"changes the route")

        if not converted:
            problems.append(
                f"Patrol route {obj.name!r} has no point inside any module, so "
                f"there is nothing to write and it is skipped")
            continue

        specs.append({
            "name": obj.name,
            "path_id": slot,
            "flags": PATH_FLAG_BACK_AND_FORTH,
            "points": converted,
        })

    return specs, problems


def _collect_sounds(context):
    """Return a spec per sound emitter in the scene.

    `snd_name` comes from the Blender object name and `wav_name` from the
    panel's chosen file, which is the same split retail uses: one names the
    emitter in this level, the other names the audio. Keeping them apart means
    renaming an object never changes what it plays.
    """
    from . import sounds
    from .placeables import KIND_SOUND, PLACEABLE_PROP

    specs = []
    for obj in context.scene.objects:
        if obj.get(PLACEABLE_PROP) != KIND_SOUND:
            continue
        settings = obj.rif_placeable
        loc = obj.matrix_world.translation
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        flags = ((0x1 if settings.sound_not_at_start else 0)
                 | (0x2 if settings.sound_no_loop else 0))
        # The sphere's size is the reach: the mesh has a radius of one metre, so
        # its scale in metres is the radius in metres. A stretched sphere uses
        # the mean, and the panel says so.
        scale = obj.matrix_world.to_scale()
        radius_m = (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0
        outer = max(0, int(round(radius_m * 1000.0)))
        fraction = min(max(settings.sound_inner_fraction, 0.0), 1.0)
        specs.append({
            "position": (meters_to_rif(bx), meters_to_rif(by),
                         meters_to_rif(bz)),
            "wav_name": ("" if settings.sound_wav == sounds.NONE_CHOICE
                         else settings.sound_wav or ""),
            "snd_name": obj.name,
            "inner_range": int(round(outer * fraction)),
            "outer_range": outer,
            "max_volume": settings.sound_volume,
            "pitch": settings.sound_pitch,
            "flags": flags,
            "probability": 0,
        })
    return specs


def _collect_placed_objects(context, objs):
    """Return a spec per placed object, such as a pickup.

    A pickup is a **mesh** carrying the placeable marker, and it must not reach
    the module list: `_gather_meshes` already drops every marked object, which
    is what keeps it out of the collision system. It still needs a REBSHAPE and
    an RBOBJECT, so it is gathered here instead and the builder emits it with
    `PLACED_OBJECT_FLAGS` and an `OBJPRJDT > AVPSTRAT` child.

    The category comes from the menu entry that created the object and the
    concrete item from `sub_type`, so neither is guessed here.
    """
    from .placeables import KIND_OBJECT, KIND_PICKUP, PLACEABLE_PROP
    from .rif.builder import _convert_orientation
    from .rif.chunks.strategy import compose_integrity

    specs = []
    for obj in context.scene.objects:
        if obj.type != "MESH":
            continue
        marked = obj.get(PLACEABLE_PROP)
        if marked not in (KIND_PICKUP, KIND_OBJECT):
            # An object built by 3.3.0 has no marker at all, so an author's
            # pickups from that window would silently vanish from the export.
            # A mesh that names a pickup category and is not one of our placed
            # objects is a pickup, whatever it is labelled -- and the marker is
            # set here so the panel agrees from now on.
            settings_candidate = getattr(obj, "rif_placeable", None)
            if (settings_candidate is None
                    or getattr(settings_candidate, "kind", "") != KIND_PICKUP
                    or getattr(obj, "rif_object_type", "MODULE") != "SIMPLE_OBJECT"):
                continue
            obj[PLACEABLE_PROP] = KIND_PICKUP
        settings = obj.rif_placeable
        loc = obj.matrix_world.translation
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        _, rotation, _scale = obj.matrix_world.decompose()
        specs.append({
            "mesh": obj.data,
            "name": obj.name,
            "location": (meters_to_rif(bx), meters_to_rif(by),
                         meters_to_rif(bz)),
            "orientation": _convert_orientation(rotation),
            "strategy": {
                "type_id": settings.pickup_type,
                # The panel shows a named variant for a pickup, and the number
                # it stands for is what the file wants. A generator has no such
                # list, so its own sub_type is used unchanged.
                "sub_type": (int(settings.pickup_variant)
                              if obj.get(PLACEABLE_PROP) == KIND_PICKUP
                              else settings.sub_type),
                # Shown as a plain number in the panel; the spares marker the
                # format always carries is added here rather than by the artist.
                "integrity": compose_integrity(settings.object_integrity),
                "mass": settings.object_mass,
                "flags": 1 | (0x10 if settings.object_heavy else 0),
            },
        })
    return specs


def _waypoint_box(obj):
    """Return ((min), (max)) for a waypoint in world metres, ignoring rotation.

    The engine's waypoint is an **axis-aligned box**, so a rotated object has no
    meaning. The local bounds are used rather than the world AABB for the same
    reason: a cube turned to its corner measures 73% more once rotated into
    world space, and the artist would have no way to see why the volumes
    disagree.

    The size comes from the mesh rather than from `obj.scale`, because
    `Object > Apply > All Transforms` sets the scale to one and the location to
    zero, baking both into the vertices. Reading the transform made every
    applied waypoint collapse to the world origin -- 22 identical boxes with a
    link between each pair. The mesh's bounds survive that, since that is where
    the numbers went, and they are what the artist sees on screen either way.
    """
    corners = [tuple(corner) for corner in obj.bound_box]
    if not corners:
        return None
    origin = obj.matrix_world.translation
    scale = obj.scale
    low = []
    high = []
    for axis in range(3):
        values = [corner[axis] for corner in corners]
        factor = scale[axis]
        low.append(origin[axis] + min(values) * factor)
        high.append(origin[axis] + max(values) * factor)
    return (tuple(low), tuple(high))


def _waypoint_is_rotated(obj):
    """Return True when a waypoint carries a rotation the file cannot express."""
    rotation = obj.rotation_euler
    return any(abs(value) > 1e-6 for value in rotation)


def _collect_waypoints(context, objs):
    """Return {module index: [waypoint record, ...]} for the whole level.

    A waypoint belongs to the module that contains its **origin**. The box may
    be far larger than the module and may spill into the next room; that is
    normal, and retail does it often. What matters is which module owns the
    chunk, because the engine only consults the waypoint list of the AI module
    an NPC is standing in — so the origin decides where the waypoint takes
    effect, and the artist controls it by moving the object.

    A waypoint whose origin is outside every module is reported as a problem
    rather than silently attached to whatever is nearest.
    """
    from .placeables import KIND_WAYPOINT, PLACEABLE_PROP
    from .rif import waypoints as rif_waypoints
    from .rif.chunks.waypoint import WAYPOINT_FLAG_CENTRE_DEFINED_BY_USER

    bounds = [(obj.name, _world_box(obj)) for obj in objs]
    bounds = [(name, box) for name, box in bounds if box is not None]
    if not bounds:
        return {}, [], []

    placed = [obj for obj in context.scene.objects
              if obj.get(PLACEABLE_PROP) == KIND_WAYPOINT]
    if not placed:
        return {}, [], []

    # Group by owning module, then link within each group.
    grouped = {}
    orphaned = []
    rotated = [obj.name for obj in placed if _waypoint_is_rotated(obj)]
    for obj in placed:
        box = _waypoint_box(obj)
        if box is None:
            continue
        low, high = box
        origin = obj.matrix_world.translation
        owner = None
        for index, (_name, (m_low, m_high)) in enumerate(bounds):
            if all(m_low[k] <= origin[k] <= m_high[k] for k in range(3)):
                owner = index
                break
        if owner is None:
            orphaned.append(obj.name)
            continue
        # Stored in millimetres, absolute, matching the file format. The crawl
        # setting rides along, because the box alone cannot say whether the node
        # is meant for a wall.
        grouped.setdefault(owner, []).append((
            tuple(int(round(v * 1000.0)) for v in low),
            tuple(int(round(v * 1000.0)) for v in high),
            # The centre is the object's origin: the artist moves it inside a
            # wall to mark a climb, which is the only way the engine is told to
            # put an alien on a vertical surface.
            tuple(int(round(origin[a] * 1000.0)) for a in range(3)),
        ))

    specs = {}
    for index, boxes in grouped.items():
        # The graph compares geometry only; the crawl setting is not part of it.
        graph = rif_waypoints.build_graph(
            [(lo, hi) for lo, hi, _o in boxes])
        links = rif_waypoints.link_entries(graph)
        records = []
        for position, (low, high, centre) in enumerate(boxes):
            # Not the midpoint: see the module docstring. Moving the object
            # puts the centre wherever the level needs it, and inside a wall is
            # how a climb is declared.
            flags = WAYPOINT_FLAG_CENTRE_DEFINED_BY_USER
            records.append((low, high, centre, flags, links[position]))
        specs[index] = records
    return specs, orphaned, rotated


def _world_box(obj):
    """Return ((min), (max)) of an object's bounds in world metres, or None.

    Uses ound_box, the object's local box, transformed by matrix_world —
    the same transform the exporter places geometry with, so the check matches
    what actually lands in the file.
    """
    corners = getattr(obj, "bound_box", None)
    if not corners:
        return None
    matrix = obj.matrix_world
    xs, ys, zs = [], [], []
    for corner in corners:
        world = matrix @ Vector(corner)
        xs.append(world.x)
        ys.append(world.y)
        zs.append(world.z)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _sound_layout_problems(context):
    """Return what is wrong with the scene's sound emitters, worst first.

    `inner_range` must be smaller than `outer_range`: the engine fades from full
    volume at the inner radius to silence at the outer one, and the shipping data
    never reverses them. A reversed pair is silent everywhere except at the exact
    centre, which looks like a broken sound rather than a mistyped number.

    An emitter with no file chosen writes a name the game cannot resolve, and a
    level with emitters but no Sound Directory resolves none of them, so both are
    worth saying before the export rather than after a silent level.
    """
    from . import placeables
    from . import sounds

    problems = []
    emitters = [obj for obj in context.scene.objects
                if obj.get(placeables.PLACEABLE_PROP)
                == placeables.KIND_SOUND]

    # The fraction cannot exceed the radius by construction, so this is a
    # guard against a .blend written when both were typed in by hand.
    zero_reach = [obj.name for obj in emitters
                  if (abs(obj.matrix_world.to_scale().x)
                      + abs(obj.matrix_world.to_scale().y)
                      + abs(obj.matrix_world.to_scale().z)) / 3.0 <= 0.0]
    if zero_reach:
        names = ", ".join(zero_reach[:4])
        more = "" if len(zero_reach) <= 4 else \
            f" (+{len(zero_reach) - 4} more)"
        problems.append(
            f"Zero-sized sound sphere on: {names}{more}. The sphere's size is "
            f"the emitter's reach, so a zero-sized one is audible only at an "
            f"exact point")

    unset = [obj.name for obj in emitters
             if not obj.rif_placeable.sound_wav
             or obj.rif_placeable.sound_wav == sounds.NONE_CHOICE]
    if unset:
        names = ", ".join(unset[:4])
        more = "" if len(unset) <= 4 else f" (+{len(unset) - 4} more)"
        problems.append(
            f"No sound chosen on: {names}{more}. The emitter is written with an "
            f"empty file name, which the game cannot resolve")

    if emitters and not sounds.directory_of(context):
        problems.append(
            f"{len(emitters)} sound emitter(s) but no Sound Directory for this "
            f"scene. The audio is addressed as folder plus file name, so "
            f"without a folder none of them resolve. Set it in Properties > "
            f"World > Level Audio")

    return problems


def _waypoint_layout_problems(context):
    """Return what is wrong with the scene's waypoints.

    The one worth catching is an origin that has been zeroed. `centre` is the
    point a bot walks toward and it is read from the object's location, so
    `Object > Apply > All Transforms` -- which sets that location to zero --
    sends every applied waypoint's target to the world origin. The volume still
    looks correct, because it reads the mesh, so nothing on screen gives it
    away.
    """
    from .placeables import KIND_WAYPOINT, PLACEABLE_PROP

    placed = [obj for obj in context.scene.objects
              if obj.get(PLACEABLE_PROP) == KIND_WAYPOINT]
    if not placed:
        return []

    translation = [obj.matrix_world.translation for obj in placed]
    at_origin = [obj.name for obj, loc in zip(placed, translation)
                 if max(abs(v) for v in loc) < 1e-6]
    # One waypoint genuinely at the origin is possible. Every one of them is an
    # apply, and nothing else produces that pattern.
    if len(at_origin) < 2 or len(at_origin) != len(placed):
        return []

    return [
        f"All {len(at_origin)} waypoint(s) have their origin at the world "
        f"origin. The centre -- the point a bot walks toward -- is read from "
        f"the object's location, so `Object > Apply > All Transforms` has sent "
        f"every target there. The volumes still look right, which is why this "
        f"is easy to miss. Undo the apply, or set each origin back with "
        f"`Object > Set Origin`"
    ]


def _collect_mission_objective(context, objs):
    """Return (objective or None, problems) for the scene.

    Retail ships exactly one objective in each of its 31 mission levels. A
    second is reported and ignored rather than exported: the engine sorts them
    by `mission_number` and uses the first, so writing both would leave the
    level's ending decided by a number the artist may not have set.
    """
    from .placeables import KIND_MISSION_OBJECTIVE, PLACEABLE_PROP
    from .rif.chunks.objective import MissionObjective

    found = [obj for obj in context.scene.objects
             if obj.get(PLACEABLE_PROP) == KIND_MISSION_OBJECTIVE]
    if not found:
        return None, []

    problems = []
    found.sort(key=lambda o: o.name)
    if len(found) > 1:
        names = ", ".join(o.name for o in found)
        problems.append(
            f"{len(found)} mission objectives in the scene: {names}. The game "
            f"sorts them by order number and uses the first, so only {found[0].name!r} "
            f"is written. A level has one ending")

    # A marker, not a volume. Retail's objectives are all `size = 1000`, one
    # metre, which is a default rather than a measurement, and the engine reads
    # the position and the name and nothing else. The name is what matters most:
    # the id a switch will aim at is `MD5("VirtOb" + name)`, so renaming the
    # object changes the id a switch has to use.
    obj = found[0]
    world = obj.matrix_world.translation
    rif_position = blender_to_rif_axis(world.x, world.y, world.z)
    centre = tuple(int(round(v * 1000.0)) for v in rif_position)

    settings = obj.rif_placeable
    return MissionObjective(
        name=obj.name,
        position=centre,
        description=int(settings.objective_description),
        complete=int(settings.objective_complete),
        order=int(settings.objective_order),
    ), problems


def _particle_orientation(obj):
    """Return the emitter's direction as the chunk stores it.

    `Objsetup.cpp:3809` negates x, y and z as it reads the four floats, so
    the chunk holds the negation of the RIF quaternion and the engine's
    negation restores it. Inverting twice is the whole conversion, and its
    absence is why an emitter written with the identity always fired the
    same way however its object was turned.

    An emitter points along its own axis, so the object's rotation is what
    the artist sets in the viewport.
    """
    from .rif.builder import _convert_orientation

    quat = obj.matrix_world.to_quaternion()
    x, y, z, w = _convert_orientation(quat)
    return (-x, -y, -z, w)


def _collect_particles(context, objs):
    """Return (emitters, problems) for the scene's particle generators.

    A marker with a world position: the engine works out which module the
    generator belongs to from that position, so a generator outside every module
    is one that never runs. The position takes the same axis conversion as every
    other position here.

    The sound is reported rather than written: `INDSOUND` is this chunk's child
    in 444 of the 570 retail generators, but there is no writer for it yet, and
    a generator without its sound is a whole emitter rather than a broken one.
    """
    from . import sounds
    from .placeables import KIND_PARTICLE_GENERATOR, PLACEABLE_PROP
    from .rif.chunks.particle import (ParticleGenerator,
                                      PARGEN_TYPE_SPARK)

    bounds = [(obj.name, _world_box(obj)) for obj in objs]
    emitters = []
    problems = []
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_PARTICLE_GENERATOR:
            continue
        world = obj.matrix_world.translation
        rif_position = blender_to_rif_axis(world.x, world.y, world.z)
        position = tuple(int(round(v * 1000.0)) for v in rif_position)

        if _module_index_at(objs, bounds, (world.x, world.y, world.z)) is None:
            problems.append(
                f"Particle generator {obj.name!r} is outside every module. The "
                f"engine works out which module it belongs to from its "
                f"position, so this one would never run")

        settings = obj.rif_placeable
        particle_type = int(settings.particle_type)
        emitters.append(ParticleGenerator(
            name=obj.name,
            position=position,
            particle_type=particle_type,
            # Only a spark reads the interval; the rest divide by quantity, and
            # the engine does that division, so both are simply carried.
            frequency_seconds=float(settings.particle_frequency),
            probability=int(settings.particle_probability),
            quantity=int(settings.particle_quantity),
            speed_metres=float(settings.particle_speed),
            active=bool(settings.particle_active),
            # The list uses the same sentinel as the sound object's, which is a
            # non-empty string and so cannot be tested for truth.
            sound_name=(None if settings.particle_sound == sounds.NONE_CHOICE
                        else settings.particle_sound or None),
            # Millimetres in the chunk, metres in the panel.
            sound_inner_mm=int(round(
                float(settings.particle_sound_inner) * 1000.0)),
            sound_outer_mm=int(round(
                float(settings.particle_sound_outer) * 1000.0)),
            sound_loop=bool(settings.particle_sound_loop),
            orientation=_particle_orientation(obj),
        ))
        if particle_type != PARGEN_TYPE_SPARK and \
                float(settings.particle_frequency):
            problems.append(
                f"Particle generator {obj.name!r} is not a spark, so its "
                f"Frequency is ignored -- the interval comes from Quantity "
                f"({settings.particle_quantity})")
    return emitters, problems


def _collect_messages(context, objs):
    """Return (messages, problems) for the scene's Message objects.

    A marker with no volume, so the position comes from `matrix_world`. The
    only field that can be wrong is the text index, and the engine asserts on
    it: outside one to 449 it takes the level down rather than failing quietly,
    so it is checked here.
    """
    from .placeables import KIND_MESSAGE, PLACEABLE_PROP
    from .rif.chunks.objective import (TextMessageStrategyChunk,
                                       DEFAULT_MESSAGE_SIZE, MAX_TEXT_INDEX)
    from .rif.chunks.trigger import VirtualObjectChunk

    messages = []
    problems = []
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_MESSAGE:
            continue
        settings = obj.rif_placeable
        index = int(settings.message_string)
        if not 1 <= index < MAX_TEXT_INDEX:
            problems.append(
                f"Message {obj.name!r} uses text number {index}. The engine "
                f"asserts on anything outside 1 to {MAX_TEXT_INDEX - 1}, so "
                f"this would take the level down on load")
            continue

        world = obj.matrix_world.translation
        rif_position = blender_to_rif_axis(world.x, world.y, world.z)
        position = tuple(int(round(v * 1000.0)) for v in rif_position)

        # `TextMessageFlag_NotActiveAtStart` is the only flag there is, and it
        # is inverted against the panel: ticking "show at start" clears it.
        flags = 0 if settings.message_shown_at_start else 1
        messages.append(VirtualObjectChunk(
            obj.name,
            TextMessageStrategyChunk(index, flags, 0),
            location=position,
            size=DEFAULT_MESSAGE_SIZE,
        ))
    return messages, problems


def _collect_self_destructs(context, objs):
    """Return (self_destructs, problems) for the scene.

    A marker, so the position comes from `matrix_world`. A level may carry more
    than one -- `Vaults_M.RIF` ships two -- so unlike a mission objective a
    second is not a fault.
    """
    from .placeables import KIND_SELF_DESTRUCT, PLACEABLE_PROP
    from .rif.chunks.selfdestruct import SelfDestruct

    found = []
    problems = []
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_SELF_DESTRUCT:
            continue
        world = obj.matrix_world.translation
        rif_position = blender_to_rif_axis(world.x, world.y, world.z)
        position = tuple(int(round(v * 1000.0)) for v in rif_position)
        found.append(SelfDestruct(
            name=obj.name,
            position=position,
            timer_seconds=int(obj.rif_placeable.self_destruct_timer),
        ))
    return found, problems


def _collect_multi_switches(context, objs):
    """Return (switches, problems) for the scene's MultiSwitch objects.

    A marker rather than a volume, so its position comes from `matrix_world`.
    The targets are shared with `_collect_area_switches`: a switch driving
    another switch is what the type is for, so both kinds are addressable.
    """
    from .placeables import (KIND_AREA_SWITCH, KIND_DEATH_VOLUME,
                             KIND_MISSION_OBJECTIVE, KIND_MULTI_SWITCH,
                             KIND_SELF_DESTRUCT, KIND_MESSAGE,
                             PLACEABLE_PROP)
    from .rif.chunks.switch import (MultiSwitch, SwitchTarget, MODE_WAIT,
                                    TIMED_MODES)
    from .rif.chunks.trigger import virtual_object_id

    addressable = set()
    for obj in context.scene.objects:
        if obj.get(PLACEABLE_PROP) in (KIND_DEATH_VOLUME,
                                       KIND_MISSION_OBJECTIVE,
                                       KIND_AREA_SWITCH, KIND_MULTI_SWITCH,
                                       KIND_SELF_DESTRUCT, KIND_MESSAGE):
            addressable.add(obj.name)

    switches = []
    problems = []
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_MULTI_SWITCH:
            continue
        world = obj.matrix_world.translation
        rif_position = blender_to_rif_axis(world.x, world.y, world.z)
        position = tuple(int(round(v * 1000.0)) for v in rif_position)

        settings = obj.rif_placeable
        targets = []
        for entry in settings.switch_targets:
            name = entry.target_name.strip()
            if not name:
                problems.append(
                    f"Multi switch {obj.name!r} has a target with no object "
                    f"named, so there is nothing to send to")
                continue
            if name not in addressable:
                problems.append(
                    f"Multi switch {obj.name!r} sends to {name!r}, which is not "
                    f"a kill volume, mission objective or switch in this scene. "
                    f"The engine matches on an id derived from the name, so this "
                    f"target would never be found")
            targets.append(SwitchTarget(virtual_object_id(name),
                                        int(entry.target_request)))

        mode = int(settings.switch_mode)
        switches.append(MultiSwitch(
            name=obj.name,
            position=position,
            targets=targets,
            mode=mode,
            time_seconds=(float(settings.switch_time)
                          if mode in TIMED_MODES else 0.0),
            rest_state=1 if settings.switch_rest_on else 0,
        ))

    return switches, problems


def _collect_area_switches(context, objs):
    """Return (switches, problems) for the scene's AreaSwitch objects.

    A switch names its targets, and the name becomes an object id here. Whether
    that name matches anything is checked against the scene, because the engine
    looks the id up and a name that matches nothing produces a switch that does
    nothing at all -- silently, since the chunk is well formed either way.
    """
    from .placeables import (KIND_AREA_SWITCH, KIND_DEATH_VOLUME,
                             KIND_MESSAGE, KIND_MISSION_OBJECTIVE,
                             KIND_MULTI_SWITCH, KIND_SELF_DESTRUCT,
                             PLACEABLE_PROP)
    from .rif.chunks.switch import (AreaSwitch, SwitchTarget, REQUEST_OFF,
                                    MODE_WAIT, TIMED_MODES)
    from .rif.chunks.trigger import virtual_object_id

    # Everything a switch may aim at, by the name the artist sees.
    addressable = {}
    for obj in context.scene.objects:
        kind = obj.get(PLACEABLE_PROP)
        if kind in (KIND_DEATH_VOLUME, KIND_MISSION_OBJECTIVE,
                    KIND_AREA_SWITCH, KIND_MULTI_SWITCH, KIND_SELF_DESTRUCT,
                    KIND_MESSAGE):
            addressable[obj.name] = kind

    switches = []
    problems = []
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_AREA_SWITCH:
            continue
        box = _world_box(obj)
        if box is None:
            problems.append(
                f"Area switch {obj.name!r} has no measurable box, so nothing "
                f"can walk into it")
            continue
        corners = [blender_to_rif_axis(*corner) for corner in box]
        low = tuple(min(c[axis] for c in corners) for axis in range(3))
        high = tuple(max(c[axis] for c in corners) for axis in range(3))
        millimetres = [tuple(int(round(v * 1000.0)) for v in corner)
                       for corner in (low, high)]
        if any(millimetres[0][a] >= millimetres[1][a] for a in range(3)):
            problems.append(
                f"Area switch {obj.name!r} is flat in at least one axis, so "
                f"nothing can be inside it")
            continue

        settings = obj.rif_placeable
        targets = []
        for entry in settings.switch_targets:
            name = entry.target_name.strip()
            if not name:
                problems.append(
                    f"Area switch {obj.name!r} has a target with no object "
                    f"named, so there is nothing to send to")
                continue
            if name not in addressable:
                problems.append(
                    f"Area switch {obj.name!r} sends to {name!r}, which is not "
                    f"a kill volume, mission objective or switch in this scene. "
                    f"The engine matches on an id derived from the name, so this "
                    f"target would never be found")
            targets.append(SwitchTarget(virtual_object_id(name),
                                        int(entry.target_request)))

        mode = int(settings.switch_mode)
        switches.append(AreaSwitch(
            name=obj.name,
            low=millimetres[0],
            high=millimetres[1],
            targets=targets,
            mode=mode,
            time_seconds=(float(settings.switch_time)
                          if mode in TIMED_MODES else 0.0),
            rest_state=1 if settings.switch_rest_on else 0,
        ))

    return switches, problems


def _collect_death_volumes(context, objs):
    """Return a DeathVolume per marked object, and any problems.

    A kill volume needs no geometry, so the object is only a way of drawing the
    box: its world bounds become `volume_min` and `volume_max` in millimetres,
    and the object itself is left out of the level. It is excluded by its kind,
    the same way a waypoint is.
    """
    from .placeables import KIND_DEATH_VOLUME, PLACEABLE_PROP
    from .rif.chunks.trigger import DeathVolume

    volumes = []
    problems = []

    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.get(PLACEABLE_PROP) != KIND_DEATH_VOLUME:
            continue
        box = _world_box(obj)
        if box is None:
            problems.append(
                f"Kill volume {obj.name!r} has no measurable box. Its bounds "
                f"are the whole of it, so there is nothing to write")
            continue
        # Into the engine's frame before anything else. Blender is Z-up and
        # the engine Y-up, so an unconverted box is rotated 90 degrees and
        # lands somewhere else -- except at the origin, where the rotation is
        # invisible. That is exactly how a volume placed at (0, 0, 0) worked
        # while the same volume on the floor did not.
        corners = [blender_to_rif_axis(*corner) for corner in box]
        low = tuple(min(c[axis] for c in corners) for axis in range(3))
        high = tuple(max(c[axis] for c in corners) for axis in range(3))
        millimetres = [tuple(int(round(v * 1000.0)) for v in corner)
                       for corner in (low, high)]
        if any(millimetres[0][a] >= millimetres[1][a] for a in range(3)):
            problems.append(
                f"Kill volume {obj.name!r} is flat in at least one axis, so "
                f"nothing can be inside it")
            continue
        settings = obj.rif_placeable
        volumes.append(DeathVolume(
            name=obj.name,
            low=millimetres[0],
            high=millimetres[1],
            damage=int(settings.death_damage),
            starts_on=bool(settings.death_starts_on),
            collision_required=bool(settings.death_collision_required),
        ))

    return volumes, problems


def _envir_settings(context):
    """Return the AVPENVIR settings for the scene's World, or None.

    None when the scene has no World, which is the one case where there is
    nothing to take the settings from; every level with an environment chunk in
    retail has one, so a level without a World is unusual rather than normal.
    """
    world = context.scene.world
    if world is None:
        return None

    def to_byte(value):
        """Convert a linear float to the byte the engine stores."""
        linear = max(0.0, min(1.0, float(value)))
        sRGB = (linear * 12.92 if linear <= 0.0031308
                else 1.055 * (linear ** (1.0 / 2.4)) - 0.055)
        return int(round(max(0.0, min(1.0, sRGB)) * 255.0))

    from .rif.chunks.environment_settings import pack_bits

    return {
        "sky_colour": tuple(to_byte(v)
                            for v in getattr(world, "rif_sky_colour",
                                             (0.0129, 0.0129, 0.0129))),
        # The chunk stores one packed word, so the flags are packed here rather
        # than handed over separately and re-packed by the writer.
        "bits": pack_bits(
            # The loader's own defaults: the Predator keeps all four of these
            # unless a level says otherwise (AVPCHUNK.CPP:629).
            predator_pistol=True,
            predator_plasmacaster=True,
            predator_disc=True,
            predator_medicomp=True,
            predator_grappling_hook=bool(
                getattr(world, "rif_predator_grappling_hook", False)),
            marine_jetpack=bool(getattr(world, "rif_marine_jetpack", False)),
            stars_in_sky=bool(getattr(world, "rif_stars_in_sky", False))),
        "predator_num_spears": 30,
    }


def _path_layout_problems(context, objs):
    """Return what is wrong with the scene's patrol routes.

    Only the faults the engine cannot recover from are reported. A route is
    optional and a generator without a mission is normal, so silence is the
    common case.
    """
    from .placeables import (KIND_NPC, KIND_DEFAULT_ENEMY, KIND_PATROL_PATH,
                             KIND_WEIGHTED, PLACEABLE_PROP)
    from .sounds import NONE_CHOICE

    placed = [obj for obj in context.scene.objects
              if obj.get(PLACEABLE_PROP) == KIND_PATROL_PATH]
    generators = [obj for obj in context.scene.objects
                  if obj.get(PLACEABLE_PROP) in
                  (KIND_NPC, KIND_DEFAULT_ENEMY, KIND_WEIGHTED)]
    if not placed and not generators:
        return []

    routes = {obj.name: obj for obj in placed}
    bounds = [(obj.name, _world_box(obj)) for obj in objs]
    problems = []

    for obj in sorted(generators, key=lambda o: o.name):
        settings = obj.rif_placeable
        if int(settings.mission or 0) != 5:
            continue

        chosen = settings.patrol_path
        route = None if chosen in ("", NONE_CHOICE) else routes.get(chosen)
        if route is None:
            problems.append(
                f"Patrol generator {obj.name!r} has no route chosen. A patrol "
                f"is a list of modules to walk, so without one the creature has "
                f"nowhere to go")
            continue

        points = _curve_points(route) or []
        modules = set()
        sequence = []
        for point in points:
            index = _module_index_at(
                objs, bounds, point)
            if index is not None:
                modules.add(index)
                sequence.append(index)

        # Two points in one module are two steps asking for the same place,
        # because the engine walks `modules_in_path` and not the coordinates.
        # Retail never repeats: in all 69 paths it ships, the points number
        # exactly the distinct modules. A route that does repeat leaves the
        # creature pacing in one room while the file looks correct.
        repeated = [i for i, (a, b) in enumerate(zip(sequence, sequence[1:]))
                    if a == b]
        if repeated:
            problems.append(
                f"Patrol route {route.name!r} has {len(repeated)} place(s) "
                f"where two points in a row are in the same module "
                f"({len(sequence)} points in {len(modules)} module(s)). The "
                f"engine walks the list of modules, so those are steps that ask "
                f"for somewhere the creature already is, and it paces instead "
                f"of patrolling. Put at most one point in each module, and give "
                f"the route a point in every module it should visit")
        if not modules:
            problems.append(
                f"Patrol route {route.name!r} has no point inside any module. "
                f"A path is a list of modules, so a route that lies outside all "
                f"of them cannot be written")
            continue

        start = _module_index_for(obj, objs)
        if start is None:
            problems.append(
                f"Patrol generator {obj.name!r} stands outside every module, so "
                f"the engine cannot place it on route {route.name!r}")
        elif start not in modules:
            problems.append(
                f"Patrol generator {obj.name!r} stands in a module that is not "
                f"on route {route.name!r}. The engine looks the module up on the "
                f"route to find the starting step, and reports 'Error setting up "
                f"path' -- with an assertion behind it -- when it is missing. Add "
                f"a route point in this module")

    return problems


def _module_layout_problems(objs, hierarchy, visibility=None):
    """Return a list of things wrong with the level's modules, worst first.

    One source of truth: the export warnings and the Validate Level command
    both read this, so they can never drift apart. An empty list means the
    layout is one the engine expects.
    """
    problems = []
    count = len(objs)
    if count < 2:
        problems.append(
            f"Only one module. The engine will not create an NPC while the "
            f"generator's module is visible, and with a single module every "
            f"generator is always visible — so Mixed Enemy and Default Enemy "
            f"can never spawn. A lone module also lists no peers in its "
            f"VMDARRAY, which no retail level does: the smallest has 14 "
            f"modules and the median is 190, so this arrangement is untested "
            f"by the game. Split the level into at least two objects")
        return problems

    # Either way of linking counts. The Outliner tree is one, each module's
    # Links list is the other, and a level that used only the second was being
    # told it had linked nothing.
    linked = {name for name, peers in (visibility or {}).items() if peers}
    if not hierarchy and len(linked) < 2:
        problems.append(
            f"{count} modules and no links, so no module names any other and "
            f"each one renders on its own: you will see only the room you stand "
            f"in. Link them either by parenting in the Outliner or by filling "
            f"in each module's Links list, and leave a generator's module "
            f"unlinked so it stays out of sight")

    if visibility is not None and linked and len(linked) < count:
        lonely = sorted(obj.name for obj in objs if obj.name not in linked)
        if lonely:
            problems.append(
                "These modules are linked to nothing and will only ever be "
                "visible from inside themselves: "
                + ", ".join(lonely[:4])
                + ". Add them to a Links list, or parent them")

    boxes = [(obj.name, _world_box(obj)) for obj in objs]
    boxes = [(name, box) for name, box in boxes if box is not None]
    overlapping, isolated = set(), []
    for index, (name, box) in enumerate(boxes):
        low, high = box
        touches = False
        for other, (other_name, other_box) in enumerate(boxes):
            if index == other:
                continue
            o_low, o_high = other_box
            gaps = [min(high[a], o_high[a]) - max(low[a], o_low[a])
                    for a in range(3)]
            if any(gap < -0.001 for gap in gaps):
                continue
            touches = True
            if all(gap > 0.001 for gap in gaps):
                overlapping.add(" / ".join(sorted((name, other_name))))
        if not touches:
            isolated.append(name)

    if overlapping:
        problems.append(
            "Modules overlap instead of touching: "
            + ", ".join(sorted(overlapping)[:4])
            + ". The engine expects modules to meet at a face; move them apart "
              "or expect the level to misbehave in game")
    if isolated:
        problems.append(
            "Modules touch nothing, so the player falls into empty space beside "
            "them: " + ", ".join(isolated[:4]))
    return problems


def _door_layout_problems(context):
    """Return what is wrong with the doors in the scene.

    A door is a module plus a second shape the engine moves
    (`introduction/glossary.htm`: doors are made by morphing between two
    versions of the shape). Three arrangements export and do nothing, and
    none of them looks wrong in Blender: a module ticked as a door with no
    child mesh, a tick on the leaf rather than the module, and a child mesh
    with no geometry.
    """
    problems = []
    for obj in context.scene.objects:
        if obj.type != "MESH":
            continue
        if not getattr(obj, "rif_module_door", False):
            continue
        # A leaf ticked instead of its module is reported below with the
        # message that names the mistake; saying "no child mesh" about it too
        # would bury the useful line under a redundant one.
        parent = obj.parent
        if (parent is not None and parent.type == "MESH"
                and not getattr(parent, "rif_module_door", False)):
            continue
        leaves = [child for child in obj.children if child.type == "MESH"]
        if not leaves:
            problems.append(
                f"Door {obj.name!r} has no child mesh, so there is no leaf "
                "to write into SHPMORPH and nothing will move. Parent a "
                "mesh to it and leave it where it is")
        elif not leaves[0].data.polygons:
            problems.append(
                f"Door {obj.name!r}: its leaf {leaves[0].name!r} has no "
                "faces, so the sub-shape would be empty")

    # The leaf marked instead of the module: nothing above catches it,
    # because such a mesh is a door with no child of its own, which the
    # first check reports -- but the message would name the leaf rather than
    # the mistake. Say the useful thing.
    for obj in context.scene.objects:
        if obj.type != "MESH" or not getattr(obj, "rif_module_door", False):
            continue
        parent = obj.parent
        if parent is None or parent.type != "MESH":
            continue
        if getattr(parent, "rif_module_door", False):
            continue
        problems.append(
            f"{obj.name!r} is ticked as a door but is a child of "
            f"{parent.name!r}, which is not. The flag belongs on the "
            "module and the leaf goes inside it as a child")
    return problems


def _budget_layout_problems(context, spawns):
    """Return what is wrong with how the level pays for its creatures.

    Two faults stop spawning dead and neither looks like anything in game: a
    rate of zero turns the whole system off (`BH_GENER.C:189`), and a maximum of
    zero leaves no room for a creature to be alive. A missing budget is a third:
    `GLOGENDC` is what gives a generator its limits, so without the marker there
    is nothing to read.

    `_collect_global_generator` returns None when the scene has no marker, which
    is why the check for that comes first -- reaching into a missing budget
    raised AttributeError and took the export down, a worse outcome than the
    fault it was reporting.
    """
    from . import placeables

    generators = [spec for spec in spawns
                  if spec.get("kind") not in (
                      placeables.KIND_PLAYER_START_MP,
                      placeables.KIND_PLAYER_START_SP)]
    if not generators:
        return []

    budget = _collect_global_generator(context)
    if budget is None:
        return [
            "The scene has generators but no Global Generator Data. GLOGENDC "
            "is what gives them a budget, so nothing would ever spawn"]

    problems = []
    if not budget.get("npcs_per_minute"):
        problems.append(
            "Global Generator Data has NPCSPerMinute = 0, which disables the "
            "whole generator system silently (BH_GENER.C:189). No generator "
            "will ever create an NPC")
    if not budget.get("max_npcs"):
        problems.append(
            "Global Generator Data has MaxNPCS = 0, so no creature can be "
            "alive at once and nothing will ever spawn")
    return problems


def _visibility_links(objs, mode):
    """Return {name: set(names)} of modules that can see each other.

    Separate from `_build_hierarchy` on purpose. A tree holds one parent per
    module, which is what OBJCHIER needs, but a module can have any number of
    neighbours and VMDARRAY wants all of them. Reducing a Links list to its first
    entry is what made a corridor that named two rooms see only one.
    """
    links = {obj.name: set() for obj in objs}
    names = {obj.name for obj in objs}

    if mode == "LIST":
        for obj in objs:
            for link in getattr(obj, "rif_module_links", ()):
                target = (link.target_name or "").strip()
                if target and target in names and target != obj.name:
                    links[obj.name].add(target)
    elif mode == "OUTLINER":
        for obj in objs:
            parent = getattr(obj, "parent", None)
            if parent is not None and parent.name in names:
                links[obj.name].add(parent.name)
                links[parent.name].add(obj.name)

    # Symmetric. If A can see B then B can see A, or standing in B you are blind
    # to a room that is watching you.
    for name, peers in list(links.items()):
        for peer in peers:
            links[peer].add(name)
    return links


def _build_hierarchy(objs, mode):
    """Return {child_name: parent_name} for the chosen link mode.

    The tree has one parent per module, so adjacency -- a graph -- has to be
    reduced to one. `Auto` picks the nearest module that is close enough to
    touch and has a lower index, which also makes a cycle impossible.
    """
    if mode == "LIST":
        pairs = {}
        for obj in objs:
            links = getattr(obj, "rif_module_links", ())
            for link in links:
                name = (link.target_name or "").strip()
                if name:
                    pairs[obj.name] = name
                    break
        return pairs or None

    if mode == "AUTO":
        from mathutils import Vector
        bounds = {}
        for obj in objs:
            corners = [obj.matrix_world @ Vector(c)
                       for c in obj.bound_box]
            bounds[obj.name] = (
                Vector((min(v.x for v in corners), min(v.y for v in corners),
                        min(v.z for v in corners))),
                Vector((max(v.x for v in corners), max(v.y for v in corners),
                        max(v.z for v in corners))))
        pairs = {}
        order = [obj.name for obj in objs]
        for index, name in enumerate(order):
            low, high = bounds[name]
            best = None
            best_gap = None
            for other_index in range(index):
                other = order[other_index]
                olow, ohigh = bounds[other]
                gap = max(low.x - ohigh.x, olow.x - high.x,
                          low.y - ohigh.y, olow.y - high.y,
                          low.z - ohigh.z, olow.z - high.z)
                if gap > 0:
                    continue
                if best_gap is None or gap < best_gap:
                    best_gap = gap
                    best = other
            if best is not None:
                pairs[name] = best
        return pairs or None

    return {o.name: o.parent.name for o in objs if o.parent is not None} or None


def _report_layout(operator, context, objs, hierarchy, visibility=None):
    """Warn about every layout problem the engine will not tolerate.

    Covers modules and sound emitters both, from the same rule sets the Debug
    command reads, so the two can never disagree about what is wrong.
    """
    for problem in _module_layout_problems(objs, hierarchy,
                                          visibility):
        _report(operator, {"WARNING"}, problem)
    for problem in _sound_layout_problems(context):
        _report(operator, {"WARNING"}, problem)
    for problem in _waypoint_layout_problems(context):
        _report(operator, {"WARNING"}, problem)
    for problem in _path_layout_problems(context, objs):
        _report(operator, {"WARNING"}, problem)
    # The budget rule was in the Debug command alone, so the export warned
    # about everything except the fault that stops a level spawning at all.
    spawns, _skipped = _collect_spawns(context, objs)
    for problem in _budget_layout_problems(context, spawns):
        _report(operator, {"WARNING"}, problem)
    for problem in _door_layout_problems(context):
        _report(operator, {"WARNING"}, problem)


class RIF_OT_audition_sound(bpy.types.Operator):
    """Write the chosen sound to a temporary file and play it."""

    bl_idname = "object.rif_audition_sound"
    bl_label = "Audition Sound"
    bl_description = (
        "Play the sound this emitter uses. The audio is copied straight out of "
        "the game's fastfile, so nothing needs unpacking first"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        """Offer the button for anything that carries a chosen sound.

        A placed sound and a particle generator both name a file from the
        level's Sound Directory, and both are worth hearing before the level is
        exported -- an emitter whose sound is wrong is easier to notice here
        than in game.
        """
        from . import placeables
        obj = context.active_object
        if obj is None:
            return False
        return obj.get(placeables.PLACEABLE_PROP) in (
            placeables.KIND_SOUND, placeables.KIND_PARTICLE_GENERATOR)

    def execute(self, context):
        """Carve the sound out of the library and play it."""
        from . import placeables, sounds

        obj = context.active_object
        root = sounds.game_root(context)
        if not root:
            self.report({"ERROR"},
                        "Set the game directory in preferences first")
            return {"CANCELLED"}

        kind = obj.get(placeables.PLACEABLE_PROP)
        field = ("particle_sound"
                 if kind == placeables.KIND_PARTICLE_GENERATOR
                 else "sound_wav")
        name = getattr(obj.rif_placeable, field)
        if not name or name == sounds.NONE_CHOICE:
            self.report({"ERROR"}, "No sound chosen")
            return {"CANCELLED"}

        directory = sounds.directory_of(context)
        if not sounds.carve(root, name, directory):
            where = f" in {directory}" if directory else ""
            self.report({"ERROR"},
                        f"{name} is not in the game's library{where}")
            return {"CANCELLED"}

        path = sounds.audition(root, name, directory)
        # Load it into Blender as well as playing it: that works everywhere, and
        # gives the artist something to scrub when no system player is present.
        loaded = sounds.load_into_blender(path, name)
        note = sounds.play(path)
        if note:
            self.report({"INFO"}, note)
        elif loaded:
            self.report({"INFO"}, f"Playing {name}")
        else:
            self.report({"INFO"}, f"Playing {name}")
        return {"FINISHED"}


class RIF_OT_validate_level(bpy.types.Operator):
    """Check the scene is fit to export, and say what to fix if it is not."""

    bl_idname = "object.rif_validate_level"
    bl_label = "Validate Level"
    bl_options = {"REGISTER"}

    def execute(self, context):
        """Run every pre-export check and report the result.

        The same rules the export warnings use, gathered in one place and run
        on demand, so a level can be audited without exporting it and reading
        the console afterwards.
        """
        from . import placeables  # noqa: F401  (ensures the module loads)

        objs = _gather_meshes(context, False)
        problems = []
        notes = []

        if not objs:
            _report(self, {"ERROR"}, "No mesh objects in the scene to export")
            return {"CANCELLED"}
        notes.append(f"{len(objs)} module(s)")

        # Both ways of linking are checked, so the validator sees what the
        # export would write rather than assuming the Outliner was used.
        hierarchy = _build_hierarchy(objs, "OUTLINER")
        visibility = _visibility_links(objs, "LIST")
        problems.extend(_module_layout_problems(objs, hierarchy,
                                              visibility))
        problems.extend(_sound_layout_problems(context))
        problems.extend(_waypoint_layout_problems(context))
        problems.extend(_path_layout_problems(context, objs))

        # ---- player start
        player_start = _resolve_player_start(context)
        spawns, skipped = _collect_spawns(context, objs)
        mp_spawns = [spec for spec in spawns
                     if spec.get("kind") == placeables.KIND_PLAYER_START_MP]
        if player_start is None and not mp_spawns:
            problems.append(
                "No Player Start and no multiplayer spawn: the level begins at "
                "the world origin, which is usually inside a wall")

        # ---- generators
        generators = [spec for spec in spawns
                      if spec.get("kind") not in (
                          placeables.KIND_PLAYER_START_MP,
                          placeables.KIND_PLAYER_START_SP)]
        notes.append(f"{len(generators)} generator(s)")
        # One copy of the rule, shared with the export warnings.
        problems.extend(_budget_layout_problems(context, spawns))
        if skipped:
            problems.append("Skipped objects: " + ", ".join(sorted(skipped)[:4]))

        # ---- lighting
        world = _scene_world(context)
        if world is None:
            problems.append(
                "No World in the scene, so the level has no ambient light and "
                "every unlit surface renders black")

        for line in notes:
            print(f"[RIF] validate: {line}")
        for problem in problems:
            print(f"[RIF] validate: PROBLEM - {problem}")

        if not problems:
            _report(self, {"INFO"},
                        f"Level looks exportable ({', '.join(notes)})")
            return {"FINISHED"}
        _report(self, {"WARNING"},
                    f"{len(problems)} problem(s): " + " | ".join(problems)[:500])
        return {"FINISHED"}


def _report_spawns(operator, specs, skipped):
    """Report what was exported, and name what was left out."""
    if specs:
        _report(operator, {"INFO"}, f"Exported {len(specs)} AvP generator(s)")
    # Skirmish sets maxAlien to 0, so an alien-only spawn can never be used.
    alien_only = [s for s in specs
                  if s["flags"] & 0x1000
                  and not s["flags"] & (0x800 | 0x2000)]
    if alien_only:
        _report(operator, 
            {"WARNING"},
            f"{len(alien_only)} spawn(s) allow only the alien, which skirmish "
            "forbids - tick Marine or Predator as well")
    if skipped:
        names = ", ".join(name for name, _kind in skipped[:4])
        more = "" if len(skipped) <= 4 else f" (+{len(skipped) - 4} more)"
        _report(operator, 
            {"WARNING"},
            f"{len(skipped)} AvP object(s) not exported yet: {names}{more}")


def _resolve_shape_flags_override(operator):
    """Return the SHPHEAD1 flags override, or None when the builder decides.

    A plain `int(self.shape_flags_override) or None` cannot express the
    value 0, which is exactly the diagnostic case "force flags to zero".
    An explicit enable toggle removes that ambiguity.
    """
    if not operator.override_shape_flags:
        return None
    return int(operator.shape_flags_override)


def _report_bake_quality(operator, baked, base_colour):
    """Warn when the baked lighting will look flat in-game.

    A World ambient that is already bright saturates every vertex, so point
    lights and colour can no longer show through — the most common cause of a
    uniformly lit room. Checking the histogram here turns that into an
    immediate message instead of a surprise inside the game.
    """
    levels = []
    for entry in baked:
        if entry:
            levels.extend(v >> 16 for v in entry)
    if not levels:
        return

    total = len(levels)
    saturated = sum(1 for v in levels if v >= 255)
    base = (base_colour >> 16) & 255
    spread = max(levels) - min(levels)

    if saturated > total * 0.9:
        _report(operator, 
            {"WARNING"},
            f"{saturated}/{total} vertices are fully saturated. World ambient "
            f"is about {base}/255 — lower World > Strength so lights and "
            "colour can show")
    elif spread <= 2:
        _report(operator, 
            {"WARNING"},
            "Baked lighting is flat: only "
            f"{spread} brightness level(s) across {total} vertices. World "
            f"ambient is about {base}/255; lower World > Strength or raise "
            "light Power")
    else:
        _report(operator, 
            {"INFO"},
            f"Baked {total} vertices: brightness {min(levels)}-{max(levels)}, "
            f"World ambient {base}/255")


def _rif_light_settings(light_object):
    """Return the object's RIF Light settings, or None when unavailable."""
    return getattr(light_object, "rif_light", None)


def _light_range_mm(light_object, default_range_m, settings=None):
    """Return the light's falloff radius in RIF millimetres.

    The engine needs an explicit falloff radius (it lights linearly from full
    brightness at the light to zero at `LightRange`) and Blender has no
    equivalent field, so the order is:

      1. `Reach` in Object Properties > RIF Light, in metres;
      2. the legacy `rif_range` custom property, in millimetres (older scenes);
      3. the module default, `DEFAULT_LIGHT_RANGE_MM`.

    Blender's `shadow_soft_size` is deliberately NOT used: it defaults to
    0.25 m and describes shadow softness, not reach. Using it gave every light
    a 250 mm radius and left whole rooms unlit.
    """
    # Reach is no longer a setting: the distance is not something an artist can
    # judge in the viewport, and the per-light value only got in the way. The
    # legacy custom property is still honoured so levels built with older
    # versions of the exporter keep the distances they were authored with.
    getter = getattr(light_object, "get", None)
    legacy = getter(RIF_RANGE_PROP) if getter is not None else None
    if legacy:
        return max(1, int(round(float(legacy))))
    return max(1, int(round(default_range_m * 1000.0)))


def _light_power_watts(light_object, light_data, settings=None):
    """Return the light's effective Power in watts.

    Blender's UI calls it Power (watts); `energy` is the same quantity and is
    what older files carry, so it is used only when `power` is absent. The
    comparison is `is None`, not truthiness: a light deliberately set to
    Power 0 would otherwise be resurrected by the fallback.

    `Brightness Scale` multiplies the result, letting one lamp be lifted or
    dimmed for the game without changing its Blender preview.
    """
    if settings is None:
        settings = _rif_light_settings(light_object)
    power = getattr(light_data, "power", None)
    if power is None:
        power = getattr(light_data, "energy", 0.0)
    watts = float(power or 0.0)
    if settings is not None:
        scale = float(getattr(settings, "brightness_scale", 0.0) or 0.0)
        if scale > 0.0:
            watts *= scale
    return watts


def _scene_world(context):
    """Return the scene's World, or None when it has none.

    Reads `context.scene.world`, NOT the bare `context.world`. The bare member
    is resolved from whichever editor owns the context, and the export runs from
    the file browser, where it comes back None even though the scene has a World
    assigned — which made the exporter warn about a missing World on a scene
    that plainly had one. `context.world` is kept only as a fallback for
    contexts that carry it and no scene.
    """
    scene = getattr(context, "scene", None)
    world = getattr(scene, "world", None) if scene is not None else None
    if world is not None:
        return world
    return getattr(context, "world", None)


def _world_color_strength(context):
    """Return (colour, strength) from Blender's World settings.

    Blender expresses ambient as World > Color plus a World > Strength
    multiplier; together they are the flat term an unlit surface receives.
    Falls back to mid grey at strength 1.0 when the scene has no world.
    """
    world = _scene_world(context)
    if world is None:
        return (0.5, 0.5, 0.5), 1.0
    colour = tuple(getattr(world, "color", (0.5, 0.5, 0.5)))
    strength = float(getattr(world, "strength", 1.0) or 0.0)
    return colour, strength


def _spot_direction_blender(light_object):
    """Return a spot light's forward axis in Blender world space.

    Blender lights point along their local -Z, so the world-space axis is
    `matrix_world` applied to (0, 0, -1).
    """
    return light_object.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))


def _collect_scene_lights(context, default_range_m):
    """Gather every supported light in the scene as RIF light specs.

    Returns (lights, skipped_types, dark_count).

    `location` is written in **RIF millimetres**, the one space every consumer
    agrees on: the baker compares it with `SHPRAWVT` vertex positions, and
    `_make_stdlight` writes it straight into the STDLIGHT record. Emitting
    Blender metres here made both comparisons meaningless — a lamp 10.5 m away
    looked 10.5 units from the origin, so only geometry within a few millimetres
    of the RIF origin was lit. That is the "light bakes only into point 0,0,0"
    symptom. The axis conversion happens exactly once, here.

    Mapping to engine units:
      * Power (watts) -> 16.16 fixed brightness, see
        `lighting.power_to_brightness`.
      * Color -> packed 0x00RRGGBB, the value Objsetup.cpp:3381 turns into
        the engine's RedScale / GreenScale / BlueScale.
      * POINT is omnidirectional; SPOT additionally gets a cone. The engine
        has no cone code (`LFlag_CosSpreadAtten` is defined and never used),
        so a spot's shape exists purely in the bake — its STDLIGHT still gets
        the equivalent spread written for the editor's benefit.

    Blender's `shadow_soft_size` is not used as the falloff radius; see
    `_light_range_mm` for why.
    """
    lights = []
    skipped = set()
    dark = 0
    for obj in context.scene.objects:
        if obj.type != "LIGHT":
            continue
        data = obj.data
        light_type = getattr(data, "type", "POINT")
        if light_type not in ("POINT", "SPOT"):
            skipped.add(light_type)
            continue

        light_range = _light_range_mm(obj, default_range_m)
        brightness = rif_lighting.effective_brightness(
            _light_power_watts(obj, data), light_range)
        if brightness <= 0:
            dark += 1
            continue

        location = obj.matrix_world.translation
        # Convert to RIF millimetres once, so both the STDLIGHT writer and the
        # prelight baker compare positions in the same space as SHPRAWVT.
        bx, by, bz = blender_to_rif_axis(location.x, location.y, location.z)
        spec = {
            "name": obj.name,
            "location": (meters_to_rif(bx), meters_to_rif(by),
                         meters_to_rif(bz)),
            "colour": rif_lighting.colour_to_packed(
                rif_lighting.light_colour(
                    tuple(getattr(data, "color", (1.0, 1.0, 1.0))),
                    getattr(data, "temperature", None),
                    bool(getattr(data, "use_temperature", False)))),
            "brightness": brightness,
            "range": light_range,
        }

        if light_type == "SPOT":
            half_angle = float(getattr(data, "spot_size", 0.785398)) * 0.5
            blend = float(getattr(data, "spot_blend", 0.0) or 0.0)
            cos_inner, cos_outer = rif_lighting.spot_cosines(half_angle, blend)
            spec["direction"] = rif_lighting.direction_to_rif(
                _spot_direction_blender(obj))
            spec["spot_cos_inner"] = cos_inner
            spec["spot_cos_outer"] = cos_outer
            spec["spread"] = rif_lighting.angle_to_spread(half_angle * 2.0)

        lights.append(spec)
    return lights, skipped, dark


def _object_world_rif(obj):
    """Return (location_rif, quaternion) for one Blender mesh object."""
    loc, rot_q, _scale = obj.matrix_world.decompose()
    bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
    return ((meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz)), rot_q)


def _object_world_geometry(obj):
    """Return (vertices_rif, normals_rif) for one object, in WORLD space.

    The mesh's own coordinates are local to the object, so an object standing
    away from the origin has vertices nowhere near the lights, which live in
    world space. Feeding local vertices to a baker holding world-space lights
    made only the part of the geometry crossing the world origin receive any
    light — the symptom being "the lamp only bakes into point 0,0,0".

    Both positions and normals are converted exactly the way the writer
    converts them, so the baked values describe the geometry that ends up in
    the file.
    """
    matrix = obj.matrix_world
    # A quaternion cannot be multiplied by a vector; its rotation matrix can.
    rotation = matrix.to_quaternion().to_matrix()

    verts_rif = []
    normals = []
    for vert in obj.data.vertices:
        world = matrix @ vert.co
        bx, by, bz = blender_to_rif_axis(world.x, world.y, world.z)
        verts_rif.append((meters_to_rif(bx), meters_to_rif(by),
                          meters_to_rif(bz)))

        # Rotating the local normal by the object's rotation puts it in world
        # orientation, the same space as the positions and the lights.
        vec = rotation @ Vector((vert.normal.x, vert.normal.y, vert.normal.z))
        normals.append(blender_to_rif_axis(vec.x, vec.y, vec.z))
    return verts_rif, normals


def _bake_object_lights(objs, lights, base_colour):
    """Bake per-vertex prelight values for every object; returns a list.

    Each entry is either the per-vertex packed intensity list for that object
    or None when the mesh has no vertices.
    """
    baked = []
    for obj in objs:
        verts_rif, normals = _object_world_geometry(obj)
        if not verts_rif:
            baked.append(None)
            continue
        baked.append(rif_lighting.bake_vertex_intensities(
            verts_rif, normals, lights, base_colour=base_colour,
        ))
    return baked


def _resolve_player_start(context):
    """Return the player start position for build_rif_level, or None.

    The position comes from a **Player Start** object placed through
    `Add > AvP`, and from nowhere else.

    Three settings used to describe the same point — an "AABB centre" toggle, a
    fallback position and X/Y/Z fields — resolved in that order. The toggle was
    on by default, so the coordinate fields were dead unless it was switched
    off, and a user who typed a position saw it ignored. One source is easier to
    explain and impossible to shadow.

    Returning None is not fatal: `AVPSTART` is optional, and 20 of the 28 retail
    levels with multiplayer spawns have none. `_export_level` warns instead,
    because a single-player level without it starts at the world origin, which
    is usually inside a wall.
    """
    for obj in context.scene.objects:
        if obj.get(MARKER_PROP) == PLAYER_START_MARKER:
            # World position, not `obj.location`: that one is measured from
            # the parent, and a player start parented to its module -- which is
            # how the visibility hierarchy is built -- would report its offset
            # from that module instead of where it stands. Everything else in
            # this file reads `matrix_world`, and the viewport shows the world
            # position, so a local read is invisible until the level is played.
            world = obj.matrix_world.translation
            return (world.x, world.y, world.z)
    return None


def _report_player_start(operator, player_start, spawns):
    """Warn when a level has no player start, unless it needs none.

    A level whose only way in is a multiplayer spawn legitimately has no
    `AVPSTART` — the engine places players from those. A level with neither
    falls back to the world origin, which is worth saying out loud.
    """
    if player_start is not None:
        return
    multiplayer = [s for s in spawns or () if s.get("flags", 0) & 0x40000000]
    if multiplayer:
        _report(operator, 
            {"INFO"},
            f"No Player Start (single player); {len(multiplayer)} multiplayer "
            "spawn(s) will place players instead")
        return
    _report(operator, 
        {"WARNING"},
        "No Player Start and no multiplayer spawn: the level has no AVPSTART, "
        "so the game starts the player at the world origin. Place a "
        "'Player Start' from Add > AvP for single player, or a "
        "'Multiplayer Spawn' for a network map")


class RIF_PT_light(bpy.types.Panel):
    """RIF Light settings, shown in Object Properties for light objects.

    Exists so the engine-only settings are discoverable: the exporter needs a
    falloff radius that Blender has no field for, and a hidden custom property
    is not a usable answer. Deliberately open by default for the same reason —
    a collapsed panel is a panel nobody finds.
    """

    bl_idname = "RIF_PT_light"
    bl_label = "RIF Light"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        """Only offer the panel for supported light objects."""
        obj = context.object
        return (obj is not None and obj.type == "LIGHT"
                and getattr(obj.data, "type", "") in ("POINT", "SPOT"))

    def draw(self, context):
        """Draw the brightness controls for the active light."""
        obj = context.object
        layout = self.layout
        settings = obj.rif_light

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(settings, "brightness_scale")

        # Show what the exporter will actually write, so the numbers are never
        # a mystery. Imported lazily to keep the panel cheap to draw.
        from .rif import lighting as rif_lighting
        watts = _light_power_watts(obj, obj.data)
        brightness = rif_lighting.effective_brightness(
            watts, _light_range_mm(obj, DEFAULT_LIGHT_RANGE_MM / 1000.0))
        layout.separator()
        column = layout.column(align=True)
        column.label(text=f"Power: {watts:.0f} W", icon="LIGHT")
        column.label(text=f"Engine brightness: {brightness}")
        if brightness >= rif_lighting.BRIGHTNESS_CEILING:
            column.label(text="At the ceiling - lower Power",
                         icon="ERROR")

        layout.separator()
    

class RIF_OT_launch_game(bpy.types.Operator):
    """Start Aliens versus Predator from the configured game folder."""

    bl_idname = "object.rif_launch_game"
    bl_label = "Launch Game"
    bl_description = (
        "Start the game so the exported level can be tested. Uses the "
        "game directory set in preferences and prefers the bin_Crossplay "
        "build, which is the one the game ships and runs")
    bl_options = {"REGISTER"}

    def execute(self, context):
        """Find the executable and launch it detached."""
        import subprocess

        from . import sounds

        root = sounds.game_root(context)
        if not root:
            self.report({"ERROR"},
                        "Set the game directory in preferences first")
            return {"CANCELLED"}

        found = sorted(pathlib.Path(root).rglob("AvP_Classic.exe"))
        if not found:
            self.report({"ERROR"},
                        f"No AvP_Classic.exe under {root}")
            return {"CANCELLED"}
        # Prefer the Crossplay build. It is the one the game ships and runs
        # today; the plain bin build beside it is the older executable.
        found.sort(key=lambda path: ("Crossplay" not in str(path), str(path)))

        executable = found[0]
        try:
            # Detached: the editor must not wait for the game, and the game
            # must outlive the operator.
            subprocess.Popen([str(executable)], cwd=str(executable.parent))
        except OSError as error:
            self.report({"ERROR"}, f"Could not start {executable.name}: {error}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Launched {executable}")
        print(f"[RIF] launched {executable}")
        return {"FINISHED"}


class RIF_MT_add_avp(bpy.types.Menu):
    """AvP submenu inside Object > Add."""

    bl_idname = "RIF_MT_add_avp"
    bl_label = "AvP"

    def draw(self, context):
        """List the AvP categories of placeable objects.

        Categories come first so the menu answers "where does this work"
        before "which chunk is it" - the alien generator types are split from
        the single-player-only ones because a skirmish map can never spawn the
        latter. See `placeables.py`.

        Placing is all this menu does. Validation and launching sit below a rule
        at the end, because they are what happens once the placing is finished.
        """
        from . import placeables

        placeables.menu_draw(self, context)
        # Checking and running are the two things done after placing objects, so
        # they sit below a rule that separates them from the placing itself.
        # Validate comes first: it is the step that catches a level which would
        # not load, and launching is pointless before it passes.
        self.layout.separator()
        self.layout.operator(RIF_OT_validate_level.bl_idname,
                             icon="CHECKMARK")
        self.layout.operator(RIF_OT_launch_game.bl_idname, icon="PLAY")


def _menu_func_add(self, context):
    """Hook the AvP submenu into Object > Add."""
    self.layout.separator()
    self.layout.menu(RIF_MT_add_avp.bl_idname, text="AvP")


def _menu_func_export(self, context):
    """Add the AvP RIF entry to File > Export."""
    self.layout.operator(RIF_OT_export.bl_idname,
                         text="Aliens vs Predator RIF")


_CLASSES = (
    RIF_LightSettings,
    RIF_AddonPreferences,
    RIF_OT_load_shapes,
    RIF_PT_light,
    RIF_OT_export,
    RIF_OT_load_game_textures,
    RIF_OT_build_texture_gallery,
    RIF_OT_clear_texture_library,
    RIF_OT_audition_sound,
    RIF_OT_launch_game,
    RIF_OT_validate_level,
    RIF_MT_add_avp,
)


def register():
    """Register operators, panels, menus and menu hooks."""
    from . import placeables
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    placeables.register_placeables()
    bpy.types.Object.rif_light = bpy.props.PointerProperty(
        type=RIF_LightSettings)
    bpy.types.TOPBAR_MT_file_export.append(_menu_func_export)
    bpy.types.VIEW3D_MT_add.append(_menu_func_add)


def unregister():
    """Unregister everything registered by register()."""
    from . import placeables
    bpy.types.VIEW3D_MT_add.remove(_menu_func_add)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_func_export)
    del bpy.types.Object.rif_light
    placeables.unregister_placeables()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
