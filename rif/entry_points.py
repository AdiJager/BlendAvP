"""Where the entry point between two adjacent modules goes.

A port of IntEng's `FUN_00402ae0` (`Adjmmode.cpp`), whose nine steps AVP_OpenInteng
documents in `entry_points.h` and implements in `entry_points.cpp`. The port reaches
99.1 % agreement with the 23 947 stock entry points that do not belong to AI
master/slave groups, and 91.1 % on flags and point together, so it is the same
algorithm the shipped levels were built with rather than a guess at it.

Why it matters: `ADJMDLEP` says which modules touch, but the point says *where*
an NPC steps through. An entry vector of zeros is accepted by the loader and sends
NPCs to the level origin, which is why edges without points are worse than no
edges at all.

Everything is worked out in RIF integer units, the same space the file stores, so
the rounding is the engine's and the result can be compared with retail byte for
byte. The `100`, `5` and `10` in the algorithm are file units too: 0.1 m, 0.005 m
and 0.01 m.

Two deliberate differences from IntEng:

- **AI master/slave groups are not modelled.** `ComputeEntryPoint` takes each
  module alone, and the header says that is what the group step reduces to for a
  module that is nobody's slave or master. Clusters are rare (2 to 30 per level,
  none in `Stat101` or `Breakout`).
- **`stopWhenNotTouching` is always off**, matching how the recalculation pass
  calls it: a pair that does not touch is reported and still gets a point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The margin every box is grown or shrunk by, in RIF units (0.1 m).
TOUCH_MARGIN = 100

# A pair closer than this on any side does not touch.
NOT_TOUCHING = -100

# The adjacent box is shrunk by this before the point is clamped into it (0.005 m).
ADJACENT_SHRINK = 5

# A floor polygon's lowest corner is taken this far below its vertices (0.01 m).
FLOOR_DROP = 10

# The "no value yet" sentinel the original loops start from.
FAR = 1_000_000_000

# Flags this routine owns: 0x2 and 0x4 say one module sits inside the other, and
# 0x8 says the way through is vertical. Only 0x8 is read by the game.
FLAG_INSIDE_ADJACENT = 0x2
FLAG_ADJACENT_INSIDE_ME = 0x4
FLAG_VERTICAL = 0x8
FLAG_SET_BY_HAND = 0x1
FLAGS_OWNED = FLAG_INSIDE_ADJACENT | FLAG_ADJACENT_INSIDE_ME


@dataclass
class EntryModule:
    """One module as the algorithm sees it, in RIF integer units.

    `points` are local to `place`, as the file stores them: a shape's points plus
    the object's place. `polygon_corners` hold a count followed by up to four
    point indices, five numbers each, matching IntEng's own packing.
    """

    place: tuple = (0, 0, 0)
    min_extent: tuple = (0, 0, 0)
    max_extent: tuple = (0, 0, 0)
    points: list = field(default_factory=list)
    polygon_corners: list = field(default_factory=list)
    polygon_normal_y: list = field(default_factory=list)

    def box(self):
        """Return this module's box: its extents moved by its place."""
        return (tuple(self.place[axis] + self.min_extent[axis]
                      for axis in range(3)),
                tuple(self.place[axis] + self.max_extent[axis]
                      for axis in range(3)))


@dataclass
class EntryResult:
    """What the algorithm decided, with the reasons kept for the panel."""

    done: bool = True
    warned_no_touch: bool = False
    failed_to_place: bool = False
    flags: int = 0
    point: tuple = (0, 0, 0)
    axis: int = -1
    middle: bool = False
    floor_moved: bool = False
    #: The point before the floor step. The stock levels often store this value
    #: rather than the floor one (AVP_OpenInteng counts 1 179 such cases), so a
    #: comparison has to know both.
    before_floor: tuple = (0, 0, 0)


def _half(value):
    """Return C's integer division by two: toward zero, not toward minus infinity."""
    return int(value / 2) if value < 0 else value // 2


