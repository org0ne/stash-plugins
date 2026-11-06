#!/usr/bin/env python3
"""
jasna_batch_lut.py -- per-video colour LUT for presets with --auto-lut.

Older titles often have a yellow/warm cast. For each source this measures

  warmth  ln(R/G) - ln(B/G) of the brightest unclipped, not-too-saturated
          pixels (white-patch style; walls, sheets, clothing), and
  skin    the median ln(R/G) of skin-like pixels,

over FRAMES frames, and writes a LUT that is BASE_LUT (Bombadil MIGD-358)
blended with identity:

  blend = ramp(warmth, WARM_LO -> WARM_HI) * ramp(skin, SKIN_NONE -> SKIN_FULL)

so a neutral video gets none, a warm one the full LUT, and one whose skin is
already red gets none (MIGD-358 adds red to skin; MEYD-057 read too warm).
The cut-offs were judged by eye on 25 videos (2026-11-06; 23 agreed, the
other two are within measurement noise of ones that did). A straight
white-balance (per-channel gains) was tried first and looked washed out.

Next to the source it writes

  <stem>.lut.json   what was measured and the blend (always), and
  <stem>.lut.cube   the LUT (only when blend > 0),

which the plugin's sidecar copy puts next to the restored file too. An
existing <stem>.lut.json is reused as it is: its blend is applied without
measuring again. Set "manual": true and your own "blend" (0 = no LUT, 1 =
full base LUT) to override a video; delete the .lut.json to measure again.

Needs numpy, so the wrapper runs this with a Python that has it (see
jasna_batch_upscale.lut_python). By hand, to measure ahead and review the
sidecars before queueing:

  python jasna_batch_lut.py VIDEO [VIDEO ...]

The last stdout line per source is "RESULT {json}" for the wrapper.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from typing import List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import jasna_batch_core as core  # noqa: E402

BASE_LUT = core.AUTO_LUT_BASE
FRAMES = 80          # under ~30 the skin measurement wanders (6 frames got MEYD-057 wrong)
SAMPLE_W = 320
WARM_LO, WARM_HI = 0.06, 0.18
SKIN_FULL, SKIN_NONE = 0.28, 0.30
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


class LutError(Exception):
    pass


def log(message: str) -> None:
    print(f"[lut] {message}", flush=True)


# ---------------------------------------------------------------- .cube

def read_cube(path: str) -> np.ndarray:
    """3D .cube -> array [b][g][r][3] (file order: red changes fastest)."""
    size, rows = None, []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.split()[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(line.split()[1])
            elif key in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE"):
                continue
            elif key == "LUT_1D_SIZE":
                raise LutError(f"{path}: 1D LUTs cannot be blended")
            else:
                rows.append([float(x) for x in line.split()[:3]])
    if size is None or len(rows) != size ** 3:
        raise LutError(f"{path}: not a 3D .cube ({len(rows)} rows, size {size})")
    return np.array(rows, dtype=np.float32).reshape(size, size, size, 3)


def save_cube(path: str, lut: np.ndarray) -> None:
    """Laid out like the Bombadil files Jasna is known to read: no TITLE,
    LUT_3D_SIZE then the rows (the blend is in the .lut.json)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"LUT_3D_SIZE {lut.shape[0]}\n")
        np.savetxt(f, lut.reshape(-1, 3), fmt="%.6f")
    os.replace(tmp, path)


def blend_lut(base: np.ndarray, k: float) -> np.ndarray:
    """identity + k * (base - identity), on base's own grid."""
    i = np.linspace(0, 1, base.shape[0], dtype=np.float32)
    b, g, r = np.meshgrid(i, i, i, indexing="ij")
    ident = np.stack([r, g, b], -1)
    return np.clip(ident + k * (base - ident), 0, 1)


# ---------------------------------------------------------------- measuring

def ramp(x: float, zero_at: float, one_at: float) -> float:
    return min(1.0, max(0.0, (x - zero_at) / (one_at - zero_at)))


