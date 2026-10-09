"""Building a Blender material library from the game's textures.

Pure helpers only -- everything here works on plain values so it can be tested
without Blender. The parts that touch ``bpy`` live in ``texture_import.py``.

Two conventions matter and are decided here:

**Material names are the logical RIM paths**, for example
``Envrnmts\\Battle\\Cliff04.RIM``. The archive stores its entries as
``graphics\\Envrnmts\\Battle\\Cliff04.RIM``; the leading ``graphics`` folder is
the archive's own root and is not part of what the game looks up, so it is
stripped. Using the full path rather than the bare file name is not cosmetic:
the game resolves a texture by basename but narrows the search using the
directory prefix, and 109 basenames in the retail data hold different art in
different directories. All 1028 logical paths are distinct and the longest is
59 characters, inside Blender's 63-character material name limit.

**Every material carries the game-side name separately**, in the ``RIM_PATH``
custom property, so the exporter never has to parse it back out of a name that
Blender may have had to shorten.
"""
from __future__ import annotations

import math
import re

# Custom property holding the exact name to write into BMPNAMES.
RIM_PATH_PROP = "RIM_PATH"
# Custom property holding the PNG the material was built from.
RIM_PNG_PROP = "RIM_PNG"
# Custom property marking a material as part of the game library.
RIM_LIBRARY_PROP = "RIF_GAME_TEXTURE"

_ARCHIVE_ROOT = re.compile(r"^graphics[\\/]+", re.IGNORECASE)
_SEPARATORS = re.compile(r"[\\/]+")

# Blender truncates material names beyond this many bytes.
BLENDER_NAME_LIMIT = 63

# Backface culling: the engine draws single-sided polygons with no culling, so
# a surface is visible from both sides. Enabling `use_backface_culling` matches
# that in the viewport and in EEVEE, which is what the user asked for.
BACKFACE_CULLING_DEFAULT = True

# The 1999 engine has neither a specular term nor a roughness parameter: every
# surface is diffuse and nothing reflects. These Principled BSDF values
# reproduce that, so the Blender preview does not promise highlights the game
# will never draw.
SPECULAR_LEVEL = 0.0
ROUGHNESS = 1.0
METALLIC = 0.0


def logical_name(archive_path):
    """Return the game-side name for a texture stored under ``graphics\\``.

    ``graphics\\Envrnmts\\Battle\\Cliff04.RIM`` becomes
    ``Envrnmts\\Battle\\Cliff04.RIM``. Anything without that prefix is returned
    unchanged apart from separator normalisation, so an unexpected layout still
    produces something usable rather than an empty string.
    """
    if not archive_path:
        raise ValueError("empty texture path")
    # A replacement string of "\\" is a bad escape in `re`; the doubled form is
    # the literal backslash.
    normalised = _SEPARATORS.sub("\\\\", archive_path.strip())
    stripped = _ARCHIVE_ROOT.sub("", normalised)
    return stripped.lstrip("\\")


def rim_name_for_material(mat, fallback="dummy.RIM"):
    """Return the BMPNAMES name a material slot contributes, or ``fallback``.

    Single source of truth for both the exporter operator and the chunk builder.
    They must agree exactly: the builder looks this name up in the list the
    operator produced, so any difference in normalisation silently changes which
    texture a polygon points at.

    The `graphics\\` prefix is stripped because the loader already prefixes the
    textures root (`FirstTex_Directory = "Graphics"`, Projload.cpp:97); leaving
    it in makes every lookup miss and the surface stop being drawn. A material
    without `RIM_PATH` falls back to its own name so hand-made materials still
    export something, and an empty slot gets `fallback` because BMPNAMES must
    never be empty.
    """
    if mat is None:
        return fallback
    raw = mat.get(RIM_PATH_PROP) or mat.name or fallback
    try:
        return logical_name(str(raw))
    except ValueError:
        return fallback


def material_name(logical):
    """Return the Blender material name for a logical RIM path.

    The logical path is already unique across the whole game, so no
    disambiguation is needed. If it were ever too long for Blender, the tail is
    preserved rather than the head: the file name is what identifies a texture
    at a glance, and the full value stays in the ``RIM_PATH`` property either
    way.
    """
    if len(logical) <= BLENDER_NAME_LIMIT:
        return logical
    keep = BLENDER_NAME_LIMIT - 3
    return "..." + logical[-keep:]


def square_grid(count, columns=None):
    """Return ``(columns, rows)`` for laying out ``count`` items.

    A square-ish grid keeps a thousand textures navigable instead of forming
    one very long strip.
    """
    if count <= 0:
        return 0, 0
    if columns is None:
        columns = max(1, int(math.ceil(math.sqrt(count))))
    columns = max(1, int(columns))
    rows = int(math.ceil(count / columns))
    return columns, rows


def gallery_placements(count, spacing, columns=None):
    """Yield ``(x, y)`` centres for a grid of ``count`` planes.

    The grid is centred on the origin so it lands in view when the scene is
    created, and it lies in the XY plane because that is where a Blender floor
    and camera naturally sit.
    """
    columns, rows = square_grid(count, columns)
    if not columns:
        return
    x_offset = (columns - 1) * spacing / 2.0
    y_offset = (rows - 1) * spacing / 2.0
    for index in range(count):
        column = index % columns
        row = index // columns
        yield (column * spacing - x_offset, row * spacing - y_offset)


def describe_library(materials):
    """Return a short human summary of an imported material library.

    Takes an iterable of ``(name, rim_path)`` pairs so it works without Blender.
    """
    entries = list(materials)
    longest = max((len(path) for _name, path in entries), default=0)
    return (f"{len(entries)} material(s), longest RIM path {longest} chars")
