"""
colorimetry.py
==============
Deterministic colour-science pipeline (NO machine learning). Turns two images
(BEFORE and AFTER adding reagent) into a set of SIGNED, CONTINUOUS signals plus
full debug numbers.

Pipeline per image:
  1. Sample ROI  -> mean R,G,B of the liquid.
  2. White ROI   -> mean R,G,B of a white reference (auto = brightest ~2%).
  3. Normalise   -> norm_ch = sample_ch / white_ch, clipped to (0.001, 1.0).
  4. Absorbance  -> A_ch = -log10(norm_ch).

Across the two images:
  * before_yellow  = (normR + normG)/2 - normB of the BEFORE sample ROI.
                     PRIMARY quantity — tracks curcumin, signed & continuous.
  * reaction_delta = A_blue(after) - A_blue(before).
                     Curcumin-specificity CROSS-CHECK only (reaction saturates).
  * white_diff     = brightness-invariant chromaticity distance between the two
                     white points. If too large the pair is NOT comparable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

from . import config

logger = logging.getLogger("haldi.colorimetry")

RoiTuple = Tuple[int, int, int, int]  # x, y, w, h in pixels


# ---------------------------------------------------------------------------
# Image decoding
# ---------------------------------------------------------------------------
def decode_image(raw: bytes) -> np.ndarray:
    """Decode raw image bytes to an RGB uint8 array. Raises ValueError if bad."""
    if not raw:
        raise ValueError("Empty image upload.")
    buf = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Could not decode image (unsupported or corrupt file).")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# ROI helpers
# ---------------------------------------------------------------------------
def default_sample_roi(width: int, height: int) -> RoiTuple:
    """Centred square whose area ~= DEFAULT_SAMPLE_ROI_AREA_FRAC of the image."""
    area = config.DEFAULT_SAMPLE_ROI_AREA_FRAC * width * height
    side = int(round(area ** 0.5))
    side = max(1, min(side, width, height))
    x = (width - side) // 2
    y = (height - side) // 2
    return x, y, side, side


def parse_roi(spec: Optional[str], width: int, height: int) -> Optional[RoiTuple]:
    """
    Parse an optional ROI string "a,b,c,d".
      * If every value is in [0, 1] -> treated as FRACTIONS of (W, H).
      * Otherwise -> treated as absolute PIXEL coords.
    Returns a clamped (x, y, w, h) pixel tuple, or None if empty/invalid.
    """
    if not spec:
        return None
    try:
        parts = [float(p) for p in str(spec).replace(" ", "").split(",")]
    except ValueError:
        logger.warning("Ignoring unparseable ROI spec: %r", spec)
        return None
    if len(parts) != 4:
        logger.warning("Ignoring ROI spec with != 4 values: %r", spec)
        return None

    a, b, c, d = parts
    if all(0.0 <= v <= 1.0 for v in parts):
        x, y, w, h = a * width, b * height, c * width, d * height
    else:
        x, y, w, h = parts
    return _clamp_roi(int(round(x)), int(round(y)),
                      int(round(w)), int(round(h)), width, height)


def _clamp_roi(x, y, w, h, width, height) -> Optional[RoiTuple]:
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    if w < 1 or h < 1:
        return None
    return x, y, w, h


def crop(image: np.ndarray, roi: RoiTuple) -> np.ndarray:
    x, y, w, h = roi
    return image[y:y + h, x:x + w]


# ---------------------------------------------------------------------------
# White reference
# ---------------------------------------------------------------------------
def auto_white_reference(image: np.ndarray) -> Tuple[np.ndarray, int]:
    """
    Fallback white reference: mean RGB of the brightest ~top-% of pixels.
    Returns (mean_rgb float array shape (3,), n_pixels_used).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    pct = 100.0 - config.WHITE_REFERENCE_TOP_PERCENT
    thresh = np.percentile(gray, pct)
    mask = gray >= thresh
    n = int(mask.sum())
    if n < config.WHITE_REFERENCE_MIN_PIXELS:
        # Too few pixels above the percentile -> take the brightest N outright.
        flat = gray.reshape(-1)
        k = int(min(config.WHITE_REFERENCE_MIN_PIXELS, flat.size))
        idx = np.argpartition(flat, -k)[-k:]
        pixels = image.reshape(-1, 3)[idx]
        return pixels.astype(np.float64).mean(axis=0), k
    pixels = image[mask]
    return pixels.astype(np.float64).mean(axis=0), n


# ---------------------------------------------------------------------------
# Per-image measurement
# ---------------------------------------------------------------------------
@dataclass
class ChannelMeasurement:
    sample_rgb: np.ndarray          # mean R,G,B of sample ROI (0-255)
    white_rgb: np.ndarray           # mean R,G,B of white ref  (0-255)
    norm_rgb: np.ndarray            # white-normalised, clipped to CONFIG range
    absorbance_rgb: np.ndarray      # -log10(norm)
    sample_roi: RoiTuple
    white_source: str               # "user" or "auto"
    white_pixels: int

    @property
    def norm_blue(self) -> float:
        return float(self.norm_rgb[2])

    @property
    def a_blue(self) -> float:
        return float(self.absorbance_rgb[2])