def blend_for(warmth: float, skin: Optional[float]) -> float:
    k = ramp(warmth, WARM_LO, WARM_HI)
    if skin is not None:
        k *= ramp(skin, SKIN_NONE, SKIN_FULL)
    return k


def bright_cast(rgb: np.ndarray, top: float = 0.10, max_sat: float = 0.5):
    """(ln R/G, ln B/G, pixels) of the median of the brightest `top` share of
    unclipped, not near-black, not strongly saturated pixels; None if too few."""
    mx, mn = rgb.max(1), rgb.min(1)
    p = rgb[(mn > 0.06) & (mx < 0.96) & ((mx - mn) / np.maximum(mx, 1e-3) < max_sat)]
    if len(p) < 300:
        return None
    y = p @ LUMA
    p = p[y >= np.quantile(y, 1 - top)]
    return float(np.median(np.log(p[:, 0] / p[:, 1]))), float(np.median(np.log(p[:, 2] / p[:, 1]))), len(p)


def skin_redness(frames: List[np.ndarray]) -> Optional[float]:
    """Median ln(R/G) of skin-like pixels (R>G>B, hue 5-45 deg, moderate
    saturation, not dark or clipped); None if there are too few."""
    px = np.concatenate([f.reshape(-1, 3) for f in frames])
    r, g, b = px.T
    mx, mn = px.max(1), px.min(1)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    hue = np.degrees(np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b))
    skin = (r > g) & (g > b) & (sat > 0.15) & (sat < 0.65) & (hue > 5) & (hue < 45) & (px @ LUMA > 0.2) & (mx < 0.97)
    if skin.sum() < 1000:
        return None
    return float(np.median(np.log(px[skin, 0] / px[skin, 1])))


def video_info(path: str) -> dict:
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "json", path], capture_output=True, text=True)
    if done.returncode:
        raise LutError(f"ffprobe failed: {done.stderr.strip()[:300]}")
    j = json.loads(done.stdout)
    if not j.get("streams"):
        raise LutError("the source has no video stream")
    s = j["streams"][0]
    return {"w": int(s["width"]), "h": int(s["height"]), "duration": float(j["format"].get("duration") or 0)}


def grab(path: str, t: float, w: int, h: int) -> Optional[np.ndarray]:
    sh = max(2, round(h * SAMPLE_W / w / 2) * 2)
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                           "-vf", f"scale={SAMPLE_W}:{sh}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True)
    if done.returncode or len(done.stdout) != SAMPLE_W * sh * 3:
        return None
    return np.frombuffer(done.stdout, np.uint8).reshape(sh, SAMPLE_W, 3).astype(np.float32) / 255