def _box_of(module):
    """Return (lowest, highest) of one module's box."""
    return module.box()


def _list_box(modules):
    """Return the box that holds every module in the list."""
    lowest = [FAR, FAR, FAR]
    highest = [-FAR, -FAR, -FAR]
    for module in modules:
        low, high = _box_of(module)
        for axis in range(3):
            lowest[axis] = min(lowest[axis], low[axis])
            highest[axis] = max(highest[axis], high[axis])
    return tuple(lowest), tuple(highest)


def _place_is_inside(outer, inner):
    """Return True when `inner`'s place lies inside `outer`'s box, edges included."""
    low, high = _box_of(outer)
    for axis in range(3):
        if inner.place[axis] < low[axis] or high[axis] < inner.place[axis]:
            return False
    return True


def _count_inside_one(module, low, high, smallest, largest):
    """Count points strictly inside the box and widen `smallest`/`largest` to them.

    The caller starts `smallest` at the list's box maximum and `largest` at its
    minimum, exactly as the original does, so a module with no points inside
    leaves them inverted and the caller's check sees fewer than two.
    """
    count = 0
    for index in range(0, len(module.points) - 2, 3):
        point = tuple(module.points[index + axis] + module.place[axis]
                      for axis in range(3))
        if not all(low[axis] < point[axis] < high[axis] for axis in range(3)):
            continue
        count += 1
        for axis in range(3):
            smallest[axis] = min(smallest[axis], point[axis])
            largest[axis] = max(largest[axis], point[axis])
    return count


def _floor_candidates(module, point, floor_below, best):
    """Return the lowest floor under `point` in this module, or `best`.

    A floor is a polygon whose normal's y is below the limit, whose box in x and z
    holds the point, and whose lowest corner sits below the point. `FUN_004028f0`.
    """
    rel_x = point[0] - module.place[0]
    rel_z = point[2] - module.place[2]
    for index, normal_y in enumerate(module.polygon_normal_y):
        if not normal_y < floor_below:
            continue
        corners_at = 5 * index
        if corners_at + 4 >= len(module.polygon_corners):
            break
        count = module.polygon_corners[corners_at]
        low_x = low_y = low_z = FAR
        high_x = high_z = -FAR
        for corner in range(min(count, 4)):
            vertex = module.polygon_corners[corners_at + 1 + corner]
            if vertex < 0 or vertex * 3 + 2 >= len(module.points):
                continue
            x = module.points[vertex * 3]
            y = module.points[vertex * 3 + 1]
            z = module.points[vertex * 3 + 2]
            low_x = min(low_x, x)
            low_y = min(low_y, y - FLOOR_DROP)
            low_z = min(low_z, z)
            high_x = max(high_x, x)
            high_z = max(high_z, z)
        if not (low_x <= rel_x <= high_x and low_z <= rel_z <= high_z):
            continue
        floor = module.place[1] + low_y
        if point[1] < floor < best:
            best = floor
    return best


def _move_to_floor(modules, point, floor_below):
    """Return `point` with its height moved to the lowest floor of the list."""
    best = FAR
    for module in modules:
        best = _floor_candidates(module, point, floor_below, best)
    if best == FAR:
        return tuple(point), False
    moved = (point[0], best, point[2])
    return moved, moved != tuple(point)


