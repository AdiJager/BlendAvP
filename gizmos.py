"""Viewport overlay showing how far each RIF light reaches.

The engine's falloff is linear — full brightness at the lamp, zero exactly at
its range — while Blender can only show an inverse-square falloff. No light
*type* can bridge that, because EEVEE does not expose light falloff at all
(the old Linear/Quadratic attenuation sliders the engine's model would need
were removed from Blender's lamps). What the artist cannot read off the shaded
viewport is therefore the *reach*, so this module draws it explicitly:

  * three rings at the reach distance, one per body axis, forming a wireframe
    sphere;
  * two rings per axis inside it, at one third and two thirds of the reach, so
    the sphere reads as a volume rather than a flat circle;
  * for spots, the cone instead of the sphere, from the lamp out to its reach.

Unselected lights are drawn faintly, the active one solid, and lights with no
reach limit draw nothing (there is nothing to delimit).

Everything is computed in the overlay's own cache and re-uploaded only when a
light's transform or reach changes, because `draw` runs on every redraw.
"""
import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

# Segment count for a full circle. 64 is smooth at typical zoom levels without
# making the vertex count matter.
CIRCLE_SEGMENTS = 64

# Ring radii as fractions of the reach: the full distance plus two inner rings.
RING_FRACTIONS = (1.0, 2.0 / 3.0, 1.0 / 3.0)

_SHADER = None

# handler -> (args, region_type) so unregister can remove exactly what it added.
_DRAW_HANDLE = None


def _shader():
    """Return the shared polyline shader, built once on first use."""
    global _SHADER
    if _SHADER is None:
        _SHADER = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    return _SHADER


def unit_circle(segments=CIRCLE_SEGMENTS):
    """Return a closed unit circle in the XY plane as ((x, y, z), ...)."""
    points = []
    for i in range(segments + 1):
        angle = 2.0 * math.pi * i / segments
        points.append((math.cos(angle), math.sin(angle), 0.0))
    return tuple(points)


def rotate(rotation, point):
    """Apply a 3x3 rotation given as nested tuples to a 3-element point.

    Spelled out rather than using `rotation @ point`: Blender's `Matrix`
    supports `@` only against a Vector, and the `TypeError` it raised there
    killed the whole draw handler, which Blender then swallowed silently.
    """
    x, y, z = point
    return (
        rotation[0][0] * x + rotation[0][1] * y + rotation[0][2] * z,
        rotation[1][0] * x + rotation[1][1] * y + rotation[1][2] * z,
        rotation[2][0] * x + rotation[2][1] * y + rotation[2][2] * z,
    )


def _rotation_x(angle):
    """Return the 3x3 rotation about X as nested tuples."""
    c, s = math.cos(angle), math.sin(angle)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _rotation_y(angle):
    """Return the 3x3 rotation about Y as nested tuples."""
    c, s = math.cos(angle), math.sin(angle)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _axis_rotations():
    """Return the three rotations placing the unit circle on each body axis."""
    return (
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),  # XY plane
        _rotation_x(math.pi / 2.0),                            # XZ plane
        _rotation_y(math.pi / 2.0),                            # YZ plane
    )


def _circle_coords(rotation, radius, segments=CIRCLE_SEGMENTS):
    """Return a closed circle of `radius` in the plane given by `rotation`."""
    return tuple(
        tuple(component * radius for component in rotate(rotation, point))
        for point in unit_circle(segments)
    )


def sphere_lines(reach):
    """Return the wireframe sphere's line strips for a given reach.

    Pure geometry, no GPU: three axis planes, each at the full reach and at
    two inner fractions, so the sphere reads as a volume.
    """
    lines = []
    for rotation in _axis_rotations():
        for fraction in RING_FRACTIONS:
            lines.append(_circle_coords(rotation, reach * fraction))
    return lines