def measure_image(image: np.ndarray,
                  sample_roi: Optional[RoiTuple] = None,
                  white_roi: Optional[RoiTuple] = None) -> ChannelMeasurement:
    h, w = image.shape[:2]
    if sample_roi is None:
        sample_roi = default_sample_roi(w, h)

    sample_rgb = (crop(image, sample_roi)
                  .reshape(-1, 3).astype(np.float64).mean(axis=0))

    if white_roi is not None:
        white_rgb = (crop(image, white_roi)
                     .reshape(-1, 3).astype(np.float64).mean(axis=0))
        white_source = "user"
        white_pixels = int(white_roi[2] * white_roi[3])
    else:
        white_rgb, white_pixels = auto_white_reference(image)
        white_source = "auto"

    # Guard against a zero / near-zero white reference before dividing.
    safe_white = np.where(white_rgb < 1.0, 1.0, white_rgb)
    norm = np.clip(sample_rgb / safe_white,
                   config.NORM_CLIP_MIN, config.NORM_CLIP_MAX)
    absorbance = -np.log10(norm)

    m = ChannelMeasurement(sample_rgb, white_rgb, norm, absorbance,
                           sample_roi, white_source, white_pixels)
    logger.debug("measure: sample=%s white=%s (%s, %dpx) norm=%s A=%s",
                 np.round(sample_rgb, 2), np.round(white_rgb, 2),
                 white_source, white_pixels, np.round(norm, 4),
                 np.round(absorbance, 4))
    return m


# ---------------------------------------------------------------------------
# Yellowness (PRIMARY signal) — from the white-normalised sample ROI
# ---------------------------------------------------------------------------
def normalized_yellowness(norm_rgb: np.ndarray) -> float:
    """
    Signed, continuous yellowness of a white-normalised sample:
        (normR + normG) / 2 - normB
    ~0 for a neutral/pale extract, large & positive for a vivid yellow one.
    Because it is computed on white-NORMALISED channels it is robust to
    lighting/white-balance, and it never collapses distinct samples to 0.
    """
    r, g, b = float(norm_rgb[0]), float(norm_rgb[1]), float(norm_rgb[2])
    return (r + g) / 2.0 - b


def white_difference(before_white: np.ndarray, after_white: np.ndarray) -> float:
    """
    Brightness-invariant chromaticity distance between two white points: L1
    distance between their normalised (sum-to-1) RGBs. ~0 => same white balance
    / background; large => different backgrounds or lighting (not comparable).
    """
    bw = np.asarray(before_white, dtype=np.float64)
    aw = np.asarray(after_white, dtype=np.float64)
    bs, as_ = float(bw.sum()), float(aw.sum())
    if bs < 1.0 or as_ < 1.0:
        return 1.0
    return float(np.abs(bw / bs - aw / as_).sum())


# ---------------------------------------------------------------------------
# Top-level: compute all signals from the two images
# ---------------------------------------------------------------------------
@dataclass
class SignalResult:
    before: ChannelMeasurement
    after: ChannelMeasurement
    before_yellow: float            # PRIMARY: yellowness of BEFORE sample ROI
    reaction_delta: float           # CROSS-CHECK: A_blue(after) - A_blue(before)
    white_diff: float               # chromaticity distance between white points
    comparable: bool                # white_diff <= tolerance
    after_darkness: float           # mean of AFTER sample ROI channels (0-255)
    debug: dict = field(default_factory=dict)


def _measurement_debug(m: ChannelMeasurement) -> dict:
    return {
        "sample_rgb": [round(float(v), 2) for v in m.sample_rgb],
        "white_rgb": [round(float(v), 2) for v in m.white_rgb],
        "norm_rgb": [round(float(v), 4) for v in m.norm_rgb],
        "absorbance_rgb": [round(float(v), 4) for v in m.absorbance_rgb],
        "white_source": m.white_source,
        "white_pixels": m.white_pixels,
        "sample_roi": list(m.sample_roi),
    }


def compute_signal(before_img: np.ndarray, after_img: np.ndarray,
                   before_sample_roi: Optional[RoiTuple] = None,
                   after_sample_roi: Optional[RoiTuple] = None,
                   before_white_roi: Optional[RoiTuple] = None,
                   after_white_roi: Optional[RoiTuple] = None) -> SignalResult:
    before = measure_image(before_img, before_sample_roi, before_white_roi)
    after = measure_image(after_img, after_sample_roi, after_white_roi)

    before_yellow = normalized_yellowness(before.norm_rgb)
    reaction_delta = after.a_blue - before.a_blue
    white_diff = white_difference(before.white_rgb, after.white_rgb)
    comparable = white_diff <= config.WHITE_COMPARABILITY_TOLERANCE
    after_darkness = float(np.mean(after.sample_rgb))

    debug = {
        "before": _measurement_debug(before),
        "after": _measurement_debug(after),
        "white_before": [round(float(v), 2) for v in before.white_rgb],
        "white_after": [round(float(v), 2) for v in after.white_rgb],
        "white_diff": round(white_diff, 4),
        "white_tolerance": config.WHITE_COMPARABILITY_TOLERANCE,
        "before_yellow": round(before_yellow, 4),
        "reaction_delta": round(reaction_delta, 4),
        "after_darkness": round(after_darkness, 2),
        "comparable": comparable,
    }

    # Log all raw signals + per-channel absorbances so results are debuggable.
    logger.info("SIGNAL before_yellow=%.5f | reaction_delta=%.5f | "
                "white_diff=%.4f (tol=%.4f) comparable=%s | "
                "A_blue before=%.4f after=%.4f | after_darkness=%.1f",
                before_yellow, reaction_delta, white_diff,
                config.WHITE_COMPARABILITY_TOLERANCE, comparable,
                before.a_blue, after.a_blue, after_darkness)

    return SignalResult(before=before, after=after,
                        before_yellow=before_yellow,
                        reaction_delta=reaction_delta,
                        white_diff=white_diff, comparable=comparable,
                        after_darkness=after_darkness, debug=debug)
