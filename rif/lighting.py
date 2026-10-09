"""Prelight baking — reproduce the engine's per-vertex lighting maths.

AvP (1999) has no dynamic per-object lighting for static geometry: it shades
each *vertex* and stores the result in the SHPVTINT chunk, which the loader
copies into ``SHAPEHEADER::sh_extraitemdata`` and
``VertexIntensity_Standard_Opt`` (Kshape.c:2116-2298) reads back as the
starting colour of every vertex. This module computes those values offline,
exactly the way the engine would, so a level exported from Blender is lit the
way the same lights would light it in-game.

The engine formula
==================

``VertexIntensity_Standard_Opt`` (Kshape.c:2242-2251 for prelight, then
2205-2243 for every light):

    redI = greenI = blueI = 0
    if shape.shapeflags & ShapeFlag_PreLit:
        packedI = Global_EID_IPtr[vertexNumber]      # our SHPVTINT value
        blueI  = (packedI & 255) * 257
        greenI = ((packedI >> 8) & 255) * 257
        redI   = ((packedI >> 16) & 255) * 257

    for each light in LightSourcesForObject:
        vertexToLight = light.LightWorld - vertex.position
        distanceToLight = |vertexToLight|_1 / 4 approximation   # see below

        if distanceToLight < light.LightRange:
            idot = MUL_FIXED(light.LightRange - distanceToLight,
                             light.BrightnessOverRange)
            if distanceToLight > 0:
                dotproduct = MUL_FIXED(vertexNormal, vertexToLight)  # per axis
                if dotproduct > 0:
                    idot = (WideMulNarrowDiv(idot, dotproduct, distanceToLight)
                            + idot / 4) / 2
                else:
                    idot /= 8
            redI   += MUL_FIXED(idot, light.RedScale)
            greenI += MUL_FIXED(idot, light.GreenScale)
            blueI  += MUL_FIXED(idot, light.BlueScale)

    R = min(redI   >> 8, 255)
    G = min(greenI >> 8, 255)
    B = min(blueI  >> 8, 255)

Supporting values, all resolved at load time:

* ``MUL_FIXED(a, b)   = (a * b) >> 16``            (INLINE.H:899)
* ``DIV_FIXED(a, b)   = (a << 16) / b``            (INLINE.H:918)
* ``WideMulNarrowDiv(a, b, c) = (a * b) / c``      (INLINE.H:979)
* ``light.LightBright     = STDLIGHT.brightness``  (Objsetup.cpp:3377)
* ``light.RedScale        = ((colour >> 16) & 255) * 257``  (Objsetup.cpp:3381)
* ``light.LightRange      = STDLIGHT.range``       (Objsetup.cpp:3385)
* ``light.BrightnessOverRange = DIV_FIXED(MUL_FIXED(LightBright, LightScale),
                                          LightRange)``  (Avpview.c:793)

The distance metric is the engine's cheap octagonal approximation
(Kshape.c:2180-2214), not the Euclidean length:

    dx, dy, dz = |vertexToLight.{x,y,z}|
    if dx > dy:  distance = dx + ((dy + dz) >> 2) if dx > dz
                            else dz + ((dy + dx) >> 2)
    else:        distance = dy + ((dx + dz) >> 2) if dy > dz
                            else dz + ((dx + dy) >> 2)

Units
=====

* positions and ranges: RIF millimetres (Blender metres x 1000)
* brightness: 16.16 fixed point, ``ONE_FIXED = 65536`` means 1.0
* colours: 0..255 per channel

Scope of this module
====================

Modelled: omni point lights with colour, brightness and range, plus a flat
ambient/base term. Not modelled yet (they need more source work and are not
required for Alpha):

* spot cones — the engine's ``LFlag_CosSpreadAtten`` uses ``spread``, which
  this baker currently ignores (all lights are treated as omni);
* shadows — ``LFlag_NoShadows`` exists but there is no shadow code to copy;
* light contribution limits — the engine caps at ``MaxLightsPerObject`` (50)
  per object, which is far above what a level normally has.
"""
from __future__ import annotations

import math

from .types import blender_to_rif_axis, meters_to_rif

ONE_FIXED = 65536

# Packed SHPVTINT layout: bits 0..7 blue, 8..15 green, 16..23 red.
PRELIGHT_BLACK = 0x00000000
PRELIGHT_WHITE = 0x00FFFFFF

