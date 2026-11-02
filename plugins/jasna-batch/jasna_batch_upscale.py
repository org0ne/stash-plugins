#!/usr/bin/env python3
"""
jasna_batch_upscale.py -- wrapper the bridge runs around Jasna (batch.wrapper).

The bridge calls this with Jasna's whole command line:

  jasna_batch_upscale.py /path/to/jasna --input SRC --output OUT
      --working-directory WORK ... flags

Without -upscale in the flags it just becomes Jasna. With it, the source is
first upscaled into WORK with the method named by core.UPSCALE_METHOD, then
this process becomes Jasna with -upscale removed and --input pointing at the
upscaled file. The bridge sees one process from start to finish: its GPU lock,
cancel, pause, timeout, restart adoption and removal of WORK all apply to
both stages, and the job log holds what happened. Progress lines are shaped
like Jasna's so the bridge reads them the same way.

"--check" reports whether this host can run the method (bridge /batch/check).
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import jasna_batch_core as core  # noqa: E402

TOOLKIT = os.path.expanduser(os.environ.get("JASNA_UPSCALE_TOOLKIT") or "~/Applications/upscale-toolkit")

# How each method runs in the toolkit. QP 16 stays around 50 dB of lossless at
# 52-80 MB/min where lossless takes 860-1800 MB/min (measured 2026-11-01).
METHODS: Dict[str, dict] = {
    "qtgmc-compact-2x": {
        "scale": 2,
        "mode": "deshimmer",
        "model": "models/2xNomosUni_compact_multijpg_ldl.safetensors",
        "encode": "-c:v h264_nvenc -preset p5 -tune hq -rc constqp -qp 16 -pix_fmt yuv420p",
        "gb_per_gigapixel": 0.06,   # twice the noisiest sample, for the free-space check
    },
}
METHOD = METHODS[core.UPSCALE_METHOD]
MIN_FREE_AFTER_GB = 10
MAX_RATE_ERROR = 0.01       # packets may stray this far from an even spacing before a source counts as VFR
MAX_DURATION_ERROR_S = 0.5  # how far the upscaled video may end from the source's end
ZIMG_MATRIX = {"bt709": "709", "smpte170m": "170m", "bt470bg": "470bg"}


class UpscaleError(Exception):
    pass


def log(message: str) -> None:
    print(f"[upscale] {message}", flush=True)


def option(argv: List[str], name: str) -> Optional[str]:
    for i, arg in enumerate(argv[:-1]):
        if arg == name:
            return argv[i + 1]
    return None


def jasna_command(argv: List[str], new_input: str) -> List[str]:
    """Jasna's command line with -upscale gone and --input replaced."""
    out = [a for a in argv if a != core.UPSCALE_FLAG]
    out[out.index("--input") + 1] = new_input
    return out


