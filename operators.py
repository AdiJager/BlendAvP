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
        left entire rooms unlit. It is now `rif_range` when set, otherwise
        25 m.
"""
import bpy
from bpy.props import (
    StringProperty, FloatProperty, IntProperty, EnumProperty, BoolProperty,
)
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
from .rif.builder import build_rif_bytes, build_rif_multi, build_rif_level
from .rif import lighting as rif_lighting
from .rif.types import blender_to_rif_axis, meters_to_rif


MARKER_PROP = "rif_marker_type"
PLAYER_START_MARKER = "PLAYER_START"
BITMAP_IDX_PROP = "rif_bitmap_idx"

# Custom properties an artist can set on a Blender light object to control
# exactly what reaches the game, overriding the Blender-side values.
RIF_RANGE_PROP = "rif_range"        # float, RIF millimetres
RIF_ENERGY_PROP = "rif_energy"      # float, 1.0 == engine ONE_FIXED

# AvP's own level editor shipped lights with ranges around 3.6-13 m and the
# game's largest reference value is 20000 mm; anything beyond that is wasted.
DEFAULT_LIGHT_RANGE_MM = 25000


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

    # ------------------------------------------------------ texture fix

    texture_override: StringProperty(
        name="Texture Override",
        description=(
            "Optional: path of a .RIM texture that exists in the game's "
            ".ffl archives. When set, all polygons in the export reference "
            "bitmap_idx 0 pointing at this single RIM. Without a real RIM "
            "name, chnkload.cpp::copy_to_shapeheader degrades every "
            "textured polygon to I_Polyline with iflag_nolight — an "
            "invisible one-pixel line — which makes the geometry look "
            "empty even though modules and collisions exist. Example: "
            "Envrnmts\\Battle\\timegg.RIM"
        ),
        default="",
    )

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
        except Exception as exc:
            self.report({"ERROR"}, f"Export failed: {exc}")
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

        # Collect texture path candidates.
        if self.texture_override.strip():
            tex_paths = [self.texture_override.strip()]
            force_bitmap_idx = 0
        else:
            tex_paths = []
            seen = set()
            for o in objs:
                for mat in o.data.materials:
                    if mat and mat.name not in seen:
                        seen.add(mat.name)
                        tex_paths.append(mat.name)
            force_bitmap_idx = None

        hierarchy = {
            o.name: o.parent.name for o in objs if o.parent is not None
        } or None

        # Temporarily force rif_bitmap_idx on every material so that the
        # polygon colour field points at entry 0 in BMPNAMES.
        originals = []
        if force_bitmap_idx is not None:
            for o in objs:
                for mat in o.data.materials:
                    if mat is None:
                        continue
                    originals.append((mat, mat.get(BITMAP_IDX_PROP)))
                    mat[BITMAP_IDX_PROP] = force_bitmap_idx

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
            for mat, orig in originals:
                if orig is None:
                    if BITMAP_IDX_PROP in mat:
                        del mat[BITMAP_IDX_PROP]
                else:
                    mat[BITMAP_IDX_PROP] = orig


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


def _light_range_mm(light_object, default_range_m):
    """Return the light's falloff radius in RIF millimetres.

    The engine needs an explicit falloff radius (it lights linearly from full
    brightness at the light to zero at `LightRange`), and Blender has no
    equivalent setting, so:

      1. the `rif_range` custom property on the light object, in millimetres;
      2. otherwise the module default (DEFAULT_LIGHT_RANGE_MM).

    Blender's `shadow_soft_size` is deliberately NOT used: it defaults to
    0.25 m and describes shadow softness, not reach. Using it produced a
    250 mm light radius, which left whole rooms unlit.
    """
    explicit = light_object.get(RIF_RANGE_PROP)
    if explicit:
        return max(1, int(round(float(explicit))))
    return max(1, int(round(default_range_m * 1000.0)))


def _light_power_watts(light_object, light_data):
    """Return the light's Power in watts, honouring the `rif_energy` override.

    Blender's UI exposes this as Power (watts); `energy` is the same quantity
    and is what older files carry, so it is used only when `power` is absent.
    The check is `is None`, not truthiness: a light deliberately set to
    Power 0 would otherwise be resurrected by the fallback.
    """
    explicit = light_object.get(RIF_ENERGY_PROP)
    if explicit:
        return (float(explicit)
                * rif_lighting.BLENDER_WATTS_PER_ONE_FIXED)
    power = getattr(light_data, "power", None)
    if power is None:
        power = getattr(light_data, "energy", 0.0)
    return float(power or 0.0)


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

    Returns (lights, skipped_types, dark_count). Positions stay in Blender
    metres on purpose: `_make_stdlight` applies the same axis conversion and
    scaling that object locations go through, so converting here as well
    would rotate every light twice. The baker does its own conversion.

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

        brightness = rif_lighting.power_to_brightness(
            _light_power_watts(obj, data))
        if brightness <= 0:
            dark += 1
            continue

        location = obj.matrix_world.translation
        spec = {
            "name": obj.name,
            "location": (location.x, location.y, location.z),
            "colour": rif_lighting.colour_to_packed(
                tuple(getattr(data, "color", (1.0, 1.0, 1.0)))),
            "brightness": brightness,
            "range": _light_range_mm(obj, default_range_m),
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


def _bake_object_lights(objs, lights, base_colour):
    """Bake per-vertex prelight values for every object; returns a list.

    Each entry is either the per-vertex packed intensity list for that object
    or None when the mesh has no vertices. Vertices and normals are converted
    the same way the writer converts them, so the baked values describe the
    geometry that actually ends up in the file.
    """
    baked = []
    for obj in objs:
        mesh = obj.data
        normals = []
        # Rotating the local normals by the object's own rotation puts them in
        # world orientation, which is the space the lights live in.
        rot = obj.matrix_local.to_quaternion()
        for vert in mesh.vertices:
            n = vert.normal
            vec = rot @ Vector((n.x, n.y, n.z))
            normals.append(blender_to_rif_axis(vec.x, vec.y, vec.z))

        verts_rif = []
        for vert in mesh.vertices:
            co = vert.co
            bx, by, bz = blender_to_rif_axis(co.x, co.y, co.z)
            verts_rif.append((meters_to_rif(bx), meters_to_rif(by),
                              meters_to_rif(bz)))

        if not verts_rif:
            baked.append(None)
            continue

        loc_rif, _rot_q = _object_world_rif(obj)
        baked.append(rif_lighting.bake_for_object(
            loc_rif, verts_rif, normals, lights, base_colour=base_colour,
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


class RIF_MT_add_avp(bpy.types.Menu):
    """AvP submenu inside Object > Add."""

    bl_idname = "RIF_MT_add_avp"
    bl_label = "AvP"

    def draw(self, context):
        """List AvP-specific objects the user can drop into the scene."""
        self.layout.operator(RIF_OT_add_player_start.bl_idname,
                             icon="EMPTY_ARROWS")


def _menu_func_add(self, context):
    """Hook the AvP submenu into Object > Add."""
    self.layout.separator()
    self.layout.menu(RIF_MT_add_avp.bl_idname, text="AvP")


def _menu_func_export(self, context):
    """Add the AvP RIF entry to File > Export."""
    self.layout.operator(RIF_OT_export.bl_idname,
                         text="Aliens vs Predator RIF")


_CLASSES = (
    RIF_OT_export,
    RIF_OT_add_player_start,
    RIF_MT_add_avp,
)


def register():
    """Register operators, menus and menu hooks."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_func_export)
    bpy.types.VIEW3D_MT_add.append(_menu_func_add)


def unregister():
    """Unregister everything registered by register()."""
    bpy.types.VIEW3D_MT_add.remove(_menu_func_add)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_func_export)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)