# --- Blender <-> engine light calibration ---------------------------------
#
# The two renderers disagree about both falloff and units, so the mapping has
# to be derived rather than guessed.
#
# Blender, for a surface facing a lamp of P watts at distance d:
#
#     irradiance = P / (4*pi*d^2)          (linear, 1.0 == white)
#
# The engine, from Kshape.c:2205-2243 with the vertex normal pointing at the
# light so the dot-product term is 1:
#
#     accumulation += MUL_FIXED(idot, colour_scale)      where
#     idot          = MUL_FIXED(brightness * (R - d) / R, ONE_FIXED)
#     colour_scale  = 255 * 257 = 65535
#     pixel         = accumulation >> 8
#
# With a white light at full colour that collapses to
#
#     pixel = brightness * (R - d) / R / 256
#
# so a pixel value of 255 needs `brightness * (R - d) / R = 65536`: the
# engine's brightness is the surface irradiance expressed as 65536 per unit.
# Setting that against Blender's irradiance gives the conversion, and the
# linear `(R - d) / R` term is left to the engine — forcing it to follow an
# inverse square as well would apply the falloff twice.
#
#     brightness = 65536 * P / (4*pi*r0^2)
#
# r0 is the distance at which the two scales are compared. It is THE exposure
# knob for the whole exporter, and it has to sit where Blender's wattages and
# the engine's 16-bit range overlap: `brightness` is clamped to 32767 because
# the engine shifts it left 16 and divides by the range, so a lamp whose
# (P / r0^2) is too large simply clips and stops responding to Power.
#
# `LightBright` is documented as "0->1 Fixed Point" (prototyp.h:772), so the
# engine's brightness is 16.16 fixed point where ONE_FIXED == 1.0 — the same
# scale as ONE_FIXED everywhere else. Retail Battle.RIF ships brightness 65536
# on its lights, confirming it. The earlier belief that the field is clamped
# to 32767 was wrong and made every exported light half as bright as it should
# be.
#
# The mapping from Blender watts was MEASURED rather than derived, because the
# engine's 16.16 shifts truncate at every step and hand-derivation is easy to
# get wrong. `scratch/probe_brightness.py` runs the real bake and gives, for a
# vertex whose normal faces the light:
#
#     pixel = brightness * (R - d) / R / one_unit
#
# where one unit of brightness (ONE_FIXED) is a whole 0-255 pixel step per
# unit of the linear ramp. Anchoring the intent the artist can reason about
#
#     500 W at 5 m  ->  128/255 (mid-grey)
#
# fixes the watts-to-brightness constant, and everything else follows
# proportionally:
#
#      125 W -> brightness  8192  -> ~32/255 at 5 m, dim but visible
#      250 W -> brightness 16384  -> ~64/255
#      500 W -> brightness 32768  -> ~128/255
#     1000 W -> brightness 65536  -> ONE_FIXED, the engine's own ceiling
#     2000 W -> clamped; lower Power or brighten via rif_range
#
# Power stays proportional up to ONE_FIXED. A darker level should lower
# BLENDER_REFERENCE_POWER_W rather than push Power up.
BLENDER_REFERENCE_POWER_W = 500.0
BLENDER_REFERENCE_DISTANCE_M = 5.0

# Brightness per watt per metre^2, derived from the MEASURED relation
# (`scratch/probe_brightness.py`):
#
#     pixel = brightness * (R - d) / R / PIXEL_DIVISOR,  PIXEL_DIVISOR ~= 364
#
# So mid-grey at the anchor needs brightness ~46600 there (46600 / 364 = 128):
#   brightness = K * P / d^2   =>   K = 46600 * 5^2 / 500
_PIXEL_DIVISOR = 364.0
_ANCHOR_PIXEL = 128.0

_WATTS_DISTANCE_TO_BRIGHTNESS = (
    (_ANCHOR_PIXEL * _PIXEL_DIVISOR) * (BLENDER_REFERENCE_DISTANCE_M ** 2)
    / BLENDER_REFERENCE_POWER_W)

# The engine cannot represent brightness above ONE_FIXED (1.0), so that is
# the real ceiling rather than a 16-bit limit.
BRIGHTNESS_CEILING = ONE_FIXED


