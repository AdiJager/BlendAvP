"""The game's sound library: what exists, how often retail uses it, and playback.

Every sound lives in a fastfile -- `common.ffl` and the `Snd*.FFL` files -- as a
verbatim WAV with its name in front of it. Because a WAV states its own length,
reading one out is a byte range rather than a decode, so audition needs no
unpacking step and no container specification.

Two container shapes, both handled:

* `common.ffl` interleaves name and audio. Walking back from each `RIFF` over
  the NUL padding recovers the name.
* `Snd*.FFL` starts with `RFFL`, then a table of names, then one contiguous
  block of audio. The names and the blocks are both ordered lists, so pairing
  them needs only that the two agree in length; the offset check
  `len(data) - data_size` landing on the first `RIFF` confirms the split.

**A file whose name and block counts disagree contributes nothing.** Pairing two
lists of different lengths shifts every name after the gap onto the wrong audio,
which audition would reveal as the wrong sound playing -- worse than a gap.

Retail uses 195 of the library's names, so the panel offers them most-used first
and keeps the `env` catalogue, the per-level ambience, at the top: that is where
almost every placed sound in the shipping data comes from.
"""
from __future__ import annotations

import struct
from pathlib import Path

CONTAINERS = ("fastfile",)
# A directory can be pointed at the install root or straight at the fastfiles.
FASTFILE_GLOBS = ("common.ffl", "*.ffl", "*.FFL")
# Retail's own folder prefix inside the library.
ENV_PREFIX = "sound\\env\\"
# Custom property on the scene World, holding the level's sound folder.
SOUND_DIRECTORY_PROP = "rif_sound_directory"

# Identifier for the "nothing chosen" entry. Blender drops an enum item
# whose identifier is empty, so it cannot be the empty string.
NONE_CHOICE = "__none__"

_index_cache = {}
_usage_cache = {}


def game_root(context):
    """Return the configured game folder, or None.

    The same preference the texture path uses, so a user who has already pointed
    the exporter at their install gets sounds with no further setup.
    """
    try:
        preferences = context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return None
    directory = (preferences.game_directory or "").strip()
    return directory or None


def find_fastfiles(root):
    """Return every fastfile under `root`, common.ffl first."""
    if not root:
        return []
    base = Path(root)
    found = []
    for pattern in FASTFILE_GLOBS:
        for path in base.rglob(pattern):
            if path not in found:
                found.append(path)
    found.sort(key=lambda p: (p.name.lower() != "common.ffl", p.name.lower()))
    return found


def _wav_length(data, offset, strict=True):
    """Return the byte length of the WAV at `offset`, or None."""
    if data[offset:offset + 4] != b"RIFF":
        return None
    if data[offset + 8:offset + 12] != b"WAVE":
        return None
    declared = struct.unpack_from("<I", data, offset + 4)[0]
    total = 8 + declared
    if offset + total > len(data) or total < 44:
        return None
    if not strict:
        return total
    cursor = offset + 12
    seen = set()
    while cursor + 8 <= offset + total:
        cid = data[cursor:cursor + 4]
        size = struct.unpack_from("<I", data, cursor + 4)[0]
        seen.add(cid)
        cursor += 8 + size + (size & 1)
    # The walk may land one byte past: an odd-length `data` carries a pad byte
    # the RIFF size does not count.
    if cursor - (offset + total) not in (0, 1):
        return None
    if b"fmt " not in seen or b"data" not in seen:
        return None
    return total


def _blocks(data, start, end, strict=True):
    """Return [(offset, length)] for every WAV in [start, end)."""
    out = []
    offset = start
    while offset < end:
        offset = data.find(b"RIFF", offset)
        if offset < 0 or offset >= end:
            break
        length = _wav_length(data, offset, strict)
        if length:
            out.append((offset, length))
            offset += length
        else:
            offset += 4
    return out


