#!/usr/bin/env python3
"""
jasna_batch_upscale.py -- wrapper the bridge runs around Jasna (batch.wrapper).

The bridge calls this with Jasna's whole command line:

  jasna_batch_upscale.py /path/to/jasna --input SRC --output OUT
      --working-directory WORK ... flags

With --upscale in the flags, the source is first upscaled into WORK with the
method named by core.UPSCALE_METHOD, then this process becomes Jasna with
--upscale removed and --input pointing at the upscaled file. Without it, a
source whose audio an MP4 cannot hold (WMA Pro in a .wmv, seen 2026-11-03:
Jasna copies the audio and failed at compose, 46 min in) is first remuxed
into WORK with that audio as AAC and the video copied; any other source goes
straight to Jasna. With --auto-lut, jasna_batch_lut.py first measures the
source's colour cast and writes <stem>.lut.cube/.lut.json next to it, and the
flag becomes --lut <that file> (or goes away when the source needs none). The bridge sees one process from start to finish: its GPU lock,
cancel, pause, timeout, restart adoption and removal of WORK all apply to
both stages, and the job log holds what happened. Progress lines are shaped
like Jasna's so the bridge reads them the same way.

"--check" reports whether this host can run the method and the LUT step
(bridge /batch/check).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
from fractions import Fraction
from typing import Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import jasna_batch_core as core  # noqa: E402

TOOLKIT = os.path.expanduser(os.environ.get("JASNA_UPSCALE_TOOLKIT") or "~/Applications/upscale-toolkit")
LUT_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "jasna_batch_lut.py")

# How each method runs in the toolkit. HEVC QP 20 stays around 49 dB of
# lossless at 16-30 MB/min, 0.005-0.008 GB per output gigapixel; H.264 QP 16
# took 2.4-2.7x that for under 1 dB more. After Jasna's own CQ 25 encode the
# two differ by 0.5 dB (measured 2026-11-07 on 480p and 720p sources).
METHODS: Dict[str, dict] = {
    "qtgmc-compact-2x": {
        "scale": 2,
        "mode": "deshimmer",
        "model": "models/2xNomosUni_compact_multijpg_ldl.safetensors",
        "encode": "-c:v hevc_nvenc -preset p5 -tune hq -rc constqp -qp 20 -pix_fmt yuv420p",
        "gb_per_gigapixel": 0.02,   # 2.5x the noisiest sample, for the free-space check
    },
}
METHOD = METHODS[core.UPSCALE_METHOD]
MIN_FREE_AFTER_GB = 10
TICK_JITTER_S = 0.0015      # a frame step may stray this far from the usual one: 1 ms timestamps (WMV) alternate 33/34 ms
MAX_IRREGULAR = 0.01        # share of frame steps that may be gaps or bursts (dropped frames) before a source counts as VFR
STANDARD_RATES = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                  Fraction(50), Fraction(60000, 1001), Fraction(60)]
MAX_SNAP_ERROR = 0.002      # how close a measured rate must be to a standard one to take it
MAX_DURATION_ERROR_S = 0.5  # how far the upscaled video may end from the source's end
ZIMG_MATRIX = {"bt709": "709", "smpte170m": "170m", "bt470bg": "470bg"}


class UpscaleError(Exception):
    pass


def log(message: str, stage: str = "upscale") -> None:
    print(f"[{stage}] {message}", flush=True)


def option(argv: List[str], name: str) -> Optional[str]:
    for i, arg in enumerate(argv[:-1]):
        if arg == name:
            return argv[i + 1]
    return None


def jasna_command(argv: List[str], new_input: str) -> List[str]:
    """Jasna's command line with --upscale gone and --input replaced."""
    out = [a for a in argv if a not in core.UPSCALE_FLAGS]
    out[out.index("--input") + 1] = new_input
    return out