# Exponent re-shaping the engine's linear ramp towards Blender's curve.
# 1.0 reproduces the engine's own maths bit-for-bit, which is the default:
# the falloff is the engine's job, and shaping it here as well would apply it
# twice. Raise it only when a level looks too flat compared with Blender, at
# the cost of no longer matching the runtime-lit objects around it.
DEFAULT_FALLOFF_RESPONSE = 1.0

# Falloff radius used when a light does not carry `rif_range`. The engine needs
# an explicit range because its falloff is linear and reaches zero there.
# Blender's own `shadow_soft_size` is NOT used for this: it defaults to 0.25 m
# and describes shadow softness, not reach — using it left rooms unlit.
DEFAULT_LIGHT_RANGE_MM = 25000


def mul_fixed(a: int, b: int) -> int:
    """Port of the engine's MUL_FIXED: (a * b) >> 16.

    The shipped inline is ``shrd eax, edx, 16`` on the 64-bit product, i.e.
    floor division by 65536, which Python's ``>>`` already matches.
    """
    return (a * b) >> 16


def div_fixed(a: int, b: int) -> int:
    """Port of the engine's DIV_FIXED: the 32-bit ``(a << 16) / b``.

    The shipped inline (INLINE.H:391 and :918) is

        cdq ; rol eax,16 ; mov dx,ax ; xor ax,ax ; idiv ebx

    At the ``idiv`` the register pair edx:eax holds exactly ``a << 16`` for
    any 32-bit ``a``: the rotate moves the top half of ``a`` into dx while
    the low half lands in ax, and dx already held the sign extension of
    ``a``. So the mathematically exact 32-bit value is what gets divided —
    there is NO overflow trap here, which is easy to get wrong and turns
    every light in a level black if you mask instead of rotate.
    """
    if b == 0:
        return 0
    return _trunc_div(_int32_divisor(a), b)


def _int32_divisor(a: int) -> int:
    """Value of the edx:eax pair after the engine's ``cdq/rol/mov/xor`` sequence.

    That sequence places ``a << 16`` in the register pair for any 32-bit
    ``a``, so the exact mathematical value is what ``idiv`` sees. Returning
    the plain shift is therefore correct, and — importantly — the result may
    exceed 32 bits (that is exactly what puts a value in edx).
    """
    return a << 16


def wide_mul_narrow_div(a: int, b: int, c: int) -> int:
    """Port of WideMulNarrowDiv: ``(a*b)/c`` through a 32-bit register.

    ``imul`` writes a full 64-bit product into edx:eax, but the following
    ``idiv c`` divides only eax — the low 32 bits — and faults when the true
    quotient does not fit. Both properties matter, so the product is wrapped
    to 32 bits and the quotient saturated like x86 would behave.
    """
    if c == 0:
        return 0
    product = _wrap_int32(a * b)
    quotient = _trunc_div(product, c)
    if quotient > _INT32_MAX:
        return _INT32_MAX
    if quotient < _INT32_MIN:
        return _INT32_MIN
    return quotient


_INT32_MAX = 0x7FFFFFFF
_INT32_MIN = -0x80000000


def _wrap_int32(value: int) -> int:
    """Reduce an integer to its signed 32-bit register value."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _trunc_div(a: int, b: int) -> int:
    """Integer division truncated toward zero, like x86 idiv."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def octagonal_distance(dx: int, dy: int, dz: int) -> int:
    """Port of the engine's per-light distance approximation.

    Kshape.c:2180-2214. Inputs are the absolute axis deltas between a vertex
    and the light, in RIF millimetres. Cheaper than a square root and it is
    what the shipped game actually uses, so matching it keeps a baked level
    identical to a runtime-lit one.
    """
    dx, dy, dz = abs(dx), abs(dy), abs(dz)
    if dx > dy:
        if dx > dz:
            return dx + ((dy + dz) >> 2)
        return dz + ((dy + dx) >> 2)
    if dy > dz:
        return dy + ((dx + dz) >> 2)
    return dz + ((dx + dy) >> 2)


def scale_from_colour(colour: int) -> tuple:
    """Return (RedScale, GreenScale, BlueScale) for a packed 0x00RRGGBB colour.

    Objsetup.cpp:3381-3383: each channel is expanded 0..255 -> 0..65536 with
    the x257 trick, matching the engine's fixed-point colour range.
    """
    return (((colour >> 16) & 255) * 257,
            ((colour >> 8) & 255) * 257,
            (colour & 255) * 257)