def _names(data, start, end):
    """Return [(offset, text)] for ASCII runs ending in .wav, in order."""
    out = []
    run = None
    for i in range(start, end):
        if 32 <= data[i] < 127:
            if run is None:
                run = i
        elif run is not None:
            text = data[run:i]
            if text.lower().endswith(b".wav"):
                out.append((run, text.decode("ascii", "replace")))
            run = None
    return out


def _scan_flat(data):
    """Names interleaved with audio."""
    out = {}
    for offset, length in _blocks(data, 0, len(data)):
        end = offset
        while end > 0 and data[end - 1] == 0:
            end -= 1
        if end == offset:
            continue
        begin = end
        while begin > 0 and 32 <= data[begin - 1] < 127:
            begin -= 1
        name = data[begin:end].decode("ascii", "replace")
        if name.lower().endswith(".wav"):
            out.setdefault(name.lower(), (offset, length))
    return out


def _scan_rffl(data):
    """Header, name table, then one contiguous block of audio."""
    data_size = struct.unpack_from("<I", data, 16)[0]
    split = len(data) - data_size
    names = _names(data, 28, split)
    blocks = _blocks(data, split, len(data))
    if len(blocks) != len(names):
        relaxed = _blocks(data, split, len(data), strict=False)
        if len(relaxed) == len(names):
            blocks = relaxed
    if len(blocks) != len(names):
        return {}
    out = {}
    for (_at, name), (offset, length) in zip(names, blocks):
        out.setdefault(name.lower(), (offset, length))
    return out


def index(root):
    """Return {name: (fastfile, offset, length)} for the whole library.

    Cached per root: the scan reads around 20 MB across 109 files, and the panel
    asks for the list on every redraw.
    """
    key = str(root)
    if key in _index_cache:
        return _index_cache[key]
    out = {}
    for path in find_fastfiles(root):
        data = path.read_bytes()
        found = _scan_rffl(data) if data[:4] == b"RFFL" else _scan_flat(data)
        for name, (offset, length) in found.items():
            out.setdefault(name, (path, offset, length))
    _index_cache[key] = out
    return out


def retail_usage(rif_root):
    """Return {wav_name lowercased: how many retail levels place it}.

    **Generator only.** This reads 63 levels and takes minutes; the runtime path
    uses `shipped_list()` instead. Kept because the shipped table has to come
    from somewhere.

    This is what decides the order of the panel's list. A sound retail uses
    everywhere belongs at the top; the ~1450 it never uses belong at the bottom.
    """
    key = str(rif_root)
    if key in _usage_cache:
        return _usage_cache[key]
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scratch"))
    try:
        import inspect_rif
    except ImportError:
        _usage_cache[key] = {}
        return {}
    counts = {}
    base = Path(rif_root)
    for path in sorted(base.glob("*.RIF")) + sorted(base.glob("*.rif")):
        try:
            data = inspect_rif.load(str(path))
        except Exception:  # noqa: BLE001
            continue
        offset = 0
        while True:
            offset = data.find(b"SOUNDOB2", offset)
            if offset < 0:
                break
            # The payload starts 12 bytes in; walk to the wav_name string.
            payload = offset + 12
            end = data.find(b"\x00", payload + 40)
            if end > 0:
                snd_end = data.index(b"\x00", payload + 40) + 1
                wav_end = data.find(b"\x00", snd_end)
                if wav_end > 0:
                    wav = data[snd_end:wav_end].decode("latin-1", "replace")
                    counts[wav.lower()] = counts.get(wav.lower(), 0) + 1
            offset += 8
    _usage_cache[key] = counts
    return counts


SHIPPED_LIST = Path(__file__).resolve().parent / "data" / "sounds.txt"


def shipped_list():
    """Return [(name, retail_count)] in offer order, from the shipped table.

    Generated by `scratch/generate_sound_list.py`. Reading this takes
    milliseconds; recomputing it reads 63 levels and takes minutes, so the file
    is the normal path and the computation is the fallback.
    """
    if not SHIPPED_LIST.exists():
        return None
    out = []
    for line in SHIPPED_LIST.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, count = line.partition("\t")
        if name:
            out.append((name, int(count) if count.isdigit() else 0))
    return out or None


