#!/usr/bin/env python3
"""Builds a stash plugin-source site: index.yml + one zip per plugin.

Mirrors the schema stashapp/CommunityScripts' build_site.sh produces (id/
name/metadata.description/version/date/path/sha256/requires) so any stash
instance's existing "Community (stable)"-style source config works
unmodified here too — just point it at this repo's Pages URL instead.

Rewritten in Python (vs. that script's bash/grep) for one concrete reason:
grep-based single-line field extraction silently produces an empty
description for a YAML folded/literal block scalar (`description: >`),
which is exactly the style this repo's own plugin manifests use. A real
YAML parser doesn't have that failure mode.

Usage: build_site.py <output-dir>
Expects plugins/<id>/<id>.yml (manifest filename, minus .yml, is the id —
same "filename sets the plugin id" rule stash itself uses when scanning a
local plugins folder).
"""
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

PLUGINS_ROOT = Path(__file__).parent / "plugins"


def git_info(path):
    """Short hash + UTC commit date of the last commit touching `path`."""
    rev = subprocess.run(
        ["git", "log", "-n", "1", "--pretty=format:%h", "--", str(path)],
        capture_output=True, text=True, cwd=path.parent,
    ).stdout.strip()
    date = subprocess.run(
        ["git", "log", "-n", "1", "--date=format-local:%Y-%m-%d %H:%M:%S", "--pretty=format:%ad", "--", str(path)],
        capture_output=True, text=True, cwd=path.parent, env={"TZ": "UTC0"},
    ).stdout.strip()
    return rev or "0000000", date or "1970-01-01 00:00:00"


def build_plugin(plugin_dir, out_dir):
    plugin_id = plugin_dir.name
    manifest_path = plugin_dir / f"{plugin_id}.yml"
    if not manifest_path.exists():
        print(f"  skip {plugin_id}: no {plugin_id}.yml manifest (id must match dir name)")
        return None

    manifest = yaml.safe_load(manifest_path.read_text())
    name = manifest.get("name", plugin_id)
    description = (manifest.get("description") or "").strip()
    yml_version = str(manifest.get("version", "0.0"))

    rev, date = git_info(plugin_dir)
    version = f"{yml_version}-{rev}"

    zip_path = out_dir / f"{plugin_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(plugin_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(plugin_dir))

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    entry = {
        "id": plugin_id,
        "name": name,
        "metadata": {"description": description},
        "version": version,
        "date": date,
        "path": f"{plugin_id}.zip",
        "sha256": sha256,
    }
    requires = manifest.get("ui", {}).get("requires")
    if requires:
        entry["requires"] = requires
    print(f"  built {plugin_id} {version} sha256={sha256[:12]}...")
    return entry


def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <output-dir>", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for plugin_dir in sorted(PLUGINS_ROOT.iterdir()):
        if not plugin_dir.is_dir():
            continue
        print(f"Processing {plugin_dir.name}")
        entry = build_plugin(plugin_dir, out_dir)
        if entry:
            entries.append(entry)

    index_path = out_dir / "index.yml"
    with open(index_path, "w") as f:
        yaml.dump(entries, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"Wrote {index_path} ({len(entries)} plugin(s))")


if __name__ == "__main__":
    main()
