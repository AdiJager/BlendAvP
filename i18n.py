"""Register the add-on's UI translations from one editable file.

`strings.json` holds every translatable string, keyed by its English text. A
translator edits that file; nothing in the code changes, because Blender
translates what it draws and an add-on only has to supply the table.

Only languages with at least one filled-in entry are registered, so an empty
`pl_PL` costs nothing and stays English until somebody starts. A key left empty
falls back to English on its own, which is why a half-finished language is
usable rather than a source of blanks.

The format is what `bpy.app.translations.register` expects:
`{locale: {("*", english): translation}}`.
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOGUE = Path(__file__).with_name("strings.json")

# Blender's context for add-on interface strings. `*` matches any context,
# which is what an add-on that does not distinguish them wants.
CONTEXT = "*"


def load_catalogue():
    """Return the parsed catalogue, or None when it is absent or unreadable."""
    try:
        with CATALOGUE.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def translations_for(locale):
    """Return `{(context, english): translation}` for one locale.

    Only the entries actually translated: an empty value means "keep English",
    and registering it would create an empty string in the interface.
    """
    catalogue = load_catalogue()
    if not catalogue:
        return {}
    filled = catalogue.get("languages", {}).get(locale, {})
    return {(CONTEXT, english): text
            for english, text in filled.items() if text}


def register():
    """Register every locale the catalogue has translations for.

    Does nothing when the catalogue is missing or no language has been filled
    in, which is the state a fresh checkout is in.
    """
    import bpy

    catalogue = load_catalogue()
    if not catalogue:
        return
    table = {}
    for locale in catalogue.get("languages", {}):
        entries = translations_for(locale)
        if entries:
            table[locale] = entries
    if not table:
        return
    try:
        bpy.app.translations.register(__package__, table)
    except (ValueError, RuntimeError):
        # Blender built without internationalisation: the interface stays
        # English, which is the documented behaviour of register().
        pass


def unregister():
    """Drop the translations again, quietly if they were never registered."""
    import bpy

    try:
        bpy.app.translations.unregister(__package__)
    except (ValueError, RuntimeError):
        pass
