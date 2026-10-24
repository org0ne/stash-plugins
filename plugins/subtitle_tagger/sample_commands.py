#!/usr/bin/env python3
"""Sample WhisperJAV/Whisper command sidecars for auditing.

Walks one or more source trees for ``*-whisper-command.txt`` and
``*-whisperjav-command.txt`` files and copies a representative subset into a
target directory, spread evenly across the span of file dates so that older
tooling (faster-whisper-xxl, whisper-ctranslate, ...) is as likely to be
represented as recent runs.

Copies are renamed ``<date>_<seq>_<original>`` so they sort chronologically, and
a manifest.tsv records where each one came from.

Examples:
    # 60 examples spread across the whole history
    ./sample_commands.py /mnt/media --dest ./audit -n 60

    # See the breakdown without copying anything
    ./sample_commands.py /mnt/media --dest ./audit --dry-run

    # Prioritise distinct command shapes over calendar spread
    ./sample_commands.py /mnt/media --dest ./audit -n 60 --by-variant
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subtitle_tagger import read_command_file  # noqa: E402

SUFFIXES = ("-whisperjav-command.txt", "-whisper-command.txt")

# Any long or short option name appearing in the recorded command.
FLAG_RE = re.compile(r"(?<![\w./-])--?([A-Za-z][\w-]*)")


def file_time(path: Path) -> float:
    """Creation time where the platform exposes it, else last modification."""
    st = path.stat()
    # st_birthtime exists on macOS and on Linux only for Python 3.12+ builds
    # where statx is wired up; elsewhere mtime is the closest available proxy.
    return getattr(st, "st_birthtime", None) or st.st_mtime


# A record often opens with a bare timestamp; the tool line follows it.
TIMESTAMP_LINE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*$")
VERSIONED_TOOL_RE = re.compile(r"^\s*([A-Za-z][\w.-]*)\s+v?[\d.]+\S*\s*$")


def tool_name(text: str) -> str:
    """The tool a sidecar records, across every generation of the format.

    Handles "whisper-ctranslate2 0.2.4" header lines, WhisperJAV's key/value
    preamble, the bare "script version=" wrappers, and a plain command line.
    """
    lines = [l.strip().lstrip("\ufeff").strip()
             for l in text.splitlines() if l.strip()]
    if not lines:
        return "(empty)"

    if any("whisperjav" in l.lower() for l in lines[:6]):
        return "whisperjav"

    for line in lines[:4]:
        if TIMESTAMP_LINE_RE.match(line):
            continue
        matched = VERSIONED_TOOL_RE.match(line)
        if matched and not line.lower().startswith("script version"):
            return matched.group(1)

    for line in lines[:6]:
        if line.lower().startswith("script version"):
            return f"script {line.split('=', 1)[-1].strip()}"

    for line in lines:
        if TIMESTAMP_LINE_RE.match(line) or line.startswith("-"):
            continue
        token = line.split()[0].strip("\"'")
        if Path(token).name in ("python", "python3", "py"):
            parts = line.split()
            token = (parts[2] if len(parts) > 2 and parts[1] == "-m"
                     else parts[1] if len(parts) > 1 else token)
        return Path(token.strip("\"'")).name
    return "(unrecognised)"


def flag_set(text: str) -> frozenset[str]:
    """Every distinct option name used, lowercased."""
    text = re.sub(r"--initial[-_]prompt.*$", " ", text, flags=re.M | re.I)
    return frozenset(f.lower() for f in FLAG_RE.findall(text))


def find_sidecars(roots: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        for suffix in SUFFIXES:
            found.update(root.rglob("*" + suffix))
    return sorted(found)


def time_buckets(items: list[tuple], count: int) -> list[list]:
    """Split items into `count` equal-width buckets across the date span."""
    times = [t for t, _, _ in items]
    low, high = min(times), max(times)
    span = high - low
    buckets: list[list] = [[] for _ in range(count)]
    for item in items:
        index = 0 if span <= 0 else min(int((item[0] - low) / span * count), count - 1)
        buckets[index].append(item)
    return buckets


def variant_buckets(items: list[tuple]) -> list[list]:
    """Group items by (tool, set of flags used)."""
    groups: dict[tuple, list] = {}
    for item in items:
        groups.setdefault((item[2]["tool"], item[2]["flags"]), []).append(item)
    return list(groups.values())


def round_robin(buckets: list[list], count: int, rng: random.Random) -> list[tuple]:
    """Take one from each non-empty bucket per pass until `count` are chosen.

    Every bucket contributes a sample before any bucket contributes a second, so
    a sparse era of the timeline is never crowded out by a busy one.
    """
    pool = [list(b) for b in buckets]
    for bucket in pool:
        rng.shuffle(bucket)
    selected: list[tuple] = []
    while len(selected) < count and any(pool):
        for bucket in pool:
            if not bucket:
                continue
            selected.append(bucket.pop())
            if len(selected) == count:
                break
    return sorted(selected, key=lambda item: item[0])


def histogram(items: list[tuple], selected: list[tuple], bins: int = 12) -> str:
    """Text histogram of all files vs sampled files over time."""
    times = [t for t, _, _ in items]
    low, high = min(times), max(times)
    span = high - low
    if span <= 0:
        return "  (all files share one timestamp)"

    chosen = {p for _, p, _ in selected}
    lines = []
    for index in range(bins):
        start = low + span * index / bins
        end = low + span * (index + 1) / bins
        total = sum(1 for t, _, _ in items if start <= t < end or
                    (index == bins - 1 and t == high))
        taken = sum(1 for t, p, _ in items if (start <= t < end or
                    (index == bins - 1 and t == high)) and p in chosen)
        label = datetime.fromtimestamp(start).strftime("%Y-%m")
        bar = "#" * min(40, taken) if taken else ""
        lines.append(f"  {label}  {total:6d} files  {taken:4d} sampled  {bar}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy a representative sample of whisper command sidecars.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sources", nargs="+", type=Path,
                        help="directories to search recursively")
    parser.add_argument("--dest", required=True, type=Path,
                        help="directory to copy the sampled files into")
    parser.add_argument("-n", "--count", type=int, default=50,
                        help="number of examples to copy (default: 50)")
    parser.add_argument("--by-variant", action="store_true",
                        help="spread across distinct command shapes rather than dates")
    parser.add_argument("--seed", type=int, default=0,
                        help="RNG seed, for a reproducible sample (default: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the breakdown without copying")
    args = parser.parse_args(argv)

    missing = [s for s in args.sources if not s.is_dir()]
    if missing:
        parser.error("not a directory: " + ", ".join(str(m) for m in missing))

    paths = find_sidecars(args.sources)
    if not paths:
        print("No sidecar files found.")
        return 0

    items = []
    unreadable = 0
    for path in paths:
        text = read_command_file(path)
        if text is None:
            unreadable += 1
            continue
        items.append((file_time(path), path,
                      {"tool": tool_name(text), "flags": flag_set(text)}))

    if not items:
        print(f"Found {len(paths)} file(s) but none could be read.")
        return 1

    times = [t for t, _, _ in items]
    fmt = "%Y-%m-%d"
    print(f"Found     : {len(items)} sidecar file(s)"
          + (f" ({unreadable} unreadable or not text)" if unreadable else ""))
    print(f"Date range: {datetime.fromtimestamp(min(times)).strftime(fmt)}"
          f"  ->  {datetime.fromtimestamp(max(times)).strftime(fmt)}")

    tools = Counter(meta["tool"] for _, _, meta in items)
    print(f"\nTools seen ({len(tools)}):")
    for name, hits in tools.most_common():
        print(f"  {hits:6d}  {name}")

    shapes = Counter((meta["tool"], meta["flags"]) for _, _, meta in items)
    print(f"\nDistinct command shapes: {len(shapes)}")

    rng = random.Random(args.seed)
    buckets = (variant_buckets(items) if args.by_variant
               else time_buckets(items, args.count))
    selected = round_robin(buckets, args.count, rng)

    mode = (f"{len(buckets)} distinct command shapes" if args.by_variant
            else f"{args.count} equal date buckets")
    print(f"\nSampled {len(selected)} file(s) across {mode}.")
    print("Chart below uses its own 12 bins, so a bin may read 0 even though "
          "every\nnon-empty sampling bucket contributed:")
    print(histogram(items, selected))

    picked_tools = Counter(meta["tool"] for _, _, meta in selected)
    print("\nTools represented in the sample:")
    for name, hits in picked_tools.most_common():
        print(f"  {hits:6d}  {name}")
    skipped = set(tools) - set(picked_tools)
    if skipped:
        print(f"  NOT sampled: {', '.join(sorted(skipped))}"
              f"  (raise -n, or use --by-variant)")

    if args.dry_run:
        print("\n[dry-run] nothing copied")
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    manifest = args.dest / "manifest.tsv"
    with manifest.open("w", encoding="utf-8") as handle:
        handle.write("copied_as\tmodified\ttool\tn_flags\toriginal_path\n")
        for seq, (stamp, path, meta) in enumerate(selected, start=1):
            date = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
            target = args.dest / f"{date}_{seq:04d}_{path.name}"
            shutil.copy2(path, target)
            handle.write(f"{target.name}\t{datetime.fromtimestamp(stamp).isoformat()}"
                         f"\t{meta['tool']}\t{len(meta['flags'])}\t{path}\n")

    print(f"\nCopied {len(selected)} file(s) to {args.dest}")
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