def ordered_names(root, usage=None):
    """Return [(name, retail_count)] in the order to offer them.

    The shipped list is used when present. Otherwise the order is computed, and
    the rule is, in order:

    1. the `env` catalogue -- retail's per-level ambience, where nearly every
       placed sound comes from -- before anything else;
    2. within a group, by how often retail places it, descending;
    3. then alphabetically, so the order is stable between runs.
    """
    # Returns (name, count) pairs; the count is for ordering only. Callers that
    # just want the list should use `sound_names`.
    table = shipped_list()
    if table is not None and usage is None:
        return table

    usage = usage if usage is not None else {}
    # One entry per file name, not per path: the folder is chosen once for the
    # level by SOUNDDIR, so offering `ambie12.wav` six times would ask the artist
    # to pick something that makes no difference.
    index_now = index(root)
    names = sorted({name.rsplit("\\", 1)[-1] for name in index_now})
    env = ENV_PREFIX.replace("/", "\\")

    def used(name):
        """How often retail places this sound, summed over every folder."""
        return usage.get(name, 0)

    def in_env(name):
        """Whether any folder holding this file is part of the env catalogue."""
        for path in index_now:
            if path.rsplit("\\", 1)[-1] == name and path.startswith(env):
                return True
        return False

    env_names = {n for n in names if in_env(n)}

    def key(name):
        """Sort key: env first, then usage, then name."""
        return (0 if name in env_names else 1, -used(name), name)

    return [(name, used(name)) for name in sorted(names, key=key)]


def folder_names(root, directory):
    """Return the file names inside one `Sound Directory`, or None.

    The library stores paths like `sound\\env\\temple\\ambie1.wav`, and the
    level's Sound Directory is the folder under `sound`. Narrowing to it is what
    keeps the panel honest: a name from another folder cannot resolve for this
    level, so offering it would only invite a silent failure.
    """
    if not directory:
        return None
    prefix = ("sound\\" + directory.strip("\\/").replace("/", "\\")
              + "\\").lower()
    out = []
    for path in index(root):
        if path.lower().startswith(prefix):
            name = path.rsplit("\\", 1)[-1]
            if name not in out:
                out.append(name)
    return sorted(out)


def sound_names(root, directory=None):
    """Return the sound names in offer order, without the usage counts.

    With a `directory` the list is narrowed to that folder. Without one the
    whole library is offered, which is what a level that has not chosen a folder
    yet needs in order to show that sounds exist at all.

    The counts exist to decide the order -- most used first, so the common
    choice is near the top -- and not to be read. The panel wants names, and
    Blender filters the list by typing against the label, so a count in the
    label would also be matched against and would only get in the way.
    """
    ordered = ordered_names(root)
    if directory:
        allowed = folder_names(root, directory)
        if allowed:
            keep = set(allowed)
            return [name for name, _count in ordered if name in keep]
        return []
    return [name for name, _count in ordered]


SHIPPED_FOLDERS = Path(__file__).resolve().parent / "data" / "sound_folders.txt"


def folder_list():
    """Return [(folder, sound count)] from the shipped table, most stocked first.

    Only the env catalogue: those are the folders retail uses, and the other
    half of the library (`mission pack env`) is addressed differently in the
    shipping data.
    """
    if not SHIPPED_FOLDERS.exists():
        return []
    out = []
    for line in SHIPPED_FOLDERS.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, count = line.partition("\t")
        if name:
            out.append((name, int(count) if count.isdigit() else 0))
    return out


def directory_of(context):
    """Return this scene's Sound Directory, or None.

    Stored on the World because a .blend can hold several scenes and a scene is
    a level: one folder in the addon preferences would apply to all of them. The
    World already carries the ambient settings, so a level's audio belongs
    beside them.
    """
    world = getattr(context.scene, "world", None)
    if world is None:
        return None
    # Registered property sets the value; the ID key is what a .blend written
    # before it existed still carries, so both are read.
    value = getattr(world, SOUND_DIRECTORY_PROP, None)
    if not value:
        value = world.get(SOUND_DIRECTORY_PROP)
    value = (value or "").strip()
    return None if value in ("", NONE_CHOICE) else value


