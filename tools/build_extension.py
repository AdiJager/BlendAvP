#!/usr/bin/env python3
"""Build an installable Blender extension zip from this package.

Blender looks for `blender_manifest.toml` at the **root** of the archive, so a
zip that wraps everything in `avp_rif_exporter/` is rejected as an extension.
That is the single most common way to get this wrong, and the reason the tool
verifies the archive it just wrote instead of trusting the build.

When Blender is available the build is delegated to its own
`blender --command extension build`, which is authoritative: it applies
Blender's own file exclusions and validates the manifest. A pure-Python
fallback builds an equivalent archive on machines without Blender, so the tool
still works in a bare checkout.

    python tools/build_extension.py                 # writes into ../builds
    python tools/build_extension.py --check         # validate only, no zip
    python tools/build_extension.py --out DIR       # somewhere else
    python tools/build_extension.py --blender PATH  # pick the Blender build
    python tools/build_extension.py --no-blender    # force the fallback

Exits non-zero on any problem, so it can gate a release.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = TOOLS_DIR.parent

# Fields Blender requires in blender_manifest.toml. A missing one makes the
# extension fail to install with a message that does not name the field.
REQUIRED_MANIFEST_FIELDS = (
    "schema_version", "id", "version", "name", "tagline", "maintainer",
    "type", "license", "blender_version_min",
)

# Never ship these in the fallback build: bytecode caches are machine-specific
# and Blender excludes them too, so shipping them would make the two build
# paths disagree. Logs are excluded for the same reason — they are output, not
# source, and they carry timestamps from whichever machine last ran the tests.
EXCLUDED_DIRS = {"__pycache__", ".git", ".github", "builds", "logs"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".blend1", ".orig", ".rej", ".log"}

# Places Blender tends to live. Steam installs are the usual reason a plain
# `where blender` finds nothing.
BLENDER_HINTS = (
    r"C:\Program Files\Blender Foundation",
    r"C:\Program Files (x86)\Blender Foundation",
    r"C:\Program Files (x86)\Steam\steamapps\common\Blender",
    r"D:\Steam\steamapps\common\Blender",
    r"D:\SteamLibrary\steamapps\common\Blender",
    r"C:\Program Files\Steam\steamapps\common\Blender",
)


def find_blender(explicit=None):
    """Return the path to a Blender executable, or None when there is none."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise ValueError(f"--blender {explicit} is not a file")

    env = os.environ.get("BLENDER")
    if env and Path(env).is_file():
        return Path(env)

    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path)

    for hint in BLENDER_HINTS:
        base = Path(hint)
        if not base.is_dir():
            continue
        direct = base / "blender.exe"
        if direct.is_file():
            return direct
        # Versioned installs nest one level deeper: Blender 4.2\blender.exe
        for child in sorted(base.glob("Blender*")):
            exe = child / "blender.exe"
            if exe.is_file():
                return exe
    return None


def parse_manifest(path):
    """Return the manifest as a dict, or raise ValueError with a clear reason."""
    if not path.exists():
        raise ValueError(f"missing {path.name} in {path.parent}")
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return _parse_manifest_fallback(text)
    try:
        return tomllib.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{path.name} is not valid TOML: {exc}") from exc


def _parse_manifest_fallback(text):
    """Parse the flat subset of TOML this manifest uses, without tomllib."""
    data = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"') for v in value[1:-1].split(",")]
            data[key.strip()] = [v for v in items if v]
        elif value.startswith('"') and value.endswith('"'):
            data[key.strip()] = value[1:-1]
        else:
            data[key.strip()] = value
    return data


def check_manifest(package_dir):
    """Return (manifest, problems)."""
    problems = []
    manifest = parse_manifest(package_dir / "blender_manifest.toml")

    for field in REQUIRED_MANIFEST_FIELDS:
        if not manifest.get(field):
            problems.append(f"manifest is missing the required field {field!r}")

    if manifest.get("type") not in ("add-on", "theme"):
        problems.append(
            f"manifest type is {manifest.get('type')!r}, expected 'add-on'")

    if not (package_dir / "__init__.py").exists():
        problems.append("package has no __init__.py, so it cannot be an add-on")

    return manifest, problems


def collect_files(package_dir):
    """Return the relative paths to ship, with caches filtered out."""
    files = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        if rel.name.startswith("."):
            continue
        files.append(rel)
    return files


def find_stray_files(package_dir):
    """Return files that must not be in the package at all.

    These are filtered out of the fallback build, but Blender's own
    `extension build` has no exclusion list: it ships whatever is in the
    directory. So a stray log left behind by a test run reaches the package on
    the primary build path, and the only place to catch it is before the build
    starts.
    """
    strays = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            strays.append(rel.as_posix())
    return strays


def run_blender(blender, arguments):
    """Run Blender with the given arguments, returning (ok, output)."""
    command = [str(blender), "--command", "extension", *arguments]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output


def build_with_blender(blender, package_dir, target):
    """Build via Blender's own command. Return (ok, output)."""
    ok, output = run_blender(blender, [
        "build", "--source-dir", str(package_dir),
        "--output-filepath", str(target)])
    return ok, output