def cone_lines(reach, spot_size):
    """Return (rim, ribs) for a spot cone of the given reach and spot size.

    `spot_size` is Blender's full opening angle. The rim is a closed circle at
    `-reach` on the light's local Z (Blender lights point along -Z) and the
    ribs are straight lines from the lamp to a subset of the rim.
    """
    radius = reach * math.tan(float(spot_size) * 0.5)
    rim = tuple((x, y, z - reach)
                for x, y, z in _circle_coords(_axis_rotations()[0], radius))
    step = max(1, CIRCLE_SEGMENTS // 8)
    ribs = []
    for point in rim[::step]:
        ribs.append((0.0, 0.0, 0.0))
        ribs.append(point)
    return rim, tuple(ribs)


class _LineCache:
    """One light's geometry, rebuilt only when it actually changes."""

    def __init__(self):
        self.key = None
        self.batches = []

    def rebuild(self, light_object, reach, key=None):
        """Rebuild the ring (or cone) geometry for one light, in world space.

        Coordinates are baked through the object's matrix here, once per
        change, rather than per frame — the batch is uploaded and drawn as-is.
        """
        self.batches = []
        matrix = light_object.matrix_world
        if getattr(light_object.data, "type", "POINT") == "SPOT":
            rim, ribs = cone_lines(
                reach, getattr(light_object.data, "spot_size", math.pi / 4.0))
            self.batches.append(self._batch(
                "LINE_STRIP", tuple(to_world(matrix, p) for p in rim)))
            if ribs:
                self.batches.append(self._batch(
                    "LINES", tuple(to_world(matrix, p) for p in ribs)))
        else:
            for coords in sphere_lines(reach):
                self.batches.append(self._batch(
                    "LINE_STRIP", tuple(to_world(matrix, p) for p in coords)))
        self.key = key if key is not None else self._make_key(light_object,
                                                              reach)

    @staticmethod
    def _batch(kind, coords):
        """Build one drawable batch, or None when there is nothing to draw."""
        if not coords:
            return None
        return batch_for_shader(_shader(), kind, {"pos": coords})

    @staticmethod
    def _make_key(light_object, reach):
        """Return a value that changes whenever the drawing must be redone."""
        matrix = light_object.matrix_world
        return (reach,
                getattr(light_object.data, "type", "POINT"),
                round(float(getattr(light_object.data, "spot_size", 0.0)), 4),
                tuple(round(v, 4) for row in matrix for v in row))


_LINE_CACHES = {}
_PRUNE_COUNTDOWN = 240


def _maybe_prune_caches():
    """Drop cache entries for deleted lights, occasionally rather than always.

    Hashing every object name on each redraw would be wasteful; every couple of
    hundred frames is enough to stop the cache growing after deletions.
    """
    global _PRUNE_COUNTDOWN
    _PRUNE_COUNTDOWN -= 1
    if _PRUNE_COUNTDOWN > 0:
        return
    _PRUNE_COUNTDOWN = 240
    _prune_caches()


def _light_reach(light_object):
    """Return the light's reach in metres, or 0 when it has no limit."""
    settings = getattr(light_object, "rif_light", None)
    return float(getattr(settings, "range_m", 0.0) or 0.0)


def to_world(matrix, point):
    """Transform a local point into world space with a Blender 4x4 matrix.

    Done explicitly rather than through `gpu.matrix.push()/multiply_matrix()`,
    because those calls turned out to be the reason the overlay drew nothing at
    all on Blender 4.2: a draw-handler exception is swallowed by Blender, so
    the failure was invisible. Transforming the (few hundred) vertices here is
    cheap and leaves no global GPU state to get wrong.
    """
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def collect_drawable(objects, active, default_range_m):
    """Return [(object, reach_m, is_active)] for the lights worth drawing.

    Pure selection logic, separated from all GPU work so it can be tested
    without a context and so "why is nothing drawn" has exactly one place to
    look. A light is skipped when it is hidden, is not a POINT/SPOT, has the
    overlay switched off, or has no reach to delimit.
    """
    from .operators import _light_range_mm

    drawable = []
    for obj in objects:
        if getattr(obj, "type", None) != "LIGHT":
            continue
        try:
            if not obj.visible_get():
                continue
        except (AttributeError, RuntimeError):
            # A context-free call can refuse visible_get(); treat it as visible
            # rather than silently drawing nothing.
            pass
        if getattr(obj.data, "type", "POINT") not in ("POINT", "SPOT"):
            continue
        settings = getattr(obj, "rif_light", None)
        if settings is not None and not getattr(settings, "show_reach", True):
            continue
        reach = _light_range_mm(obj, default_range_m, settings) / 1000.0
        if reach <= 0.0:
            continue
        drawable.append((obj, reach, obj is active))
    return drawable


# Set when the draw handler raises, so the Add-on preferences (and the
# console) can say what went wrong instead of the overlay failing silently.
LAST_ERROR = None


def draw_reach_overlay():
    """Draw the reach wireframe for every light in the current view layer."""
    global LAST_ERROR
    try:
        _draw_reach_overlay()
        LAST_ERROR = None
    except Exception as exc:  # noqa: BLE001 - a draw handler must never raise
        # Swallowing this silently is what made the overlay look like it simply
        # "did nothing", so keep the reason around where it can be read.
        LAST_ERROR = f"{type(exc).__name__}: {exc}"


def _draw_reach_overlay():
    """Do the drawing; separated so failures can be recorded by the caller."""
    from .operators import DEFAULT_LIGHT_RANGE_MM

    context = bpy.context
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return

    drawable = collect_drawable(view_layer.objects,
                                getattr(context, "object", None),
                                DEFAULT_LIGHT_RANGE_MM / 1000.0)
    if not drawable:
        return

    _maybe_prune_caches()
    shader = _shader()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])

    # Depth-tested pass first so the rings sit in the scene, then a second pass
    # without depth so the reach is still readable through geometry. Each pass
    # is isolated: a state call Blender rejects must cost one pass, not the
    # whole overlay, because a raising handler is swallowed without a word.
    errors = []
    for depth_test in ("LESS_EQUAL", "NONE"):
        try:
            gpu.state.depth_test_set(depth_test)
        except (TypeError, ValueError) as exc:
            errors.append(f"depth_test_set({depth_test}): {exc}")
            continue
        try:
            gpu.state.blend_set("ALPHA")
        except (TypeError, ValueError) as exc:
            errors.append(f"blend_set: {exc}")
        _draw_lights(shader, drawable, depth_test == "NONE")

    _restore_gpu_state()
    if errors:
        raise RuntimeError("; ".join(errors))