def find(root, name, directory=None):
    """Return (fastfile, offset, length) for a sound, or None.

    `name` may be a bare file name or a full library path. Several folders hold
    a file of the same name, so the level's own folder wins when it has one:
    that is the copy the level will resolve at run time, and it is the one the
    artist chose when they picked the folder.
    """
    wanted = name.lower().replace("/", "\\")
    short = wanted.rsplit("\\", 1)[-1]
    entries = index(root)
    exact = entries.get(wanted)
    if exact is not None:
        return exact
    if directory:
        prefix = ("sound\\" + directory.strip("\\/").replace("/", "\\")
                  + "\\").lower()
        here = entries.get(prefix + short)
        if here is not None:
            return here
    for path, where in entries.items():
        if path.rsplit("\\", 1)[-1] == short:
            return where
    return None


def carve(root, name, directory=None):
    """Return the raw WAV bytes for `name`, or None."""
    entry = find(root, name, directory)
    if entry is None:
        return None
    path, offset, length = entry
    data = path.read_bytes()
    return data[offset:offset + length]


def _players():
    """Return the system audio commands to try, most suitable first.

    Blender has no scriptable one-shot playback, so a small external player is
    the only way to hear a sound without leaving the application. The list is
    ordered by how likely the command is to exist and to return immediately.
    """
    import shutil
    import sys

    if sys.platform == "darwin":
        candidates = [["afplay"]]
    else:
        candidates = [
            ["paplay"],                       # PulseAudio
            ["aplay", "-q"],                  # ALSA
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
            ["mpv", "--really-quiet", "--no-video"],
            ["play", "-q"],                   # SoX
        ]
    return [c for c in candidates if shutil.which(c[0])]


def play(path):
    """Play a .wav, returning a note when it could not be played.

    Returns None on success. Every failure path returns a message naming the
    file, because a written file the artist can open is a usable outcome and a
    silent button is not.
    """
    import subprocess
    import sys

    if sys.platform == "win32":
        try:
            import winsound
        except ImportError:
            return f"Written to {path}"
        try:
            winsound.PlaySound(str(path),
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
            return None
        except RuntimeError as exc:
            return f"Written to {path} - could not play it ({exc})"

    players = _players()
    if not players:
        return f"Written to {path} - no audio player found"
    for command in players:
        try:
            subprocess.Popen(command + [str(path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return None
        except OSError:
            continue
    return f"Written to {path} - no audio player could start"


def load_into_blender(path, name):
    """Load the file into Blender's sound list and return the datablock name.

    Cross-platform by definition, and it puts the sound somewhere the artist can
    see and scrub, which is the fallback when no system player is available.
    """
    import bpy

    existing = bpy.data.sounds.get(name)
    if existing is not None:
        bpy.data.sounds.remove(existing)
    try:
        bpy.data.sounds.load(str(path), check_existing=False)
    except RuntimeError:
        return None
    sound = bpy.data.sounds[-1]
    sound.name = name
    return sound.name


def audition(root, name, sound_directory=None, output_dir=None):
    """Write `name` to a temporary .wav and return its path, or None.

    A WAV in a fastfile is stored verbatim and states its own length, so this is
    a copy rather than a conversion -- and Blender can play the result directly.

    `sound_directory` is the game's folder, such as `env\\temple`, and only
    chooses which copy to take. The file is always written to a real directory:
    passing the game's folder as an output path would name something that does
    not exist on disk.
    """
    import tempfile
    blob = carve(root, name, sound_directory)
    if not blob:
        return None
    stem = "".join(c if c.isalnum() or c in "._-" else "_"
                   for c in name.rsplit("\\", 1)[-1]) or "sound"
    target = Path(output_dir or tempfile.gettempdir()) / f"rif_{stem}"
    target.write_bytes(blob)
    return target
