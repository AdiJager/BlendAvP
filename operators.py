"""Blender operators and menus for RIF export and AvP marker creation.

Exports a Blender scene as an AvP (1999) .rif file in one of three modes:
    SHAPE       — single active mesh, identity transform
    COLLECTION  — all selected meshes, no environment
    LEVEL       — all selected meshes + REBENVDT level skeleton

Also registers an "AvP" submenu under Object > Add so the user can drop
in game-specific marker objects (currently only Player Start). Markers
are plain Blender Empties tagged with the custom property
`rif_marker_type`; the exporter scans for them in LEVEL mode.

S6 F6 fixes:

  H45 — Safe Spawn puts the player at the AABB centre (inside geometry).
        AllNewModuleHandler (Module.c) only creates DISPLAYBLOCKs for
        modules reachable from the player's module; spawning outside
        all modules means no geometry renders.

  H53 — Texture Override field. In chnkload.cpp::copy_to_shapeheader,
        any textured polygon whose bitmap_idx does not resolve to a
        loaded image (local_tex_index_nos[texno] == -1) is degraded to
        I_Polyline with iflag_nolight — an invisible one-pixel line.
        By default BMPNAMES entries carry the Blender material name,
        which never matches a real .RIM file in the game's .ffl
        archives, so every polygon was being degraded. Filling in
        Texture Override with an existing .RIM path forces all
        polygons to reference bitmap_idx 0 → that entry.
"""
import bpy
from bpy.props import (
    StringProperty, FloatProperty, IntProperty, EnumProperty, BoolProperty,
)
from bpy_extras.io_utils import ExportHelper
from .rif.builder import build_rif_bytes, build_rif_multi, build_rif_level


MARKER_PROP = "rif_marker_type"
PLAYER_START_MARKER = "PLAYER_START"
BITMAP_IDX_PROP = "rif_bitmap_idx"


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

    # -------------------------------------------------------- lighting

    ambient_level: IntProperty(
        name="Ambient",
        description="AMBIENCE value (0-255); 255 = fully lit",
        default=255, min=0, max=255,
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
        return build_rif_bytes(obj.data, object_name=obj.name)

    def _export_collection(self, context):
        """Multi-object export without environment."""
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "No mesh objects selected")
            return None
        return build_rif_multi(objs)

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
        try:
            return build_rif_level(
                objs, self.level_name,
                texture_paths=tex_paths,
                player_start=player_start,
                hierarchy=hierarchy,
                include_scale=self.include_scale,
                ambient=self.ambient_level,
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