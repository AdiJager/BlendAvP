"""Blender operators for RIF export."""
import bpy
from bpy.props import StringProperty, FloatProperty
from bpy_extras.io_utils import ExportHelper
from .rif.builder import build_rif_bytes


class RIF_OT_export(bpy.types.Operator, ExportHelper):
    """Export the active mesh as a REBINFF2 .rif file."""

    bl_idname = "export_scene.rif"
    bl_label = "Export RIF"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".rif"
    filter_glob: StringProperty(default="*.rif", options={"HIDDEN"})

    scale: FloatProperty(
        name="Scale",
        description="Blender units per RIF unit (rif_int = blender * 65536 / scale)",
        default=1.0,
        min=1e-4,
    )

    def execute(self, context):
        """Run export on the active mesh object."""
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Active object must be a mesh")
            return {"CANCELLED"}

        try:
            data = build_rif_bytes(obj.data, scale=self.scale,
                                   object_name=obj.name)
        except Exception as exc:
            self.report({"ERROR"}, f"Export failed: {exc}")
            return {"CANCELLED"}

        with open(self.filepath, "wb") as handle:
            handle.write(data)

        self.report({"INFO"},
                    f"Wrote {len(data)} bytes to {self.filepath}")
        return {"FINISHED"}


def _menu_func_export(self, context):
    """Add RIF entry to File > Export menu."""
    self.layout.operator(RIF_OT_export.bl_idname, text="RIF (.rif)")


_CLASSES = (RIF_OT_export,)


def register():
    """Register operator class and export menu hook."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_func_export)


def unregister():
    """Unregister operator class and menu hook."""
    bpy.types.TOPBAR_MT_file_export.remove(_menu_func_export)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)