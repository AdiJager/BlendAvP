"""Module linking: visibility lists and the AI adjacency graph.

AvP stitches a level together with two INDEPENDENT structures, and confusing
them is what made weighted generators inert in game.

`VMDARRAY` is a **visibility script**, not a neighbour list. The engine walks it
linearly and marks every module it passes as visible (`MODULE.C:190`):

    while(vptr->vmod_type != vmtype_term) {
        ModuleCurrVisArray[mptr->m_index] = flag;      // mark visible
        switch(vptr->vmod_instr) {
            case vmodi_null:   vptr++;                     // keep going
            case vmodi_bra_vc:                             // a DOOR
                if (mptr->m_flags & m_flag_open) vptr++;
                else vptr = vptr->vmod_data.vmodidata_ptr; // jump
        }
    }

Every entry we used to write had `branch_no == 0`, so nothing ever branched and
**every module ended up permanently visible**. `BH_GENER.C:227` refuses to create
an NPC while the generator's module is visible, so no `type 0` generator could
ever fire. Retail keeps 290,864 plain entries against 9,010 door entries.

`ADJMDLEP` is the **AI adjacency graph**: where NPCs may walk, with an entry
point per link. Every retail entry sets a real position (30,992 non-zero against
91 zero), and 4,541 of them carry `AdjacentModuleFlag_Vertical` — vents and
shafts, which `Projload.cpp:2452` turns into `alien_only`, so humans cannot use
them.

`AIMODMAS` / `AIMODSLA` cluster modules into AI modules and are **optional**:
`Als-dm.RIF`, `Base.RIF` and `Battle.RIF` have none of either and work. A module
with no AI chunks is simply its own AI module, which is the case this exporter
writes, so `entry_point` is measured from that module's own origin — the engine
adds `m_world` back (`Dynamics.c:6393`).

See `context/NOTES_MODULES.md` for the full reconnaissance.
"""
from __future__ import annotations

# Allowance when comparing box faces, in RIF millimetres. Boxes that merely
# touch can miss each other by a rounding step, and a level built from abutting
# rooms is the normal case rather than the exception.
TOUCH_EPSILON = 1

# How many axes a shared region must span to count as a way through. A region
# that is zero-thickness in two axes is an edge or corner contact — geometry
# that grazes rather than joins — and linking it would let NPCs path through a
# wall. A door or corridor mouth spans two axes, and is kept.
MIN_OPEN_AXES = 2


def box_of(points):
    """Return the ((min), (max)) corner tuple of an iterable of RIF points.

    Returns None for an empty input, which a mesh with no vertices produces.
    """
    xs, ys, zs = [], [], []
    for point in points:
        xs.append(point[0])
        ys.append(point[1])
        zs.append(point[2])
    if not xs:
        return None
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def boxes_meet(a, b, epsilon=TOUCH_EPSILON):
    """Return True when two boxes overlap or touch within `epsilon`.

    A shared wall counts: two rooms built from abutting geometry have
    interpenetrating boxes, because each includes its own half of the wall.
    """
    if a is None or b is None:
        return False
    (a_min, a_max), (b_min, b_max) = a, b
    for axis in range(3):
        if a_max[axis] + epsilon < b_min[axis]:
            return False
        if b_max[axis] + epsilon < a_min[axis]:
            return False
    return True


def overlap_box(a, b):
    """Return the box where two boxes meet, or None when they do not.

    A degenerate result is legitimate and useful: two boxes that touch along a
    plane intersect in a zero-thickness box, and that box's centre still lies on
    the boundary between them, which is exactly where an entry point belongs.
    """
    if a is None or b is None or not boxes_meet(a, b):
        return None
    (a_min, a_max), (b_min, b_max) = a, b
    low = tuple(max(a_min[axis], b_min[axis]) for axis in range(3))
    high = tuple(min(a_max[axis], b_max[axis]) for axis in range(3))
    return low, high