def measure(path: str, frames: int = FRAMES) -> dict:
    info = video_info(path)
    d = info["duration"]
    if d <= 0:
        raise LutError("the source has no duration")
    shots = [f for f in (grab(path, d * (0.05 + 0.9 * (k + 0.5) / frames), info["w"], info["h"])
                         for k in range(frames)) if f is not None]
    casts = [c for c in (bright_cast(f.reshape(-1, 3)) for f in shots) if c]
    if len(casts) < max(10, frames // 4):
        raise LutError(f"only {len(casts)} of {frames} frames could be measured")
    a = np.array(casts)
    rg = float(np.average(a[:, 0], weights=a[:, 2]))
    bg = float(np.average(a[:, 1], weights=a[:, 2]))
    skin = skin_redness(shots)
    return {"size": f"{info['w']}x{info['h']}", "frames": len(shots), "frames_measured": len(casts),
            "rg": rg, "bg": bg, "warmth": rg - bg, "skin_rg": skin, "blend": blend_for(rg - bg, skin)}


# ---------------------------------------------------------------- sidecars

def sidecar_paths(source: str):
    stem = os.path.splitext(source)[0]
    return stem + ".lut.json", stem + ".lut.cube"


def run(source: str, fallback_dir: Optional[str] = None, remeasure: bool = False) -> dict:
    """Make the source's LUT. Returns {"lut": path or None, "blend", "how"}."""
    if not os.path.exists(BASE_LUT):
        raise LutError(f"base LUT not found: {BASE_LUT} (set JASNA_LUT_BASE)")
    json_path, cube_path = sidecar_paths(source)
    record = None
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                record = json.load(f)
            float(record["blend"])
        except (OSError, ValueError, KeyError, TypeError) as err:
            log(f"ignoring unreadable {os.path.basename(json_path)}: {err}")
            record = None
    if record and (record.get("manual") or not remeasure):
        how = "manual" if record.get("manual") else "earlier measurement"
        log(f"using {os.path.basename(json_path)} ({how}): blend {float(record['blend']):.2f}")
    else:
        log(f"measuring {FRAMES} frames")
        m = measure(source)
        log(f"warmth {m['warmth']:+.3f}, skin R/G "
            + (f"{m['skin_rg']:+.3f}" if m["skin_rg"] is not None else "n/a") + f" -> blend {m['blend']:.2f}")
        how = "measured"
        record = {
            "blend": round(m["blend"], 4), "manual": False, "base_lut": os.path.basename(BASE_LUT),
            "_help": 'set "manual": true and your own "blend" (0 = no LUT, 1 = full base LUT) to override; '
                     "delete this file to measure again",
            "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "made_by": "jasna-batch jasna_batch_lut.py",
            "source": os.path.basename(source), **{k: m[k] for k in ("size", "frames", "frames_measured")},
            "warmth": m["warmth"], "skin_rg": m["skin_rg"], "rg": m["rg"], "bg": m["bg"],
            "cutoffs": {"warm_lo": WARM_LO, "warm_hi": WARM_HI, "skin_full": SKIN_FULL, "skin_none": SKIN_NONE},
        }
    blend = max(0.0, float(record["blend"]))

    # Beside the source when its folder is writable, else under the job's
    # working directory (used for this run, not kept).
    target_json, target_cube = json_path, cube_path
    if not os.access(os.path.dirname(source) or ".", os.W_OK):
        if not fallback_dir:
            raise LutError(f"cannot write next to {source}")
        os.makedirs(fallback_dir, exist_ok=True)
        base = os.path.join(fallback_dir, os.path.basename(os.path.splitext(source)[0]))
        target_json, target_cube = base + ".lut.json", base + ".lut.cube"
        log(f"source folder not writable; the LUT goes to {fallback_dir} and is not kept")

    lut_path = None
    if blend > 0:
        base_name = os.path.basename(BASE_LUT)
        if record.get("base_lut") and record["base_lut"] != base_name:
            log(f"note: {os.path.basename(json_path)} was made for {record['base_lut']}; applying {base_name}")
        save_cube(target_cube, blend_lut(read_cube(BASE_LUT), blend))
        lut_path = target_cube
    elif os.path.exists(target_cube):
        os.remove(target_cube)   # a stale LUT from an earlier, higher blend
    record["lut"] = os.path.basename(lut_path) if lut_path else None
    tmp = target_json + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    os.replace(tmp, target_json)
    log(f"{'LUT ' + lut_path if lut_path else 'no LUT needed'} ({how})")
    return {"lut": lut_path, "blend": blend, "how": how}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Per-video colour LUT for --auto-lut presets (see the module docstring).")
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--fallback-dir", help="where the LUT goes when the source folder is not writable")
    ap.add_argument("--remeasure", action="store_true", help="measure again even when a .lut.json exists "
                                                             "(manual ones are still kept)")
    args = ap.parse_args(argv)
    failed = 0
    for source in args.sources:
        try:
            result = run(source, args.fallback_dir, args.remeasure)
        except (LutError, OSError) as err:
            log(f"FAILED for {source}: {err}")
            result, failed = {"error": str(err)}, failed + 1
        print("RESULT " + json.dumps(result), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