def _timestamp(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


class Timing(NamedTuple):
    rate: Fraction
    span: float   # seconds from the first frame to the end of the last
    frames: int   # frames the upscaled video will have
    cfr: bool     # the pipeline must place frames by timestamp (dropped frames would otherwise pull the audio out of sync)


def _snap_rate(step: float, avg_rate: str) -> Fraction:
    """The frame rate for a measured frame step: a standard rate or the
    container's when one is that close, else the measurement itself."""
    try:
        candidates = [Fraction(avg_rate)] + STANDARD_RATES
    except (ValueError, ZeroDivisionError):
        candidates = STANDARD_RATES
    for rate in candidates:
        if rate > 0 and abs(float(rate) * step - 1) <= MAX_SNAP_ERROR:
            return rate
    return Fraction(1 / step).limit_denominator(1001)


def frame_timing(times: List[float], avg_rate: str) -> Timing:
    """How to upscale a source with these video packet timestamps. Steps of
    one frame, give or take the timestamps' rounding, set the rate; a few
    longer or shorter steps (dropped frames) are fine, and make the pipeline
    place frames by timestamp when they would drift from the audio by half a
    frame. Raises UpscaleError for a source whose rate really changes."""
    times = sorted(times)
    if len(times) < 2:
        raise UpscaleError("the source has no readable video timestamps")
    steps = [b - a for a, b in zip(times, times[1:])]
    usual = statistics.median(steps)
    regular = [s for s in steps if abs(s - usual) <= TICK_JITTER_S]
    if usual <= 0 or len(regular) < len(steps) * (1 - MAX_IRREGULAR):
        raise UpscaleError(f"variable frame rate source ({len(times)} packets over {times[-1] - times[0]:.1f}s, "
                           f"{len(steps) - len(regular)} of {len(steps)} frame steps differ from the usual "
                           f"{usual * 1000:.2f} ms); upscaling it would drift from the audio")
    rate = _snap_rate(statistics.fmean(regular), avg_rate)
    period = 1 / float(rate)
    span = times[-1] - times[0] + period
    drift = max(abs(t - times[0] - i * period) for i, t in enumerate(times))
    if drift <= period / 2:
        return Timing(rate, span, len(times), False)
    return Timing(rate, span, round(span / period), True)


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


def run_pipeline(source: str, output: str, rate: Fraction, stream: dict, cfr: bool, frames: int) -> None:
    full_range = stream.get("color_range") == "pc" or str(stream.get("pix_fmt") or "").startswith("yuvj")
    sar = (stream.get("sample_aspect_ratio") or "").replace(":", "/")
    cmd = [os.path.join(TOOLKIT, ".venv/bin/python"), "vs_pipeline.py", "-i", source, "-o", output,
           "--model", METHOD["model"], "--mode", METHOD["mode"], "--fps", str(rate),
           "--matrix", source_matrix(stream), "--range", "full" if full_range else "limited",
           "--encode", " ".join([METHOD["encode"], *color_flags(stream)]), "--progress"]
    if sar and sar not in ("0/1", "1/1"):
        cmd += ["--sar", sar]
    if cfr:
        cmd.append("--cfr")
    # Regular packets can still decode to fewer frames (damaged ones, MPEG-4
    # not-coded frames in AVI); only the decoder can tell, so the pipeline
    # places frames by timestamp itself then. Either way it stops before
    # upscaling when the count would still leave the audio behind.
    cmd += ["--expect-frames", str(frames)]
    log(" ".join(cmd))
    if subprocess.run(cmd, cwd=TOOLKIT, stdin=subprocess.DEVNULL).returncode:
        raise UpscaleError("the upscale pipeline failed (see the lines above)")


def fits_mp4(path: str, index: int) -> bool:
    """Whether audio stream `index` can be stream-copied into an MP4, as
    Jasna does with its input's audio. ffmpeg's own muxer answers, so no
    codec list here goes stale."""
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", f"0:a:{index}", "-c", "copy",
                           "-t", "1", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "-y", os.devnull],
                          capture_output=True, text=True)
    return done.returncode == 0


def audio_args(path: str) -> Optional[List[str]]:
    """ffmpeg audio codec options that make every audio stream MP4-safe:
    copied when it already is, AAC otherwise (bitrate as the bridge's
    audio fix-up picks it). None when nothing needs converting."""
    streams = json.loads(ffprobe("-select_streams", "a", "-show_entries", "stream=codec_name,channels",
                                 "-of", "json", path)).get("streams") or []
    args, convert = [], []
    for i, stream in enumerate(streams):
        if fits_mp4(path, i):
            args += [f"-c:a:{i}", "copy"]
        else:
            channels = int(stream.get("channels") or 2)
            args += [f"-c:a:{i}", "aac", f"-b:a:{i}", f"{min(96 * channels, 512)}k"]
            convert.append(stream.get("codec_name") or "?")
    if convert:
        log(f"audio {', '.join(convert)} cannot go in an MP4; converting to AAC", "audio")
    return args if convert else None