def brightness_over_range(brightness: int, light_range: int,
                          light_scale: int = ONE_FIXED) -> int:
    """Port of Avpview.c:793 — the per-light attenuation slope."""
    return div_fixed(mul_fixed(brightness, light_scale), light_range)


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite easing of x from [edge0, edge1] onto [0, 1]."""
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def cone_factor(direction, light_to_vertex, cos_inner, cos_outer) -> float:
    """Intensity multiplier for a spot cone, in [0, 1].

    The engine has no cone code at all: ``LFlag_CosSpreadAtten`` is defined in
    prototyp.h:793 but never referenced, and Kshape.c's per-vertex lighting
    only ever looks at distance and the vertex normal. STDLIGHT.spread is
    therefore stored and ignored, so a spot exported with runtime lights would
    light in every direction. Baking the cone here is what actually makes a
    Blender spot behave like a spot in-game.

    `direction` is the cone's forward axis and `light_to_vertex` the vector
    from the light towards the vertex. The falloff runs from full intensity at
    the inner angle to zero at the outer one, the shape artists expect from
    Blender's own spot.
    """
    dx = light_to_vertex[0]
    dy = light_to_vertex[1]
    dz = light_to_vertex[2]
    length = (dx * dx + dy * dy + dz * dz) ** 0.5
    if length < 1e-9:
        return 1.0
    cos_angle = (direction[0] * dx + direction[1] * dy
                 + direction[2] * dz) / length
    if cos_angle >= cos_inner:
        return 1.0
    if cos_angle <= cos_outer:
        return 0.0
    return smoothstep(cos_outer, cos_inner, cos_angle)


def bake_vertex_intensities(
    vertices,
    normals,
    lights,
    base_colour: int = PRELIGHT_WHITE,
    light_scale: int = ONE_FIXED,
    falloff_exponent: float = DEFAULT_FALLOFF_RESPONSE,
):
    """Bake per-vertex packed prelight values for one object.

    vertices : sequence of (x, y, z) vertex positions in RIF millimetres,
        already in the object's local space (the space SHPRAWVT stores).
    normals  : sequence of (x, y, z) unit normals in the same space, one per
        vertex and in the same order as `vertices`.
    lights   : sequence of light dicts, each with
            location        (x, y, z) in RIF millimetres, relative to the
                            object origin (use `bake_for_object` to shift)
            colour          packed 0x00RRGGBB, default white
            brightness      16.16 fixed point, default ONE_FIXED (1.0)
            range           RIF millimetres, default 20000
            direction       optional (x, y, z) cone axis; when present the
                            light is treated as a spot
            spot_cos_inner  cosine of the inner cone half-angle (default 1.0)
            spot_cos_outer  cosine of the outer cone half-angle (default 1.0)
    base_colour : packed 0x00RRGGBB ambient term every vertex starts from,
        i.e. the value the engine would have read from SHPVTINT.
    light_scale : engine LightScale (LITSCALE), normally ONE_FIXED.
    falloff_exponent : exponent applied to the engine's linear falloff to make
        it look like Blender's inverse-square response. 1.0 reproduces the
        shipped engine bit-for-bit; `DEFAULT_FALLOFF_RESPONSE` is the shape
        that visually matches Blender.

    Returns a list of packed 0x00RRGGBB ints, one per vertex.

    Note on spaces: the engine keeps vertex points in the object's local space
    and holds the light in `LIGHTBLOCK::LocalLP`, already translated into the
    same space (Avpview.c:779-800 does that conversion when a module is
    allocated). So `lights` must already be relative to the object's origin —
    `bake_for_object` below does the translation.
    """
    red_base = ((base_colour >> 16) & 255) * 257
    green_base = ((base_colour >> 8) & 255) * 257
    blue_base = (base_colour & 255) * 257

    prepared = []
    for light in lights:
        loc = light.get("location", (0, 0, 0))
        colour = light.get("colour", PRELIGHT_WHITE)
        brightness = int(light.get("brightness", ONE_FIXED))
        light_range = int(light.get("range", 20000))
        if light_range <= 0 or brightness == 0 or colour == 0:
            continue
        prepared.append((
            int(loc[0]), int(loc[1]), int(loc[2]),
            scale_from_colour(colour),
            brightness,
            brightness_over_range(brightness, light_range, light_scale),
            light_range,
            light.get("direction"),
            float(light.get("spot_cos_inner", 1.0)),
            float(light.get("spot_cos_outer", 1.0)),
        ))

    out = []
    for i, (vx, vy, vz) in enumerate(vertices):
        red_i = red_base
        green_i = green_base
        blue_i = blue_base

        nx, ny, nz = normals[i] if i < len(normals) else (0.0, 0.0, 0.0)

        for (lx, ly, lz, scales, brightness, bor, light_range,
             direction, cos_inner, cos_outer) in prepared:
            tlx, tly, tlz = lx - vx, ly - vy, lz - vz
            distance = octagonal_distance(tlx, tly, tlz)
            if distance >= light_range:
                continue

            if falloff_exponent == 1.0:
                # Fast path: exactly the engine's own arithmetic.
                idot = mul_fixed(light_range - distance, bor)
            else:
                fraction = falloff_response(
                    (light_range - distance) / light_range, falloff_exponent)
                idot = mul_fixed(int(round(fraction * light_range)), bor)

            if distance > 0:
                # MUL_FIXED of a float normal by an int vector: the engine's
                # normals are 16.16 (Objsetup stores *ONE_FIXED), so scaling
                # the integer deltas by the unit normal is the same product.
                dot = (mul_fixed(int(round(nx * ONE_FIXED)), tlx)
                       + mul_fixed(int(round(ny * ONE_FIXED)), tly)
                       + mul_fixed(int(round(nz * ONE_FIXED)), tlz))
                if dot > 0:
                    idot = (wide_mul_narrow_div(idot, dot, distance)
                            + idot // 4) // 2
                else:
                    idot //= 8

            if direction is not None:
                factor = cone_factor(direction, (vx - lx, vy - ly, vz - lz),
                                     cos_inner, cos_outer)
                if factor <= 0.0:
                    continue
                idot = int(idot * factor)

            red_s, green_s, blue_s = scales
            red_i += mul_fixed(idot, red_s)
            green_i += mul_fixed(idot, green_s)
            blue_i += mul_fixed(idot, blue_s)

        out.append(((min(red_i >> 8, 255) << 16)
                    | (min(green_i >> 8, 255) << 8)
                    | min(blue_i >> 8, 255)))
    return out


def bake_for_object(
    object_location_rif,
    vertices,
    normals,
    lights,
    base_colour=PRELIGHT_WHITE,
    light_scale=ONE_FIXED,
    falloff_exponent=DEFAULT_FALLOFF_RESPONSE,
):
    """Bake one object from vertices given in OBJECT-LOCAL space.

    object_location_rif : the OBJHEAD1.location of the object, in RIF
        millimetres. Lights are stored in world space by the level, but the
        engine gives each module a LIGHTBLOCK whose `LocalLP` is relative to
        the module's world origin (Avpview.c:779-800), so the same shift is
        applied here before calling `bake_vertex_intensities`.

    IMPORTANT: `vertices` must be local to the object, i.e. straight from
    `mesh.vertices[i].co` without the object matrix applied. Passing
    world-space vertices while the lights are also shifted here applies the
    offset twice and in two different frames, and the symptom is that only the
    part of the geometry crossing the world origin receives any light. If the
    vertices are already in world space, call `bake_vertex_intensities`
    directly instead - that is what the operator does.
    """
    ox, oy, oz = object_location_rif
    local_lights = []
    for light in lights:
        lx, ly, lz = light.get("location", (0, 0, 0))
        shifted = dict(light)
        shifted["location"] = (lx - ox, ly - oy, lz - oz)
        local_lights.append(shifted)
    return bake_vertex_intensities(
        vertices, normals, local_lights,
        base_colour=base_colour, light_scale=light_scale,
        falloff_exponent=falloff_exponent,
    )


def location_to_rif(meters) -> tuple:
    """Convert a Blender world location in metres to RIF millimetres.

    Provided for callers holding positions already converted by the builder
    (`builder._make_stdlight` does its own conversion, so the operator passes
    Blender metres through untouched and only the baker uses this).
    """
    x, y, z = meters
    bx, by, bz = blender_to_rif_axis(x, y, z)
    return (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))


def direction_to_rif(direction) -> tuple:
    """Rotate a Blender direction vector into RIF space, keeping it unit length.

    Uses the same rotation as positions but without the metre scaling, since a
    direction carries no length. The result is normalised again so rounding
    cannot skew the cone test. Accepts either a plain (x, y, z) triple or a
    Blender vector, which exposes x/y/z attributes rather than unpacking.
    """
    if hasattr(direction, "x"):
        x, y, z = direction.x, direction.y, direction.z
    else:
        x, y, z = direction
    bx, by, bz = blender_to_rif_axis(x, y, z)
    length = (bx * bx + by * by + bz * bz) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (bx / length, by / length, bz / length)


# The engine stores light angles in "spread" units where a full circle is
# 4096 (the same convention as its sine/cosine tables).
SPREAD_PER_CIRCLE = 4096.0


def angle_to_spread(radians: float) -> int:
    """Convert an opening angle in radians to the engine's spread units."""
    return int(round(float(radians) / (2.0 * math.pi) * SPREAD_PER_CIRCLE))