def _timestamp(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def frame_timing(times: List[float], avg_rate: str) -> Tuple[Fraction, float]:
    """(frame rate, span in seconds) from the video packets' timestamps.
    Raises UpscaleError when they are not evenly spaced: the pipeline writes
    a constant rate, which would drift from a variable-rate source's audio."""
    times = sorted(times)
    if len(times) < 2:
        raise UpscaleError("the source has no readable video timestamps")
    step = statistics.median(b - a for a, b in zip(times, times[1:]))
    span = times[-1] - times[0] + step
    if step <= 0 or abs(len(times) * step - span) > span * MAX_RATE_ERROR:
        raise UpscaleError(f"variable frame rate source ({len(times)} packets over {span:.1f}s, "
                           f"typical spacing {step * 1000:.2f} ms); upscaling it would drift from the audio")
    try:
        rate = Fraction(avg_rate)
    except (ValueError, ZeroDivisionError):
        rate = Fraction(0)
    if abs(float(rate) * step - 1) > MAX_RATE_ERROR:
        rate = Fraction(1 / step).limit_denominator(1001)
    return rate, span


def source_matrix(stream: dict) -> str:
    """The source's YUV matrix: its tag, else the usual one for its height."""
    return ZIMG_MATRIX.get(stream.get("color_space") or "", "170m" if int(stream["height"]) <= 576 else "709")


def color_flags(stream: dict) -> List[str]:
    """ffmpeg options that tag the upscaled file like the source: from an
    untagged file, a player would guess from the new height."""
    tag = {"709": "bt709", "170m": "smpte170m", "470bg": "bt470bg"}[source_matrix(stream)]
    flags = ["-colorspace", tag]
    for key, flag in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc")):
        if stream.get(key) and stream[key] != "unknown":
            flags += [flag, stream[key]]
    return flags


def ffprobe(*args: str) -> str:
    done = subprocess.run(["ffprobe", "-v", "error", *args], capture_output=True, text=True)
    if done.returncode:
        raise UpscaleError(f"ffprobe failed: {done.stderr.strip()[:300]}")
    return done.stdout


def packet_times(path: str) -> List[float]:
    """One timestamp per video packet. AVIs with packed B-frames have no pts
    on many packets, so fall back on dts."""
    times = []
    for line in ffprobe("-select_streams", "v:0", "-show_entries", "packet=pts_time,dts_time",
                        "-of", "csv=p=0", path).splitlines():
        parts = line.strip().strip(",").split(",")
        stamp = _timestamp(parts[0]) if parts[0:1] else None
        if stamp is None and len(parts) > 1:
            stamp = _timestamp(parts[1])
        if stamp is not None:
            times.append(stamp)
    return times


def probe(path: str) -> Tuple[dict, dict]:
    info = json.loads(ffprobe("-select_streams", "v:0", "-show_entries",
                              "stream=width,height,pix_fmt,sample_aspect_ratio,avg_frame_rate,color_space,"
                              "color_primaries,color_transfer,color_range:format=start_time", "-of", "json", path))
    if not info.get("streams"):
        raise UpscaleError("the source has no video stream")
    return info["streams"][0], info.get("format") or {}


def check_space(work: str, frames: int, stream: dict) -> None:
    scale = METHOD["scale"]
    gigapixels = frames * int(stream["width"]) * int(stream["height"]) * scale * scale / 1e9
    need = gigapixels * METHOD["gb_per_gigapixel"] + MIN_FREE_AFTER_GB
    usage = shutil.disk_usage(work)
    if usage.free / 1e9 < need:
        raise UpscaleError(f"only {usage.free / 1e9:.1f} GB free under {work}; the upscale may need {need:.1f} GB")


def run_pipeline(source: str, output: str, rate: Fraction, stream: dict) -> None:
    full_range = stream.get("color_range") == "pc" or str(stream.get("pix_fmt") or "").startswith("yuvj")
    sar = (stream.get("sample_aspect_ratio") or "").replace(":", "/")
    cmd = [os.path.join(TOOLKIT, ".venv/bin/python"), "vs_pipeline.py", "-i", source, "-o", output,
           "--model", METHOD["model"], "--mode", METHOD["mode"], "--fps", str(rate),
           "--matrix", source_matrix(stream), "--range", "full" if full_range else "limited",
           "--encode", " ".join([METHOD["encode"], *color_flags(stream)]), "--progress"]
    if sar and sar not in ("0/1", "1/1"):
        cmd += ["--sar", sar]
    log(" ".join(cmd))
    if subprocess.run(cmd, cwd=TOOLKIT, stdin=subprocess.DEVNULL).returncode:
        raise UpscaleError("the upscale pipeline failed (see the lines above)")


def add_audio(video: str, source: str, output: str, delay: float) -> None:
    """Jasna takes its audio from its input, so put the source's back. delay
    keeps the video where it was relative to the audio."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-itsoffset", f"{max(delay, 0):.6f}", "-i", video,
                           "-i", source, "-map", "0:v", "-map", "1:a?", "-c", "copy", output],
                          stdin=subprocess.DEVNULL)
    if done.returncode:
        raise UpscaleError(f"could not mux the source's audio into the upscaled file (ffmpeg {done.returncode})")


def upscale(argv: List[str]) -> str:
    """Returns the upscaled file. Everything goes under Jasna's working
    directory, which the bridge removes when the job ends."""
    source, work = option(argv, "--input"), option(argv, "--working-directory")
    if not source or not work:
        raise UpscaleError("--input and --working-directory are required")
    stream, container = probe(source)
    if int(stream["height"]) > core.UPSCALE_MAX_HEIGHT:
        raise UpscaleError(f"source is {stream['height']}p; {core.UPSCALE_FLAG} takes {core.UPSCALE_MAX_HEIGHT}p and below")
    times = packet_times(source)
    rate, span = frame_timing(times, stream.get("avg_frame_rate") or "")
    folder = os.path.join(work, "upscale")
    os.makedirs(folder, exist_ok=True)
    check_space(folder, len(times), stream)
    video, muxed = os.path.join(folder, "video.mkv"), os.path.join(folder, "input.mkv")
    log(f"{core.UPSCALE_METHOD}: {stream['width']}x{stream['height']} at {float(rate):.3f} fps, "
        f"{len(times)} packets, {span:.1f}s")
    run_pipeline(source, video, rate, stream)
    got = float(ffprobe("-show_entries", "format=duration", "-of", "csv=p=0", video).strip() or 0)
    if abs(got - span) > MAX_DURATION_ERROR_S:
        raise UpscaleError(f"the upscaled video runs {got:.2f}s, the source {span:.2f}s; it would drift from the audio")
    first = min(times)
    add_audio(video, source, muxed, first - (_timestamp(str(container.get("start_time"))) or 0.0))
    os.remove(video)
    log(f"done: {os.path.getsize(muxed) / 1e9:.2f} GB, {got:.1f}s")
    return muxed


def check() -> int:
    """One line for the bridge's check: exit 0 when the method can run here."""
    needed = [os.path.join(TOOLKIT, p) for p in (".venv/bin/python", "vs_pipeline.py", METHOD["model"])]
    missing = [p for p in needed if not os.path.exists(p)]
    missing += [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        print(f"missing: {', '.join(missing)}")
        return 1
    print(f"{core.UPSCALE_METHOD} in {TOOLKIT}")
    return 0


def main(argv: List[str]) -> int:
    if argv == ["--check"]:
        return check()
    if not argv:
        print("usage: jasna_batch_upscale.py --check | jasna [flags]", file=sys.stderr)
        return 2
    if core.UPSCALE_FLAG in argv:
        try:
            argv = jasna_command(argv, upscale(argv))
        except UpscaleError as err:
            log(f"FAILED: {err}")
            return 1
    sys.stdout.flush()
    os.execv(argv[0], argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
