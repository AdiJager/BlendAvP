"""Waypoint graphs: which waypoints a module's list should link together.

Retail links are **100% symmetric** — across 14332 links in 332 chunks there is
not one one-way link, no self-link and no out-of-range index. So the graph is
undirected and the writer emits both directions from a single stored edge.
That halves the clicking an artist would otherwise do.

The links themselves are **generated from geometry**, which retail's own data
supports: with the waypoint boxes read in the right frame, a rule of

    overlap on at least two axes, and separated by no more than T along the third

reproduces **99.4%** of retail's links at T = 500 mm. Precision is lower (~42%),
so the rule adds links retail does not have — but an extra link only matters if
it crosses a wall, and the engine routes along the best path regardless.

Waypoints are deliberately **sparse**. The designers placed them at junctions
where the default navigation fails and left the rest of the level to it, so gaps
between clusters are normal and must not be reported as errors. When an NPC is
in a module with no covering waypoint, `SweepWaypoints` finds nothing and the
NPC falls back to steering straight at its target — the documented "waypoint
dropout" path, which is a normal mode of operation rather than a failure.

See `context/NOTES_WAYPOINTS.md` for the full reconnaissance.
"""
from __future__ import annotations

# How far apart two boxes may be along the one axis they do not share and still
# be joined. Measured against retail: 500 mm reproduces 99.4% of its links.
LINK_TOLERANCE_MM = 500

# How many axes must overlap for a link. Two is what retail does: a waypoint is
# a slab across a corridor, so neighbours share the cross-section and meet end
# to end along the corridor.
MIN_SHARED_AXES = 2


def boxes_join(a, b, tolerance=LINK_TOLERANCE_MM):
    """Return True when two waypoint boxes should be linked.

    Each box is `((min), (max))` in world coordinates. The boxes must overlap on
    at least `MIN_SHARED_AXES` axes, and the gap along any remaining axis must be
    within `tolerance`.
    """
    gaps = [min(a[1][k], b[1][k]) - max(a[0][k], b[0][k]) for k in range(3)]
    shared = sum(1 for gap in gaps if gap > 0)
    if shared < MIN_SHARED_AXES:
        return False
    # A box that overlaps on all three axes leaves no axis to measure, and is
    # linked outright.
    if shared == 3:
        return True
    separation = min(-gap for gap in gaps if gap <= 0)
    return separation <= tolerance


def build_graph(boxes, tolerance=LINK_TOLERANCE_MM):
    """Return `{index: sorted([peer, ...])}` for a module's waypoints.

    Only waypoints of the **same module** are linked, because a chunk can only
    refer to indices within its own list. A box that spills into the next room
    is still linked across the spill if the neighbouring waypoint lives in the
    same module.
    """
    graph = {index: [] for index in range(len(boxes))}
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes_join(boxes[i], boxes[j], tolerance):
                graph[i].append(j)
                graph[j].append(i)
    return {index: sorted(peers) for index, peers in graph.items()}


def link_entries(graph, flags=None):
    """Return per-waypoint link lists as `[(index, flags), ...]`.

    `flags` defaults to ALIEN_ONLY, and it is the whole reason an alien ever
    leaves the ground: `AlienIsEncouragedToCrawl` returns 1 only when the link
    it is about to traverse carries that bit (`Bh_waypt.c:895`). Writing zero,
    as this once did, produced a graph the bots followed on the floor and never
    climbed.

    Retail uses ALIEN_ONLY on 9042 of its 14332 links and zero on 4674, so both
    are shipped values; the default here is the one that makes crawling
    possible.
    """
    if flags is None:
        from .chunks.waypoint import LINK_FLAG_ALIEN_ONLY
        flags = LINK_FLAG_ALIEN_ONLY
    return {index: [(peer, flags) for peer in peers]
            for index, peers in graph.items()}


def isolated(graph):
    """Return the indices with no links, worst first.

    A waypoint joined to nothing is a place an NPC can be routed into and then
    have nowhere to go, which is worth warning about — unlike a *gap* between
    clusters, which is how retail levels are built.
    """
    return [index for index, peers in sorted(graph.items())
            if not peers]