def energy_to_brightness(energy: float) -> int:
    """Convert a Blender light energy (1.0 == engine 1.0) to 16.16 fixed."""
    return max(0, int(round(float(energy) * ONE_FIXED)))


def power_to_brightness(watts: float, light_range_mm: int = None) -> int:
    """Convert a Blender light's Power (watts) to engine 16.16 brightness.

    Brightness is 16.16 fixed point where ONE_FIXED is 1.0 (`prototyp.h:772`),
    and it behaves as `watts / distance^2` scaled by a constant chosen so that
    `BLENDER_REFERENCE_POWER_W` at `BLENDER_REFERENCE_DISTANCE_M` reads as
    mid-grey (see the calibration note above). The engine's own linear ramp
    supplies the distance falloff, so `light_range_mm` is accepted for
    signature stability but does not enter the value.

    The result is clamped to `BRIGHTNESS_CEILING` (ONE_FIXED, i.e. 1.0), which
    is the strongest light the engine can represent.
    """
    watts = float(watts or 0.0)
    if watts <= 0.0:
        return 0
    distance = float(BLENDER_REFERENCE_DISTANCE_M or 1.0)
    value = _WATTS_DISTANCE_TO_BRIGHTNESS * watts / (distance * distance)
    return max(0, min(int(round(value)), BRIGHTNESS_CEILING))


