"""
scene.py  —  LAYER 1: SCENE UNDERSTANDING
=========================================
Answers WHERE and HOW, never HOW MUCH.

Gemma (when GEMMA_ENDPOINT is set) locates two regions in each photo:
  * sample_bbox : the thin translucent coloured liquid to measure
  * paper_bbox  : a clean white paper patch NEAR the sample under the SAME
                  light — this is what makes the reading lighting-independent

It also reports usable / layer_quality / align_ok, which Layer 3 turns into a
confidence level. **It never produces the purity number** — that comes only
from the arithmetic in Layer 2.

Everything here degrades gracefully. If Gemma is disabled, unreachable, slow,
returns prose, returns malformed JSON, or returns a nonsense box, we silently
fall back to deterministic auto-detection:
  * sample = the most saturated / most deviant coloured blob
  * paper  = the brightest ~2% of pixels
so the app always produces a measurement.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import colorimetry, config, gemma

logger = logging.getLogger("haldi.scene")

Box = Tuple[int, int, int, int]  # x, y, w, h in pixels


@dataclass
class SceneView:
    """Where to measure in ONE image."""
    sample_bbox: Optional[Box] = None
    paper_bbox: Optional[Box] = None
    usable: bool = True
    reject_reason: str = ""
    layer_quality: str = "unknown"     # thin | thick | paste | unknown
    sample_source: str = "auto"        # gemma | auto | user
    paper_source: str = "auto"


@dataclass
class ScenePair:
    before: SceneView
    after: SceneView
    align_ok: Optional[bool] = None    # None => unknown (Gemma unavailable)
    source: str = "auto"               # "gemma" | "auto"
    raw_response: Optional[str] = None
    parse_error: Optional[str] = None
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic fallback detection (no AI)
# ---------------------------------------------------------------------------
def auto_sample_bbox(image: np.ndarray) -> Optional[Box]:
    """Bounding box of the most saturated / most deviant coloured blob."""
    white_rgb, _ = colorimetry.auto_white_reference(image)
    mask = colorimetry.auto_sample_mask(image, white_rgb)
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    x, y = int(xs.min()), int(ys.min())
    return (x, y, int(xs.max() - x + 1), int(ys.max() - y + 1))


def auto_paper_bbox(image: np.ndarray) -> Optional[Box]:
    """
    Find a COMPACT patch of clean white paper.

    Deliberately not "the bounding box of the brightest pixels": paper normally
    surrounds the sample, so that box spans the whole frame and ends up
    including the sample itself — which then contaminates the white reference
    it is supposed to provide.

    Instead we slide a window and pick the brightest, most UNIFORM one
    (score = mean - 2*std), which lands on plain paper and avoids both the
    sample and any glare/shadow edge.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = gray.shape
    win = max(12, int(round(config.PAPER_PATCH_FRAC * min(h, w))))
    if win >= min(h, w):
        return (0, 0, w, h)

    ksize = (win, win)
    mean = cv2.boxFilter(gray, -1, ksize, normalize=True,
                         borderType=cv2.BORDER_REFLECT)
    mean_sq = cv2.boxFilter(gray * gray, -1, ksize, normalize=True,
                            borderType=cv2.BORDER_REFLECT)
    std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
    score = mean - 2.0 * std

    # Ignore windows whose centre sits too close to the border to fit fully.
    half = win // 2
    valid = np.full_like(score, -np.inf)
    valid[half:h - half, half:w - half] = score[half:h - half, half:w - half]
    if not np.isfinite(valid).any():
        return (0, 0, w, h)

    cy, cx = np.unravel_index(int(np.argmax(valid)), valid.shape)
    return colorimetry._clamp_roi(int(cx - half), int(cy - half),
                                  win, win, w, h)


def auto_view(image: np.ndarray) -> SceneView:
    return SceneView(sample_bbox=auto_sample_bbox(image),
                     paper_bbox=auto_paper_bbox(image),
                     usable=True, layer_quality="unknown",
                     sample_source="auto", paper_source="auto")


