"""RIF Exporter — Blender Extension entry point."""
from . import operators


def register():
    """Register all addon operators and menus."""
    operators.register()


def unregister():
    """Unregister all addon operators and menus."""
    operators.unregister()