def box_centre(box):
    """Return the centre of a (min, max) box, as integer RIF millimetres."""
    low, high = box
    return tuple((low[axis] + high[axis]) // 2 for axis in range(3))


def is_walkable(shared):
    """Return True when a shared region spans at least two axes.

    An edge or corner contact spans one axis or none, and is not a way through;
    a doorway or corridor mouth spans two or three.
    """
    if shared is None:
        return False
    low, high = shared
    spanned = sum(1 for axis in range(3) if high[axis] > low[axis])
    return spanned >= MIN_OPEN_AXES


def inset_box(box, margin):
    """Return `box` shrunk by `margin` on every side, never inverted.

    A box thinner than twice the margin collapses to its own centre, so a
    degenerate module still yields a usable point rather than an invalid range.
    """
    low, high = box
    shrunk_low, shrunk_high = [], []
    for axis in range(3):
        span = high[axis] - low[axis]
        step = min(margin, span // 2)
        shrunk_low.append(low[axis] + step)
        shrunk_high.append(high[axis] - step)
    return tuple(shrunk_low), tuple(shrunk_high)


def clamp_point(point, box):
    """Return `point` moved into `box` along whichever axes lie outside it."""
    low, high = box
    return tuple(max(low[axis], min(high[axis], point[axis]))
                 for axis in range(3))


def neighbours(bounds, origins, require_walkable=True, margin=None):
    """Return {index: [(peer_index, arrival_point_in_the_peer), ...]}.

    The arrival point is where an NPC ends up when travelling from this module
    into the peer, in **world** coordinates. It matters that it lands inside the
    peer rather than on the boundary: two rooms that abut share a plane of zero
    thickness, and the centre of that plane is a point in the wall. An NPC sent
    there walks into masonry and stops, which is exactly what happened before
    this clamp existed.

    The point is produced by taking the centre of the shared region and pushing
    it into the peer's interior, so for a doorway it stays near the doorway
    instead of jumping to the middle of the room.
    """
    result = {index: [] for index in range(len(bounds))}
    for i, box_i in enumerate(bounds):
        if box_i is None:
            continue
        for j in range(len(bounds)):
            if i == j or bounds[j] is None:
                continue
            if not boxes_meet(box_i, bounds[j]):
                continue
            shared = overlap_box(box_i, bounds[j])
            if shared is None:
                continue
            if require_walkable and not is_walkable(shared):
                continue
            if margin is None:
                # A comfortable step inside: a twentieth of the peer's smallest
                # dimension, floored at 50 mm so a thin module still works.
                span = min(bounds[j][1][axis] - bounds[j][0][axis]
                           for axis in range(3))
                margin = max(50, span // 20)
            arrival = clamp_point(box_centre(shared),
                                  inset_box(bounds[j], margin))
            result[i].append((j, arrival))
    return result


def linked_modules(index, parents):
    """Return the modules visible from `index`, from the outliner hierarchy.

    A module sees **itself, its parent and its children**. Parenting two modules
    together in the outliner therefore declares them mutually visible, which is
    the common case — "these two spaces are connected" — and costs one drag.

    This replaces guessing visibility from geometry. An axis-aligned box test
    cannot tell a doorway from a wall, and worse, it treats a module as a solid
    block when a module is a hollow room joined to its neighbour by an opening:
    a straight shaft two modules deep reads as "occluded by the middle one" and
    the far room vanishes, leaving a hole where it should be drawn. The original
    developers linked modules by hand for the same reason, which is why retail
    lists 7-12 modules per entry out of 50-120 rather than a computed set.

    A module with no parent and no children sees only itself, so it is invisible
    from everywhere and a runtime generator inside it runs. That is the natural
    way to mark a generator space, and it replaces the old `Sealed` flag: no
    switch to forget, and no way to hide a room that the player can plainly see
    into.

    Links are direct only. Blender allows a single parent, so the graph is a
    forest and cycles are not expressible; transitivity is deliberately off,
    because a predicted set is easier to reason about than a guessed one.
    """
    parent = parents[index]
    peers = [parent] if parent is not None else []
    for other, other_parent in enumerate(parents):
        if other != index and other_parent == index:
            peers.append(other)
    return peers


def build_vmdarray(self_index, visible_peers, door_branches=None):
    """Return the VMDARRAY entries for one module.

    Each entry is `(object_index, branch_no, flags, spare)`. `branch_no` is not
    a boolean: a non-zero value marks a DOOR and is the index the visibility
    script jumps to when that door is closed, so the exporter leaves every entry
    at 0 until doors exist.

    `visible_peers` is what the module can see, normally from
    `linked_modules`. **The module never lists itself**, and that is not a
    detail: across all 63 retail levels and 12,805 modules, not one lists
    itself. Listing self marks the module visible without ever giving it a map,
    which is how a module ends up silently not drawing. The engine handles the
    module's own presence separately — it marks the player's module directly
    (`MODULE.C:132`) and supplies self as the sole entry when a module carries no
    VMDARRAY at all (`Projload.cpp:2389`).

    A module with no visible peers therefore gets an EMPTY list, which is what
    retail does and what makes an unlinked module render only when the player
    stands in it.
    """
    entries = []
    for peer in visible_peers:
        if peer == self_index:
            continue
        entries.append([peer, 0, 0, 0])
    if door_branches:
        for entry in entries:
            branch = door_branches.get(entry[0])
            if branch:
                entry[1] = branch
    return [tuple(entry) for entry in entries]


def build_adjmdlep(neighbour_list, origins, vertical=()):
    """Return the ADJMDLEP entries for one module.

    Each entry is `(object_index, flags, (x, y, z))`. The point is **relative to
    the listed peer's origin**, not to the listing module's: the engine files
    these under the destination (`Projload.cpp:2454`) and reads them back as
    `destination->m_aimodule->m_world + position` (`Dynamics.c:6393`). Writing
    them in the wrong frame puts every arrival point off by the distance between
    the two modules.

    `vertical` names the peers reached through a vent or shaft. They carry
    `AdjacentModuleFlag_Vertical` (0x8), which `Projload.cpp:2452` turns into
    `alien_only`, so marines and predators will not use that link.
    """
    vertical = set(vertical)
    entries = []
    for peer, arrival in neighbour_list:
        origin = origins[peer]
        relative = (arrival[0] - origin[0], arrival[1] - origin[1],
                    arrival[2] - origin[2])
        flags = 0x8 if peer in vertical else 0
        entries.append((peer, flags, relative))
    return entries