def build_with_python(package_dir, target):
    """Build an equivalent archive without Blender."""
    files = collect_files(package_dir)
    if not files:
        raise ValueError("nothing to ship: no files survived filtering")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            # arcname keeps the path relative to the package root, which is what
            # puts blender_manifest.toml at the top of the archive.
            archive.write(package_dir / rel, arcname=rel.as_posix())
    return files


def verify_zip(target, package_name):
    """Re-read the archive and return a list of problems with it."""
    problems = []
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        if "blender_manifest.toml" not in names:
            problems.append(
                "blender_manifest.toml is not at the root of the archive, so "
                "Blender will not accept this as an extension")
        nested = [n for n in names if n.startswith(f"{package_name}/")]
        if nested:
            problems.append(
                f"{len(nested)} entries are nested under {package_name}/; "
                "the archive must be flat")
        caches = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
        if caches:
            problems.append(
                f"{len(caches)} bytecode cache entries were shipped")
        strays = [n for n in names if Path(n).suffix in EXCLUDED_SUFFIXES]
        if strays:
            problems.append(
                f"{len(strays)} files that are not source were shipped: "
                + ", ".join(sorted(strays)[:5]))
        if "__init__.py" not in names:
            problems.append("the archive has no __init__.py at its root")
        # A manifest that parses on disk but not inside the archive would
        # install and then fail at enable time, which is worse than failing now.
        try:
            import tomllib
            tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            problems.append(f"manifest inside the archive does not parse: {exc}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an installable Blender extension zip.")
    parser.add_argument(
        "--out", default=None,
        help="output directory (default: the 'builds' folder beside the "
             "package)")
    parser.add_argument(
        "--name", default=None,
        help="archive base name (default: <id>-<version> from the manifest)")
    parser.add_argument(
        "--blender", default=None,
        help="path to blender.exe; found automatically when omitted")
    parser.add_argument(
        "--no-blender", action="store_true",
        help="use the built-in fallback even when Blender is available")
    parser.add_argument(
        "--check", action="store_true",
        help="validate the package and stop without writing an archive")
    args = parser.parse_args(argv)

    print(f"package: {PACKAGE_DIR}")
    try:
        manifest, problems = check_manifest(PACKAGE_DIR)
    except ValueError as exc:
        print(f"  ERROR: {exc}")
        return 1

    print(f"  id={manifest.get('id')} version={manifest.get('version')} "
          f"type={manifest.get('type')}")
    if problems:
        for problem in problems:
            print(f"  ERROR: {problem}")
        return 1
    print(f"  manifest OK ({len(REQUIRED_MANIFEST_FIELDS)} required fields)")

    strays = find_stray_files(PACKAGE_DIR)
    if strays:
        print(f"  ERROR: {len(strays)} file(s) must not be packaged:")
        for name in strays[:10]:
            print(f"           {name}")
        if len(strays) > 10:
            print(f"           ... and {len(strays) - 10} more")
        print("  Blender's own build cannot exclude these, so delete them "
              "before packaging.")
        return 1

    blender = None
    if not args.no_blender:
        try:
            blender = find_blender(args.blender)
        except ValueError as exc:
            print(f"  ERROR: {exc}")
            return 1
    if blender:
        print(f"  Blender: {blender}")
    elif args.no_blender:
        print("  Blender: skipped (--no-blender), using the fallback")
    else:
        print("  Blender: not found, using the fallback builder")

    # Let Blender validate first: its rules are the ones that matter, and a
    # clear failure here beats an archive that installs and then misbehaves.
    if blender:
        ok, output = run_blender(blender, ["validate", str(PACKAGE_DIR)])
        if not ok:
            print("  ERROR: Blender rejected the package:")
            for line in output.strip().splitlines()[:12]:
                print(f"    {line}")
            return 1
        print("  Blender validation passed")

    if args.check:
        files = collect_files(PACKAGE_DIR)
        print(f"  {len(files)} files would be shipped")
        print("\ncheck only, no archive written")
        return 0

    out_dir = Path(args.out) if args.out else PACKAGE_DIR.parent / "builds"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"{manifest['id']}-{manifest['version']}"
    target = out_dir / f"{stem}.zip"
    if target.exists():
        target.unlink()

    if blender:
        ok, output = build_with_blender(blender, PACKAGE_DIR, target)
        if not ok or not target.exists():
            print("  ERROR: Blender's build failed:")
            for line in output.strip().splitlines()[:12]:
                print(f"    {line}")
            return 1
        builder = "Blender"
    else:
        build_with_python(PACKAGE_DIR, target)
        builder = "fallback"

    with zipfile.ZipFile(target) as archive:
        count = len(archive.namelist())
    print(f"\nwrote {target}")
    print(f"  built by {builder}: {count} entries, {target.stat().st_size} bytes")

    problems = verify_zip(target, PACKAGE_DIR.name)
    if problems:
        for problem in problems:
            print(f"  ERROR: {problem}")
        return 1
    print("  verified: manifest at root, archive flat, no bytecode caches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