def compute_entry_point(main, adjacent, flags_in=0, point_in=None,
                        floor_below=0.0):
    """Return the `EntryResult` for stepping from `main` into `adjacent`.

    `flags_in` is the entry's current flags, so a point already marked as set by
    hand (`0x1`) is left alone. `floor_below` is the normal limit that decides
    what counts as a floor; the game passes 0.0, which makes downward-facing
    polygons floors because RIF's y points down.
    """
    result = EntryResult()
    result.flags = flags_in & ~FLAGS_OWNED
    result.point = tuple(point_in) if point_in else (0, 0, 0)

    # 1. One module tucked inside the other. The pair is the two modules, not
    #    their lists, and the answer is the flag alone.
    if _place_is_inside(main, adjacent):
        result.flags |= FLAG_ADJACENT_INSIDE_ME
        return result
    if _place_is_inside(adjacent, main):
        result.flags |= FLAG_INSIDE_ADJACENT
        return result

    low1, high1 = _list_box([main])
    low2, high2 = _list_box([adjacent])

    # 2. The overlap box, grown by the margin on every side.
    low = tuple(max(low1[axis], low2[axis]) - TOUCH_MARGIN for axis in range(3))
    high = tuple(min(high1[axis], high2[axis]) + TOUCH_MARGIN for axis in range(3))

    # 3. The points strictly inside it, per module, and their extremes.
    inner_min1 = list(high1)
    inner_max1 = list(low1)
    inner_min2 = list(high2)
    inner_max2 = list(low2)
    inside1 = _count_inside_one(main, low, high, inner_min1, inner_max1)
    inside2 = _count_inside_one(adjacent, low, high, inner_min2, inner_max2)

    if inside1 < 2 or inside2 < 2:
        # 4. Too few points to find a seam: the middle of the overlap box.
        result.middle = True
        entry = tuple(_half(low[axis] + high[axis]) for axis in range(3))
    else:
        entry = tuple(
            _half(max(inner_min1[axis], inner_min2[axis])
                  + min(inner_max1[axis], inner_max2[axis]))
            for axis in range(3))

    # 5. A side below the threshold means the boxes do not touch.
    size = tuple(high[axis] - low[axis] for axis in range(3))
    if any(side < NOT_TOUCHING for side in size):
        result.warned_no_touch = True

    # 6. The thinnest side is the way through, and the side the adjacent module
    #    lies on decides which face of it.
    if size[0] < size[1] and size[0] < size[2]:
        result.axis = 0
        entry = (high1[0] + TOUCH_MARGIN if main.place[0] < adjacent.place[0]
                 else low1[0] - TOUCH_MARGIN, entry[1], entry[2])
        result.before_floor = tuple(entry)
        entry, result.floor_moved = _move_to_floor([adjacent], entry, floor_below)
        result.flags &= ~FLAG_VERTICAL
    elif size[1] <= size[2]:
        result.axis = 1
        entry = (entry[0],
                 high1[1] + TOUCH_MARGIN if main.place[1] < adjacent.place[1]
                 else low1[1] - TOUCH_MARGIN, entry[2])
        result.before_floor = tuple(entry)
        result.flags |= FLAG_VERTICAL
    else:
        result.axis = 2
        entry = (entry[0], entry[1],
                 high1[2] + TOUCH_MARGIN if main.place[2] < adjacent.place[2]
                 else low1[2] - TOUCH_MARGIN)
        result.before_floor = tuple(entry)
        entry, result.floor_moved = _move_to_floor([adjacent], entry, floor_below)
        result.flags &= ~FLAG_VERTICAL

    # 7. A point set by hand stays where the artist put it: the original returns
    #    without touching `result.point`, which still holds the value it came in
    #    with.
    if result.flags & FLAG_SET_BY_HAND:
        return result

    # 8. Hold the point inside the adjacent module's box, shrunk; where that puts
    #    it outside altogether, that axis takes the adjacent module's own place.
    held = []
    for axis in range(3):
        value = entry[axis]
        value = min(value, high2[axis] - ADJACENT_SHRINK)
        value = max(value, low2[axis] + ADJACENT_SHRINK)
        if high2[axis] < value or value < low2[axis]:
            value = adjacent.place[axis]
        held.append(value)

    # 9. The point has to land in the adjacent module, or IntEng says it failed.
    result.failed_to_place = not (low2[0] <= held[0] <= high2[0]
                                  and low2[1] <= held[1] <= high2[1]
                                  and low2[2] <= held[2] <= high2[2])
    result.point = tuple(held)
    return result