def effective_brightness(watts: float, light_range_mm: int) -> int:
    """Return the brightness stored in STDLIGHT, clamped to the engine ceiling.

    Kept separate from `power_to_brightness` so callers (and the calibration
    tool) can tell a light that is merely bright from one that had to be
    clamped.
    """
    return power_to_brightness(watts, light_range_mm)


def falloff_response(linear_fraction: float,
                     exponent: float = DEFAULT_FALLOFF_RESPONSE) -> float:
    """Re-shape the engine's linear falloff towards Blender's response.

    `linear_fraction` is the engine's own value for one vertex,
    ``(range - distance) / range``, in [0, 1]. The result is mapped through a
    power curve so that:

      * ``exponent == 1.0`` returns the input unchanged, reproducing the
        shipped engine's maths exactly;
      * larger exponents darken the mid-range, which is what an inverse-square
        falloff does in comparison with a linear ramp.

    The curve is anchored at 1.0 (full brightness at the light) and 0.0 (zero
    at `range`), so the light still contributes nothing beyond its range —
    unlike a naive ``1/d^2`` term, which never reaches zero and would leak
    light across the whole level.
    """
    if linear_fraction <= 0.0:
        return 0.0
    if linear_fraction >= 1.0:
        return 1.0
    if exponent == 1.0:
        return linear_fraction
    return linear_fraction ** exponent


def world_ambient_colour(world_colour, world_strength) -> int:
    """Pack Blender's World color and strength into 0x00RRGGBB ambient.

    `world_colour` is the linear (r, g, b) triple from World > Color and
    `world_strength` the World > Strength multiplier. Their product is the
    flat term every vertex starts from, i.e. what the engine reads out of
    SHPVTINT before adding lights.
    """
    strength = float(world_strength)
    out = 0
    for value in world_colour:
        channel = float(value) * strength
        channel = max(0.0, min(1.0, channel))
        out = (out << 8) | int(round(channel * 255.0))
    return out


