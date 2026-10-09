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
import traceback

import bpy
from bpy.props import (
    StringProperty, FloatProperty, IntProperty, EnumProperty, BoolProperty,
)
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
from .rif.builder import build_rif_bytes, build_rif_multi, build_rif_level
from .rif import lighting as rif_lighting
from .rif.types import blender_to_rif_axis, meters_to_rif
from .rif.texture_library import RIM_LIBRARY_PROP, rim_name_for_material


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


class RIF_LightSettings(bpy.types.PropertyGroup):
    """Per-light RIF settings, editable in Object Properties > RIF Light.

    Stored on the light object so they travel with the scene file and are
    visible without reading a script. Both fields treat 0 as "not set", which
    is what keeps the exporter's defaults working for untouched lights.
    """

    range_m: FloatProperty(
        name="Reach (m)",
        description=(
            "How far this light reaches, in metres. The engine's falloff is "
            "linear: full brightness at the light, zero at this distance. "
            "0 = use the exporter default (25 m).\n"
            "This is the main lever for how large an area one light covers — "
            "and it does NOT change brightness, so two lights of equal Power "
            "look identical up close no matter how their Reach differs."
        ),
        default=0.0, min=0.0, soft_max=100.0, unit="LENGTH",
    )

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

    show_reach: BoolProperty(
        name="Show Reach in Viewport",
        description=(
            "Draw this light's reach as a wireframe in the viewport. Blender "
            "cannot shade an inverse-square lamp like the engine's linear "
            "falloff, so the reach circle is the only honest preview of how "
            "far the light actually reaches"
        ),
        default=True,
    )


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
        default="SHAPE",
    )

    level_name: StringProperty(
        name="Level Name",
        description="Value written into RIFFNAME (LEVEL mode only)",
        default="untitled",
    )

    # Textures come from the materials, not from a setting here. Every material
    # carries the game-side RIM path in its `RIM_PATH` property, filled in by
    # "Load Game Textures"; the exporter collects those paths to build BMPNAMES.
    # A single whole-level override used to live here, which could only ever
    # give every wall the same texture.

    # ------------------------------------------------------ player start

    safe_spawn: BoolProperty(
        name="Safe Spawn (inside geometry AABB)",
        description=(
            "Place AVPSTART at the centre of the selected geometry's "
            "bounding box. In AvP the renderer only creates DISPLAYBLOCKs "
            "for modules reachable from the player's module via VMDARRAY "
            "flood-fill (AllNewModuleHandler in Module.c). Spawning outside "
            "all modules means NO geometry renders. AABB centre guarantees "
            "the player is inside at least one module."
        ),
        default=True,
    )

    include_player_start: BoolProperty(
        name="Include Player Start (UI fallback)",
        description=(
            "Emit SPECLOBJ > AVPSTART using the X/Y/Z fields below. "
            "Ignored when Safe Spawn is on or a Player Start marker exists."
        ),
        default=True,
    )

    player_start_x: FloatProperty(name="Player X", default=0.0)
    player_start_y: FloatProperty(name="Player Y", default=0.0)
    player_start_z: FloatProperty(name="Player Z", default=0.0)

    # -------------------------------------------------------- prelight

    include_prelight: BoolProperty(
        name="Include Prelight (SHPVTINT)",
        description=(
            "Emit a SHPVTINT chunk per object with the per-vertex prelight "
            "data. The loader then sets ShapeFlag_PreLit on the runtime "
            "shape by itself (Projload.cpp:1455), which is what makes "
            "VertexIntensity_Standard_Opt (Kshape.c) read it. Without "
            "prelight and without runtime lights (they need a full "
            "placed-light object system — see bh_light.c) every vertex "
            "renders black. SHPHEAD1.flags stays 0 on purpose: bit 0x1000 "
            "is also SHAPE_FLAG_EXTERNALFILE (SHPCHUNK.HPP:15) and makes "
            "the loader skip module registration — the level would then "
            "render empty."
        ),
        default=True,
    )

    prelight_grey: IntProperty(
        name="Prelight Ambient (no World)",
        description=(
            "Flat per-vertex value used as the ambient term when the scene "
            "has no World assigned: 0 = black, 255 = full white. With a World "
            "present the ambient comes from World > Color x World > Strength "
            "instead and this value is ignored. Point and spot lights are "
            "always taken from the scene's light objects."
        ),
        default=32, min=0, max=255,
    )

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

    # ------------------------------------------------ environment toggles

    include_litscale: BoolProperty(
        name="Include LITSCALE",
        description=(
            "Emit a LITSCALE chunk inside LIGHTSET. Not used by the loader at "
            "all (ApplyPrelightScale is never called), so this only matters "
            "for byte-level comparisons with retail files."
        ),
        default=True,
    )

    include_scale: BoolProperty(
        name="Include ENVSDSCL",
        description="Emit an ENVSDSCL chunk (scale = 1.0)",
        default=False,
    )

    # -------------------------------------------------------- execution

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
            self.report({"ERROR"},
                        f"Export failed: {type(exc).__name__}: {exc} "
                        "(full traceback in the console)")
            return {"CANCELLED"}

        with open(self.filepath, "wb") as handle:
            handle.write(data)
        self.report({"INFO"},
                    f"Wrote {len(data)} bytes to {self.filepath} "
                    f"({self.export_mode})")
        return {"FINISHED"}

    def _export_shape(self, context):
        """Single-mesh export; returns bytes or None on validation failure."""
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Active object must be a mesh")
            return None
        return build_rif_bytes(
            obj.data,
            object_name=obj.name,
            include_prelight=self.include_prelight,
        )

    def _export_collection(self, context):
        """Multi-object export without environment."""
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "No mesh objects selected")
            return None
        return build_rif_multi(
            objs,
            include_prelight=self.include_prelight,
        )

    def _export_level(self, context):
        """Level export: multi-object + REBENVDT skeleton."""
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "No mesh objects selected")
            return None

        player_start = _resolve_player_start(self, objs)

        # Every material slot gets one texture index, and BMPNAMES is built from
        # the same list, so the polygon indices and the chunk can never disagree.
        tex_paths, bitmap_mapping = _build_bitmap_mapping(objs)

        hierarchy = {
            o.name: o.parent.name for o in objs if o.parent is not None
        } or None

        originals = _apply_bitmap_indices(objs, bitmap_mapping)

        # The manual slider packs to 0x00RRGGBB (bits 0..7 blue, 8..15 green,
        flags_override = _resolve_shape_flags_override(self)

        # ---- lighting: World gives the ambient, light objects give the lights
        lights = []
        object_intensities = None
        world_colour, world_strength = _world_color_strength(context)
        world = getattr(context, "world", None)
        if world is not None:
            prelight_colour = rif_lighting.world_ambient_colour(
                world_colour, world_strength)
            ambient_level = rif_lighting.world_ambient_level(
                world_colour, world_strength)
        else:
            # No World assigned: fall back to the manual flat value so a scene
            # without one still exports something sane.
            ambient_level = int(self.prelight_grey)
            prelight_colour = ((ambient_level << 16)
                               | (ambient_level << 8) | ambient_level)

        if self.include_prelight:
            lights, skipped, dark = _collect_scene_lights(
                context, DEFAULT_LIGHT_RANGE_MM / 1000.0)
            if skipped:
                self.report({"WARNING"},
                            "Skipped unsupported light types: "
                            + ", ".join(sorted(skipped)))
            if dark:
                self.report({"WARNING"},
                            f"{dark} light(s) had zero Power and were ignored")
            if not lights:
                self.report({"INFO"},
                            "No point/spot lights found; World ambient only")

            # Bake whenever prelight is on: with no lights this still writes
            # the World ambient into every vertex, which is what the engine
            # reads as the starting colour.
            object_intensities = _bake_object_lights(
                objs, lights, prelight_colour)

            _report_bake_quality(self, object_intensities, prelight_colour)

        try:
            return build_rif_level(
                objs, self.level_name,
                texture_paths=tex_paths,
                player_start=player_start,
                hierarchy=hierarchy,
                include_scale=self.include_scale,
                ambient=ambient_level,
                include_litscale=self.include_litscale,
                include_prelight=self.include_prelight,
                prelight_colour=prelight_colour,
                object_intensities=object_intensities,
                lights=lights,
                emit_runtime_lights=True,
                shape_flags_override=flags_override,
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


def _build_bitmap_mapping(objs):
    """Return ``(paths, mapping)`` linking materials to BMPNAMES indices.

    ``paths`` is the BMPNAMES name list in index order; ``mapping`` maps each
    material (and ``None`` for an empty slot) to the index of its name.

    This is the fix for a real bug: the polygon texture index used to default to
    the material's slot number *on its own object*, while BMPNAMES is numbered
    globally across the level. With two objects the second one therefore
    pointed at the first one's texture. Deriving both from one list keeps them
    consistent by construction.
    """
    paths = []
    index_of = {}
    for obj in objs:
        for mat in obj.data.materials:
            path = _material_texture_path(mat)
            if path not in index_of:
                index_of[path] = len(paths)
                paths.append(path)

    mapping = {}
    for obj in objs:
        for mat in obj.data.materials:
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
        layout.prop(self, "keep_packed")

        row = layout.row()
        row.operator(RIF_OT_load_game_textures.bl_idname, icon="FILE_FOLDER")

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
    bl_options = {"REGISTER"}

    def execute(self, context):
        """Run the whole pipeline: unpack, convert, import, report."""
        from .rif import fastfile, texture_import, texture_tools

        preferences = context.preferences.addons[__package__].preferences
        game_directory = preferences.game_directory.strip()
        if not game_directory:
            self.report({"ERROR"},
                        "Set Game Directory in the add-on preferences first")
            return {"CANCELLED"}

        game_path = Path(game_directory)
        if not (game_path / "fastfile").is_dir():
            self.report({"ERROR"},
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
                game_path, progress=progress)
        except fastfile.FastFileError as exc:
            window_manager.progress_end()
            self.report({"ERROR"}, f"Texture archives: {exc}")
            return {"CANCELLED"}
        except OSError as exc:
            window_manager.progress_end()
            self.report({"ERROR"}, f"Cannot write unpacked textures: {exc}")
            return {"CANCELLED"}

        report, materials = texture_import.import_texture_library(
            texture_tools.unpacked_root_for(game_path), progress=progress)
        window_manager.progress_end()

        if preferences.keep_packed:
            for image in bpy.data.images:
                if image.source == "FILE" and not image.packed_file:
                    try:
                        image.pack()
                    except RuntimeError:
                        pass

        self.report({"INFO"},
                    f"Unpacked {extraction.written} file(s), converted "
                    f"{conversion.converted} texture(s) to PNG, built "
                    f"{len(materials)} material(s)")
        if conversion.failed:
            # Reported, not fatal: one unusual texture must not stop the rest.
            names = ", ".join(p.name for p, _why in conversion.failed[:3])
            self.report({"WARNING"},
                        f"{len(conversion.failed)} texture(s) skipped: {names}")
        if extraction.duplicates:
            self.report({"WARNING"},
                        f"{len(extraction.duplicates)} duplicate name(s) "
                        "between archives")
        return {"FINISHED"}


class RIF_OT_build_texture_gallery(bpy.types.Operator):
    """Create a scene of planes showing every game material."""

    bl_idname = "object.rif_build_texture_gallery"
    bl_label = "Build Texture Gallery"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Make the gallery scene from the imported library."""
        from .rif import texture_import

        materials = [m for m in bpy.data.materials if m.get(RIM_LIBRARY_PROP)]
        if not materials:
            self.report({"WARNING"},
                        "No game materials yet - run Load Game Textures first")
            return {"CANCELLED"}

        existing = bpy.data.scenes.get(texture_import.GALLERY_SCENE)
        if existing is not None:
            bpy.data.scenes.remove(existing)

        scene = texture_import.build_gallery_scene(materials)
        context.window.scene = scene
        self.report({"INFO"},
                    f"Gallery with {len(materials)} plane(s) in scene "
                    f"'{scene.name}'; press 0 for the camera view")
        return {"FINISHED"}


class RIF_OT_clear_texture_library(bpy.types.Operator):
    """Remove every material the texture import created."""

    bl_idname = "object.rif_clear_texture_library"
    bl_label = "Clear Texture Library"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Delete the library materials and any images left unused."""
        from .rif import texture_import

        count = len([m for m in bpy.data.materials
                     if m.get(RIM_LIBRARY_PROP)])
        if not count:
            self.report({"INFO"}, "Texture library is already empty")
            return {"CANCELLED"}
        materials, images = texture_import.delete_library()
        self.report({"INFO"},
                    f"Removed {materials} material(s) and {images} image(s)")
        return {"FINISHED"}


class RIF_OT_add_player_start(bpy.types.Operator):
    """Add an AvP Player Start marker (Empty tagged with rif_marker_type)."""

    bl_idname = "object.rif_add_player_start"
    bl_label = "Player Start"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Create the marker Empty, link it, and make it active."""
        empty = bpy.data.objects.new("PlayerStart", None)
        empty.empty_display_type = "ARROWS"
        empty.empty_display_size = 0.5
        empty[MARKER_PROP] = PLAYER_START_MARKER
        context.collection.objects.link(empty)

        for o in context.selected_objects:
            o.select_set(False)
        empty.select_set(True)
        context.view_layer.objects.active = empty
        return {"FINISHED"}


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
        operator.report(
            {"WARNING"},
            f"{saturated}/{total} vertices are fully saturated. World ambient "
            f"is about {base}/255 — lower World > Strength so lights and "
            "colour can show")
    elif spread <= 2:
        operator.report(
            {"WARNING"},
            "Baked lighting is flat: only "
            f"{spread} brightness level(s) across {total} vertices. World "
            f"ambient is about {base}/255; lower World > Strength or raise "
            "light Power")
    else:
        operator.report(
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
    if settings is None:
        settings = _rif_light_settings(light_object)
    if settings is not None and getattr(settings, "range_m", 0.0) > 0.0:
        return max(1, int(round(settings.range_m * 1000.0)))
    # Blender objects expose the legacy custom property through .get(); other
    # callers (tests, the overlay's selection logic) may not have it.
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


def _world_color_strength(context):
    """Return (colour, strength) from Blender's World settings.

    Blender expresses ambient as World > Color plus a World > Strength
    multiplier; together they are the flat term an unlit surface receives.
    Falls back to mid grey at strength 1.0 when the scene has no world.
    """
    world = getattr(context, "world", None)
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
    loc, rot_q, _scale = obj.matrix_local.decompose()
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


def _resolve_player_start(operator, mesh_objects):
    """Return the player start position for build_rif_level, or None.

    Resolution order:
        1. Safe Spawn — AABB centre of the selected meshes (inside geometry).
        2. Marker     — any object with rif_marker_type == PLAYER_START.
        3. UI fields  — when include_player_start is enabled.
        4. None       — AVPSTART omitted; engine defaults to world origin.
    """
    if operator.safe_spawn and mesh_objects:
        world_coords = [
            obj.matrix_world @ v.co
            for obj in mesh_objects for v in obj.data.vertices
        ]
        if world_coords:
            xs = [c.x for c in world_coords]
            ys = [c.y for c in world_coords]
            zs = [c.z for c in world_coords]
            cx = (min(xs) + max(xs)) * 0.5
            cy = (min(ys) + max(ys)) * 0.5
            cz = (min(zs) + max(zs)) * 0.5   # centre, NOT above
            return (cx, cy, cz)

    for obj in bpy.data.objects:
        if obj.get(MARKER_PROP) == PLAYER_START_MARKER:
            return (obj.location.x, obj.location.y, obj.location.z)

    if operator.include_player_start:
        return (operator.player_start_x,
                operator.player_start_y,
                operator.player_start_z)

    return None


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
        """Draw the reach and brightness controls for the active light."""
        obj = context.object
        layout = self.layout
        settings = obj.rif_light

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(settings, "range_m")
        layout.prop(settings, "brightness_scale")

        # Show what the exporter will actually write, so the numbers are never
        # a mystery. Imported lazily to keep the panel cheap to draw.
        from .rif import lighting as rif_lighting
        watts = _light_power_watts(obj, obj.data)
        brightness = rif_lighting.effective_brightness(
            watts, _light_range_mm(obj, DEFAULT_LIGHT_RANGE_MM / 1000.0,
                                   settings))
        layout.separator()
        column = layout.column(align=True)
        column.label(text=f"Power: {watts:.0f} W", icon="LIGHT")
        column.label(text=f"Engine brightness: {brightness}")
        if brightness >= rif_lighting.BRIGHTNESS_CEILING:
            column.label(text="At the ceiling - raise Reach or lower Power",
                         icon="ERROR")

        layout.separator()
        layout.prop(settings, "show_reach")


class RIF_OT_diagnose_lights(bpy.types.Operator):
    """Print why the reach overlay is or is not drawing anything."""

    bl_idname = "object.rif_diagnose_lights"
    bl_label = "Diagnose RIF Reach Overlay"
    bl_options = {"REGISTER"}

    def execute(self, context):
        """Report handler state, light settings and the last draw error."""
        from . import gizmos
        report = gizmos.describe_state(context)
        for line in report.splitlines():
            print(f"[RIF] {line}")
        # Also surface it in the UI: the console is easy to miss.
        failed = gizmos.LAST_ERROR is not None
        self.report({"ERROR" if failed else "INFO"},
                    report.replace("\n", " | ")[:500])
        return {"FINISHED"}


class RIF_OT_select_lights(bpy.types.Operator):
    """Select every supported light in the scene, so the panel is reachable."""

    bl_idname = "object.rif_select_lights"
    bl_label = "Select RIF Lights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        """Select all POINT and SPOT lights and report how many were found."""
        count = 0
        for obj in context.scene.objects:
            if obj.type != "LIGHT":
                continue
            if getattr(obj.data, "type", "") not in ("POINT", "SPOT"):
                continue
            obj.select_set(True)
            count += 1
        if not count:
            self.report({"WARNING"},
                        "No POINT or SPOT lights in this scene — RIF Light "
                        "settings live on those objects")
            return {"CANCELLED"}
        self.report({"INFO"},
                    f"Selected {count} light(s); RIF Light is in Object "
                    "Properties")
        return {"FINISHED"}


class RIF_MT_add_avp(bpy.types.Menu):
    """AvP submenu inside Object > Add."""

    bl_idname = "RIF_MT_add_avp"
    bl_label = "AvP"

    def draw(self, context):
        """List AvP-specific objects and helpers the user can reach."""
        self.layout.operator(RIF_OT_add_player_start.bl_idname,
                             icon="EMPTY_ARROWS")
        self.layout.operator(RIF_OT_select_lights.bl_idname,
                             icon="LIGHT")
        self.layout.operator(RIF_OT_diagnose_lights.bl_idname,
                             icon="QUESTION")


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
    RIF_PT_light,
    RIF_OT_export,
    RIF_OT_load_game_textures,
    RIF_OT_build_texture_gallery,
    RIF_OT_clear_texture_library,
    RIF_OT_add_player_start,
    RIF_OT_select_lights,
    RIF_OT_diagnose_lights,
    RIF_MT_add_avp,
)


def register():
    """Register operators, panels, menus and menu hooks."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.rif_light = bpy.props.PointerProperty(
        type=RIF_LightSettings)
    bpy.types.TOPBAR_MT_file_export.append(_menu_func_export)
    bpy.types.VIEW3D_MT_add.append(_menu_func_add)
    from . import gizmos
    gizmos.register_overlay()


def unregister():
    """Unregister everything registered by register()."""
    from . import gizmos
    gizmos.unregister_overlay()
    bpy.types.VIEW3D_MT_add.remove(_menu_func_add)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_func_export)
    del bpy.types.Object.rif_light
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)