def _draw_lights(shader, drawable, translucent_pass):
    """Draw every light's cached geometry in the current depth state."""
    for obj, reach, is_active in drawable:
        cache = _LINE_CACHES.get(obj.name)
        if cache is None:
            cache = _LineCache()
            _LINE_CACHES[obj.name] = cache
        key = _LineCache._make_key(obj, reach)
        if cache.key != key:
            cache.rebuild(obj, reach, key)

        colour = tuple(getattr(obj.data, "color", (1.0, 1.0, 1.0)))
        alpha = 0.85 if is_active else 0.28
        if translucent_pass and not is_active:
            alpha *= 0.5
        shader.uniform_float("lineWidth", 2.0 if is_active else 1.0)
        shader.uniform_float("color",
                             (colour[0], colour[1], colour[2], alpha))
        for batch in cache.batches:
            if batch is not None:
                batch.draw(shader)


def _restore_gpu_state():
    """Put the GPU state back regardless of what the passes did."""
    for setter, value in ((gpu.state.depth_test_set, "NONE"),
                          (gpu.state.blend_set, "NONE")):
        try:
            setter(value)
        except (TypeError, ValueError):
            pass


def describe_state(context=None):
    """Return a human-readable report of why the overlay is or is not drawing.

    Built for the "I toggled it and nothing happened" case, which is invisible
    otherwise: a draw handler that raises is swallowed by Blender, and a
    handler that was never registered looks exactly the same.
    """
    from .operators import DEFAULT_LIGHT_RANGE_MM, _light_range_mm

    context = context or bpy.context
    lines = []

    registered = _DRAW_HANDLE is not None
    lines.append(f"handler registered : {'yes' if registered else 'NO'}")

    scene = getattr(context, "scene", None)
    layers = list(getattr(scene, "view_layers", []) or [])
    view_layer = getattr(context, "view_layer", None)
    objects = list(getattr(view_layer, "objects", []) or [])
    lights = [o for o in objects if getattr(o, "type", None) == "LIGHT"]
    lines.append(f"objects in view layer: {len(objects)} "
                 f"({len(lights)} light(s), {len(layers)} view layer(s))")

    drawable = collect_drawable(objects, getattr(context, "object", None),
                                DEFAULT_LIGHT_RANGE_MM / 1000.0)
    lines.append(f"lights with a reach to draw: {len(drawable)}")

    for obj in lights:
        settings = getattr(obj, "rif_light", None)
        if settings is None:
            lines.append(f"  - {obj.name}: NO RIF LIGHT SETTINGS "
                         "(re-enable the add-on)")
            continue
        reach = _light_range_mm(obj, DEFAULT_LIGHT_RANGE_MM / 1000.0,
                                settings)
        shown = getattr(settings, "show_reach", True)
        lines.append(
            f"  - {obj.name}: type={getattr(obj.data, 'type', '?')} "
            f"range_m={getattr(settings, 'range_m', 0.0)} -> {reach} mm, "
            f"show_reach={shown}")

    lines.append(f"last draw error    : {LAST_ERROR or 'none'}")
    return "\n".join(lines)


def _prune_caches():
    """Drop cached geometry for objects that no longer exist."""
    existing = {obj.name for obj in bpy.data.objects if obj.type == "LIGHT"}
    for name in list(_LINE_CACHES):
        if name not in existing:
            del _LINE_CACHES[name]


def register_overlay():
    """Install the viewport draw handler, replacing any stale one."""
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ValueError, RuntimeError):
            # Already gone (reload, or Blender dropped it); just reinstall.
            pass
        _DRAW_HANDLE = None
    _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        draw_reach_overlay, (), "WINDOW", "POST_VIEW")
    return _DRAW_HANDLE


def unregister_overlay():
    """Remove the viewport draw handler and free cached geometry."""
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _DRAW_HANDLE = None
    _LINE_CACHES.clear()