# ---------------------------------------------------------------------------
# Robust JSON handling
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a reply, tolerating ``` fences."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?|```", " ", text)
    # Non-greedy first, then greedy, so we survive trailing prose.
    for pattern in (r"\{.*\}",):
        m = re.search(pattern, cleaned, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def _coerce_box(raw: Any, width: int, height: int) -> Optional[Box]:
    """
    Validate a model-supplied [x, y, w, h].

    Accepts pixels or 0-1 fractions. Rejects anything degenerate, inverted or
    absurdly small — a hallucinated box must fall back, never silently make us
    measure the wrong pixels.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if any(not np.isfinite(v) for v in vals):
        return None

    x, y, w, h = vals
    # Fractional coords (all within 0..1) -> scale to pixels.
    if all(0.0 <= v <= 1.0 for v in vals) and w <= 1.0 and h <= 1.0:
        x, y, w, h = x * width, y * height, w * width, h * height
    if w <= 0 or h <= 0:
        return None

    box = colorimetry._clamp_roi(int(round(x)), int(round(y)),
                                 int(round(w)), int(round(h)), width, height)
    if box is None:
        return None
    if (box[2] * box[3]) < config.SCENE_MIN_BOX_FRAC * width * height:
        return None
    return box


def _parse_view(node: Any, image: np.ndarray) -> SceneView:
    """Build a SceneView from one image's JSON node, falling back per-field."""
    h, w = image.shape[:2]
    view = auto_view(image)          # start from the deterministic result
    if not isinstance(node, dict):
        return view

    sample = _coerce_box(node.get("sample_bbox"), w, h)
    if sample is not None:
        view.sample_bbox, view.sample_source = sample, "gemma"
    paper = _coerce_box(node.get("paper_bbox"), w, h)
    if paper is not None:
        view.paper_bbox, view.paper_source = paper, "gemma"

    usable = node.get("usable")
    if isinstance(usable, bool):
        view.usable = usable
    reason = node.get("reject_reason")
    if isinstance(reason, str):
        view.reject_reason = reason.strip()
    quality = node.get("layer_quality")
    if isinstance(quality, str) and quality.lower() in config.VALID_LAYER_QUALITY:
        view.layer_quality = quality.lower()
    return view


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def understand(before_rgb: np.ndarray, after_rgb: np.ndarray) -> ScenePair:
    """
    Locate the sample and paper regions in both images.

    Always returns a usable ScenePair — Gemma only ever *improves* on the
    deterministic fallback, it can never leave us without regions.
    """
    fallback = ScenePair(before=auto_view(before_rgb),
                         after=auto_view(after_rgb),
                         align_ok=None, source="auto")

    if not gemma.is_enabled():
        fallback.notes.append("Gemma disabled — using auto-detection.")
        return fallback

    bh, bw = before_rgb.shape[:2]
    ah, aw = after_rgb.shape[:2]
    prompt = (f"{config.SCENE_PROMPT}\n\n"
              f"IMAGE 1 (before) is {bw}x{bh} pixels. "
              f"IMAGE 2 (after) is {aw}x{ah} pixels.")

    reply = gemma.call_vision(prompt, [before_rgb, after_rgb],
                              max_tokens=config.SCENE_MAX_TOKENS)
    if not reply:
        fallback.notes.append("Gemma unavailable — using auto-detection.")
        return fallback

    parsed = _extract_json(reply)
    if parsed is None:
        logger.warning("Scene: reply was not JSON; auto-detecting. %.200s", reply)
        fallback.raw_response = reply
        fallback.parse_error = "not_json"
        fallback.notes.append("Gemma reply unparseable — using auto-detection.")
        return fallback

    # Accept {"before":..,"after":..} and tolerate a flat single-image object.
    before_node = parsed.get("before", parsed)
    after_node = parsed.get("after", parsed)

    pair = ScenePair(
        before=_parse_view(before_node, before_rgb),
        after=_parse_view(after_node, after_rgb),
        source="gemma",
        raw_response=reply,
    )

    align = parsed.get("align_ok")
    if not isinstance(align, bool):
        for node in (before_node, after_node):
            if isinstance(node, dict) and isinstance(node.get("align_ok"), bool):
                align = node["align_ok"]
                break
    pair.align_ok = align if isinstance(align, bool) else None

    if pair.before.sample_source == "auto" and pair.after.sample_source == "auto":
        pair.notes.append("Gemma boxes rejected as invalid — auto-detected instead.")
    logger.info("Scene: source=%s align_ok=%s quality=%s/%s "
                "sample_src=%s/%s paper_src=%s/%s",
                pair.source, pair.align_ok,
                pair.before.layer_quality, pair.after.layer_quality,
                pair.before.sample_source, pair.after.sample_source,
                pair.before.paper_source, pair.after.paper_source)
    return pair


def debug_dict(pair: ScenePair) -> dict:
    def view(v: SceneView) -> Dict[str, Any]:
        return {
            "sample_bbox": list(v.sample_bbox) if v.sample_bbox else None,
            "paper_bbox": list(v.paper_bbox) if v.paper_bbox else None,
            "usable": v.usable,
            "reject_reason": v.reject_reason,
            "layer_quality": v.layer_quality,
            "sample_source": v.sample_source,
            "paper_source": v.paper_source,
        }
    return {
        "source": pair.source,
        "align_ok": pair.align_ok,
        "before": view(pair.before),
        "after": view(pair.after),
        "notes": pair.notes,
        "parse_error": pair.parse_error,
        "raw_gemma_json": pair.raw_response,
    }