def add_audio(video: str, source: str, output: str, delay: float, audio: Optional[List[str]]) -> None:
    """Jasna takes its audio from its input, so put the source's back. delay
    keeps the video where it was relative to the audio."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-itsoffset", f"{max(delay, 0):.6f}", "-i", video,
                           "-i", source, "-map", "0:v", "-map", "1:a?", "-c:v", "copy",
                           *(audio or ["-c:a", "copy"]), output],
                          stdin=subprocess.DEVNULL)
    if done.returncode:
        raise UpscaleError(f"could not mux the source's audio into the upscaled file (ffmpeg {done.returncode})")


def remux_audio(argv: List[str], audio: List[str]) -> str:
    """The source with its video copied and its audio made MP4-safe, under
    Jasna's working directory. Returns the new file."""
    source, work = option(argv, "--input"), option(argv, "--working-directory")
    if not source or not work:
        raise UpscaleError("--input and --working-directory are required")
    folder = os.path.join(work, "audio")
    os.makedirs(folder, exist_ok=True)
    need = os.path.getsize(source) * 1.2 / 1e9 + MIN_FREE_AFTER_GB
    free = shutil.disk_usage(folder).free / 1e9
    if free < need:
        raise UpscaleError(f"only {free:.1f} GB free under {folder}; the audio remux may need {need:.1f} GB")
    output = os.path.join(folder, "input.mkv")
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-map", "0:v", "-map", "0:a",
                           "-map_metadata", "0", "-c:v", "copy", *audio, output], stdin=subprocess.DEVNULL)
    if done.returncode:
        raise UpscaleError(f"the audio remux failed (ffmpeg {done.returncode})")
    durations = [float(ffprobe("-show_entries", "format=duration", "-of", "csv=p=0", p).strip() or 0)
                 for p in (source, output)]
    if abs(durations[0] - durations[1]) > MAX_DURATION_ERROR_S:
        raise UpscaleError(f"the remuxed file runs {durations[1]:.2f}s, the source {durations[0]:.2f}s")
    log(f"done: {os.path.getsize(output) / 1e9:.2f} GB, {durations[1]:.1f}s", "audio")
    return output


def upscale(argv: List[str], audio: Optional[List[str]]) -> str:
    """Returns the upscaled file. Everything goes under Jasna's working
    directory, which the bridge removes when the job ends."""
    source, work = option(argv, "--input"), option(argv, "--working-directory")
    if not source or not work:
        raise UpscaleError("--input and --working-directory are required")
    stream, container = probe(source)
    if int(stream["height"]) > core.UPSCALE_MAX_HEIGHT:
        raise UpscaleError(f"source is {stream['height']}p; {core.UPSCALE_FLAG} takes {core.UPSCALE_MAX_HEIGHT}p and below")
    # The queue page's first upscale step; vs_pipeline carries it on while
    # BestSource indexes the file (both read the whole source once).
    print("Upscale 1/2 read:   0%| source file", flush=True)
    times = packet_times(source)
    timing = frame_timing(times, stream.get("avg_frame_rate") or "")
    folder = os.path.join(work, "upscale")
    os.makedirs(folder, exist_ok=True)
    check_space(folder, timing.frames, stream)
    video, muxed = os.path.join(folder, "video.mkv"), os.path.join(folder, "input.mkv")
    log(f"{core.UPSCALE_METHOD}: {stream['width']}x{stream['height']} at {float(timing.rate):.3f} fps, "
        f"{len(times)} packets, {timing.span:.1f}s")
    if timing.cfr:
        log(f"frames dropped in the source; placing them by timestamp ({timing.frames - len(times):+d} frames)")
    run_pipeline(source, video, timing.rate, stream, timing.cfr, timing.frames)
    got = float(ffprobe("-show_entries", "format=duration", "-of", "csv=p=0", video).strip() or 0)
    if abs(got - timing.span) > MAX_DURATION_ERROR_S:
        raise UpscaleError(f"the upscaled video runs {got:.2f}s, the source {timing.span:.2f}s; "
                           "it would drift from the audio")
    first = min(times)
    add_audio(video, source, muxed, first - (_timestamp(str(container.get("start_time"))) or 0.0), audio)
    os.remove(video)
    log(f"done: {os.path.getsize(muxed) / 1e9:.2f} GB, {got:.1f}s")
    return muxed


