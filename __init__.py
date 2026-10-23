"""Aliens vs Predator RIF Exporter — Blender Extension entry point."""
from . import i18n, operators


def register():
    """Register all addon operators and menus."""
    operators.register()
    # Translations last: a language table is only useful once the interface it
    # describes exists, and registering it first would have nothing to name.
    i18n.register()


def unregister():
    """Unregister all addon operators and menus."""
    i18n.unregister()
    operators.unregister()
