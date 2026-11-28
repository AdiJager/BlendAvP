"""Resolve the AI clusters an author declared, and keep their edges out of the graph.

The model is one pointer per module: `rif_ai_master` names the cluster's head. A module
that points at another becomes a slave and gets `AIMODSLA`; a module that is pointed at
becomes a master and gets `AIMODMAS`. Both that and the entry-point filter need the same
mapping, so it lives here rather than being derived twice.

Two engine facts drive the design (`context/AI_CLUSTERS.md`):

- a slave's `WAYPOINT` is read and discarded (`Projload.cpp:2410`), so waypoints must be
  grouped by AI node rather than by module, which is why `node_of` exists;
- an `ADJMDLEP` entry whose target is in the same AI node is dropped
  (`Projload.cpp:2431`), so writing one is dead data.
"""
from __future__ import annotations


def master_map(objs):
    """Return {module index: master index} for every module that is a slave.

    A pointer at a missing object, at something that is not in the exported set, or at
    the module itself is ignored here; the validator reports those, because silently
    dropping one would leave a level that looks clustered and is not.
    """
    index_of = {obj.name: position for position, obj in enumerate(objs)}
    master_of = {}
    for position, obj in enumerate(objs):
        master = getattr(obj, "rif_ai_master", None)
        if master is None:
            continue
        target = index_of.get(master.name)
        if target is None or target == position:
            continue
        master_of[position] = target
    return master_of


def node_of(master_of, index):
    """Return the index of the AI node this module belongs to."""
    return master_of.get(int(index), int(index))


def slaves_of(master_of):
    """Return {master index: [slave index, ...]} built from the master map."""
    result = {}
    for slave, master in sorted(master_of.items()):
        result.setdefault(master, []).append(slave)
    return result


def cluster_kind(master_of, index):
    """Return what `MODULEDT` should say about this module's AI cluster.

    Returns ``"master"`` for a module others point at, ``{"master": index}`` for a
    slave, and None for a module that is its own AI node. A master is written even
    with no slaves -- the loader treats that exactly like no chunk at all, and the
    signature records what the author meant.
    """
    if index in master_of:
        return {"master": master_of[index]}
    if any(master == index for master in master_of.values()):
        return "master"
    return None


def without_internal_edges(neighbours, master_of):
    """Drop the entry points between two modules of the same AI node.

    The loader ignores such an entry (`Projload.cpp:2431`), so it is bytes that can
    never be read. The pair still gets no link, because the two boxes are one space
    and an NPC crosses between them without passing an entry point at all.
    """
    filtered = {}
    for index, pairs in neighbours.items():
        kept = [(peer, arrival) for peer, arrival in pairs
                if node_of(master_of, peer) != node_of(master_of, index)]
        filtered[index] = kept
    return filtered