def world_ambient_level(world_colour, world_strength, maximum: int = 255) -> int:
    """Ambience value for the LIGHTSET AMBIENCE chunk, from the same inputs.

    AMBIENCE is a single 0..255 scalar: Objsetup.cpp:3469 copies it into
    GlobalAmbience and VDB_Ambience, and the D3D paths clamp dynamic objects
    against GlobalAmbience. Static geometry does not read it — it starts from
    the SHPVTINT colour instead — so the two should be kept consistent.
    """
    packed = world_ambient_colour(world_colour, world_strength)
    luminance = (0.2126 * ((packed >> 16) & 255)
                 + 0.7152 * ((packed >> 8) & 255)
                 + 0.0722 * (packed & 255))
    return max(0, min(maximum, int(round(luminance))))


def spot_cosines(half_angle_radians: float, blend: float = 0.0) -> tuple:
    """Return (cos_inner, cos_outer) for a spot of the given half angle.

    Blender's `spot_size` is the cone's full opening angle, so pass half of
    it. `blend` (0..1) shrinks the inner cone, producing a soft edge the way
    Blender's Spot > Blend does.
    """
    half = max(0.0, min(math.pi / 2.0, float(half_angle_radians)))
    blend = max(0.0, min(0.999, float(blend)))
    inner = half * (1.0 - blend)
    return (math.cos(inner), math.cos(half))


def colour_to_packed(rgb) -> int:
    """Convert a linear 0..1 or 0..255 RGB triple to packed 0x00RRGGBB."""
    out = 0
    for value in rgb:
        channel = value * 255.0 if value <= 1.0 else value
        channel = max(0, min(255, int(round(channel))))
        out = (out << 8) | channel
    return out


# Blender's "Temperature" is a blackbody colour in Kelvin; 6500 K is the
# neutral default. This is the standard Tanner Helland approximation, which
# agrees with Blender's own blackbody table to within a couple of percent
# over 1000-40000 K — close enough for a 1999 renderer with 8-bit colour.
_NEUTRAL_KELVIN = 6500.0


def kelvin_rgb(kelvin: float) -> tuple:
    """Return a normalised (r, g, b) blackbody colour for a temperature."""
    temperature = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if temperature <= 66.0:
        red = 255.0
        green = 99.4708025861 * math.log(temperature) - 161.1195681661
    else:
        red = 329.698727446 * ((temperature - 60.0) ** -0.1332047592)
        green = 288.1221695283 * ((temperature - 60.0) ** -0.0755148492)
    if temperature >= 66.0:
        blue = 255.0
    elif temperature <= 19.0:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(temperature - 10.0) - 305.0447927307
    return (max(0.0, min(1.0, red / 255.0)),
            max(0.0, min(1.0, green / 255.0)),
            max(0.0, min(1.0, blue / 255.0)))


def temperature_tint(kelvin: float) -> tuple:
    """Return the colour multiplier for a temperature, 1.0 at 6500 K.

    Normalising by the neutral temperature keeps a light at the default
    setting unchanged, so enabling Temperature only adds a cast — it never
    silently rescales brightness.
    """
    r, g, b = kelvin_rgb(kelvin)
    nr, ng, nb = kelvin_rgb(_NEUTRAL_KELVIN)
    return (r / nr if nr else 1.0,
            g / ng if ng else 1.0,
            b / nb if nb else 1.0)


def light_colour(rgb, temperature=None, use_temperature=False) -> tuple:
    """Return the final (r, g, b) for a Blender light, in 0..1.

    Blender's viewport tints a lamp by its Temperature when Use Temperature is
    on, and 6500 K is neutral. The output has to stay in 0..1 because
    `colour_to_packed` expands anything up to 1.0 into 0..255, so the product
    is normalised by its own maximum instead of clipping — otherwise a warm
    tint would dim the light rather than colour it.
    """
    colour = tuple(float(v) for v in rgb)
    if use_temperature and temperature:
        tint = temperature_tint(temperature)
        colour = tuple(c * t for c, t in zip(colour, tint))
    peak = max(colour) if colour else 0.0
    if peak > 1.0:
        colour = tuple(c / peak for c in colour)
    return tuple(max(0.0, min(1.0, c)) for c in colour)