def lut_python() -> Optional[str]:
    """A Python with numpy for jasna_batch_lut.py: JASNA_LUT_PYTHON, else
    this one if it has numpy, else upscale-toolkit's venv."""
    if os.environ.get("JASNA_LUT_PYTHON"):
        return os.path.expanduser(os.environ["JASNA_LUT_PYTHON"])
    if importlib.util.find_spec("numpy"):
        return sys.executable
    venv = os.path.join(TOOLKIT, ".venv/bin/python")
    return venv if os.path.exists(venv) else None


def with_auto_lut(argv: List[str]) -> List[str]:
    """Jasna's command line with --auto-lut replaced by --lut <this source's
    LUT>, or just gone when the source needs none."""
    argv = [a for a in argv if a not in core.AUTO_LUT_FLAGS]
    if any(a == "--lut" or a.startswith("--lut=") for a in argv):
        raise UpscaleError(f"the preset has both {core.AUTO_LUT_FLAG} and --lut; use one")
    source, work = option(argv, "--input"), option(argv, "--working-directory")
    if not source or not work:
        raise UpscaleError("--input and --working-directory are required")
    python = lut_python()
    if not python:
        raise UpscaleError("no Python with numpy for the LUT step (set JASNA_LUT_PYTHON)")
    proc = subprocess.Popen([python, LUT_SCRIPT, source, "--fallback-dir", os.path.join(work, "lut")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    result = None
    for line in proc.stdout:
        if line.startswith("RESULT "):
            result = json.loads(line[len("RESULT "):])
        else:
            print(line, end="", flush=True)
    proc.wait()
    if not result or "error" in result or proc.returncode:
        raise UpscaleError((result or {}).get("error") or f"the LUT step failed (exit {proc.returncode})")
    return argv + ["--lut", result["lut"]] if result.get("lut") else argv


def check() -> int:
    """One line for the bridge's check: exit 0 when the upscale method and
    the --auto-lut step can both run here."""
    needed = [os.path.join(TOOLKIT, p) for p in (".venv/bin/python", "vs_pipeline.py", METHOD["model"])]
    missing = [p for p in needed if not os.path.exists(p)]
    missing += [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    python = lut_python()
    if not python:
        missing.append(f"a Python with numpy for {core.AUTO_LUT_FLAG} (JASNA_LUT_PYTHON)")
    if not os.path.exists(core.AUTO_LUT_BASE):
        missing.append(f"{core.AUTO_LUT_BASE} (JASNA_LUT_BASE)")
    if missing:
        print(f"missing: {', '.join(missing)}")
        return 1
    print(f"{core.UPSCALE_METHOD} in {TOOLKIT}; {core.AUTO_LUT_FLAG}: {os.path.basename(core.AUTO_LUT_BASE)} via {python}")
    return 0


def main(argv: List[str]) -> int:
    if argv == ["--check"]:
        return check()
    if not argv:
        print("usage: jasna_batch_upscale.py --check | jasna [flags]", file=sys.stderr)
        return 2
    source = option(argv, "--input")
    if core.has_auto_lut(argv):
        # First: a bad source fails here in a minute, not after an upscale.
        try:
            argv = with_auto_lut(argv)
        except (UpscaleError, OSError, ValueError) as err:
            log(f"FAILED: {err}", "lut")
            return 1
    if core.has_upscale(argv):
        try:
            argv = jasna_command(argv, upscale(argv, audio_args(source) if source else None))
        except UpscaleError as err:
            log(f"FAILED: {err}")
            return 1
    else:
        try:
            audio = audio_args(source) if source else None
        except UpscaleError as err:
            # Jasna reports an unreadable source better than this probe does.
            log(f"audio not checked: {err}", "audio")
            audio = None
        if audio:
            try:
                argv = jasna_command(argv, remux_audio(argv, audio))
            except UpscaleError as err:
                log(f"FAILED: {err}", "audio")
                return 1
    sys.stdout.flush()
    os.execv(argv[0], argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
