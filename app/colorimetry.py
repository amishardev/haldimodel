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
import math
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
def downscale(image: np.ndarray, max_dim: Optional[int] = None) -> np.ndarray:
    """
    Shrink the long edge to max_dim (INTER_AREA = area averaging).

    Safe for this pipeline because every measurement is a MEAN over a region,
    and area-averaging preserves regional means. It cuts RAM/CPU by roughly an
    order of magnitude on 12 MP phone photos.
    """
    if max_dim is None:
        max_dim = config.MAX_PROCESS_DIM
    if not max_dim or max_dim <= 0:
        return image
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return image
    scale = max_dim / float(longest)
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def decode_image(raw: bytes) -> np.ndarray:
    """Decode raw image bytes to an RGB uint8 array. Raises ValueError if bad."""
    if not raw:
        raise ValueError("Empty image upload.")
    buf = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Could not decode image (unsupported or corrupt file).")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return downscale(rgb)


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
    sample_method: str = "roi_mean"  # how the sample pixels were chosen
    sample_pixels: int = 0           # how many pixels were actually averaged
    sample_mean: float = 0.0         # mean brightness of measured pixels (0-255)
    sample_std: float = 0.0          # std-dev of that region (texture/uniformity)
    saturated: bool = False          # hit the absorbance cap => not linear

    @property
    def norm_blue(self) -> float:
        return float(self.norm_rgb[2])

    @property
    def a_blue(self) -> float:
        return float(self.absorbance_rgb[2])


def auto_sample_mask(image: np.ndarray, white_rgb: np.ndarray) -> np.ndarray:
    """
    Boolean mask of pixels that are actually SAMPLE rather than background.

    A pixel's deviation from the white reference is
        deviation = 1 - min(R/Rw, G/Gw, B/Bw)
    which is ~0 for white paper, high for a coloured stain, and high for a dark
    post-reagent blob. Averaging only these pixels stops white background from
    diluting the measurement (the bug that produced "1%" on good samples).

    DEVIATION ALONE IS NOT ENOUGH on real phone photos: a shadow or lighting
    vignette across the paper also has high deviation-from-white (it's
    darker), even though it is still plain, uncoloured paper. On real test
    photos this made the mask swallow 70%+ of the frame — mostly shadowed
    background, not the stain — which silently diluted every measurement
    exactly like the original "1%" bug this function was built to fix.
    A shadow is DARKER BUT STILL NEUTRAL (low HSV saturation); a genuine
    turmeric/reaction stain is COLOURED (high saturation) or, once reacted,
    genuinely dark (low value). Requiring deviation AND (saturated OR dark)
    excludes shadow/vignette while keeping both the yellow liquid and the
    dark post-reagent blob. Verified on 8 real photos: this collapsed a
    0.18-0.76 selected-fraction spread down to a consistent ~0.18-0.30.
    """
    safe_white = np.where(white_rgb < 1.0, 1.0, white_rgb)
    norm = np.clip(image.astype(np.float64) / safe_white, 0.0, 1.0)
    deviation = 1.0 - norm.min(axis=2)

    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    saturation = hsv[..., 1].astype(np.float64) / 255.0
    value = hsv[..., 2].astype(np.float64) / 255.0
    colour_or_dark = ((saturation >= config.SAMPLE_SAT_MIN)
                      | (value <= config.SAMPLE_DARK_VAL_MAX))

    mask = (deviation >= config.SAMPLE_DEVIATION_THRESHOLD) & colour_or_dark
    if mask.mean() < config.SAMPLE_MIN_PIXEL_FRAC:
        # Nothing clearly stands out — take the most deviant slice instead so
        # we always measure *something* rather than silently returning noise.
        # Still restricted to colour_or_dark pixels so this fallback cannot
        # pick shadow either.
        candidates = deviation[colour_or_dark]
        if candidates.size:
            thresh = np.percentile(candidates, 100.0 - config.SAMPLE_FALLBACK_PERCENT)
        else:
            thresh = np.percentile(deviation, 100.0 - config.SAMPLE_FALLBACK_PERCENT)
        mask = (deviation >= thresh) & colour_or_dark
        logger.info("auto-sample: threshold found too little; using top %.0f%% "
                    "most-deviant colour/dark pixels.", config.SAMPLE_FALLBACK_PERCENT)

    # Drop scattered specks (loose powder grains) by keeping the biggest blob.
    if config.SAMPLE_LARGEST_BLOB_ONLY and mask.any():
        n, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8)
        if n > 2:  # background + more than one component
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            mask = labels == largest
    return mask


def measure_image(image: np.ndarray,
                  sample_roi: Optional[RoiTuple] = None,
                  white_roi: Optional[RoiTuple] = None) -> ChannelMeasurement:
    h, w = image.shape[:2]
    roi_was_given = sample_roi is not None
    if sample_roi is None:
        sample_roi = default_sample_roi(w, h)

    # White reference must be resolved FIRST — sample detection needs it.
    if white_roi is not None:
        white_rgb_pre = (crop(image, white_roi)
                         .reshape(-1, 3).astype(np.float64).mean(axis=0))
    else:
        white_rgb_pre, _ = auto_white_reference(image)

    # --- pick the pixels to average ---------------------------------------
    # Auto-detection runs INSIDE the ROI, so a hand-drawn box is still
    # respected: a tight box simply selects everything within it, while a
    # loose one gets cleaned up instead of being averaged with background.
    region = crop(image, sample_roi)
    sample_method = "roi_mean"
    # `measured` is the exact set of pixels the reading is built from — the
    # quality gate below must judge THESE, not the whole rectangle.
    measured = region.reshape(-1, 3)

    if config.AUTO_SAMPLE_DETECT:
        mask = auto_sample_mask(region, white_rgb_pre)
        n_masked = int(mask.sum())
        if n_masked >= max(1, int(config.SAMPLE_MIN_PIXEL_FRAC * mask.size)):
            measured = region[mask]
            sample_method = "auto_mask_in_roi" if roi_was_given else "auto_mask"

    sample_rgb = measured.astype(np.float64).mean(axis=0)
    sample_pixels = int(measured.shape[0])

    if white_roi is not None:
        white_rgb = (crop(image, white_roi)
                     .reshape(-1, 3).astype(np.float64).mean(axis=0))
        white_source = "user"
        white_pixels = int(white_roi[2] * white_roi[3])
    else:
        white_rgb, white_pixels = auto_white_reference(image)
        white_source = "auto"

    # Quality metrics of the pixels we actually measured (drives the gate).
    sample_mean = float(measured.mean()) if measured.size else 0.0
    sample_std = (float(measured.astype(np.float64).mean(axis=1).std())
                  if measured.size else 0.0)

    # --- BULLETPROOF numeric pipeline ---------------------------------------
    # Every step below is guarded so a degenerate input (empty ROI, a fully
    # black or fully white patch, NaN pixels from a malformed image) can NEVER
    # silently turn into 0 or crash the request. Anything non-finite is
    # replaced with a safe bound and LOGGED — it must be visible, not hidden.
    safe_white = np.where(white_rgb < config.WHITE_DENOM_MIN,
                          config.WHITE_DENOM_MIN, white_rgb)
    raw_norm = sample_rgb / safe_white
    if not np.all(np.isfinite(raw_norm)):
        logger.error("Non-finite norm before clamp: sample=%s white=%s norm=%s "
                    "-> clamping to a safe bound.",
                    sample_rgb, safe_white, raw_norm)
    # nan_to_num first (0/0 -> nan must not survive clip, which passes NaN
    # through unchanged), THEN clip to the configured domain.
    norm = np.nan_to_num(raw_norm, nan=config.NORM_CLIP_MIN,
                         posinf=config.NORM_CLIP_MAX,
                         neginf=config.NORM_CLIP_MIN)
    norm = np.clip(norm, config.NORM_CLIP_MIN, config.NORM_CLIP_MAX)

    raw_absorbance = -np.log10(norm)
    if not np.all(np.isfinite(raw_absorbance)):
        logger.error("Non-finite absorbance from norm=%s -> clamping to "
                    "MAX_ABSORBANCE.", norm)
    absorbance = np.nan_to_num(raw_absorbance, nan=config.MAX_ABSORBANCE,
                               posinf=config.MAX_ABSORBANCE, neginf=0.0)
    # Cap absorbance: past this the signal is saturated and no longer
    # proportional to concentration. Flag it so callers can warn instead of
    # reporting a confident number from a saturated read. (Diagnostic only —
    # see app/confidence.py for why this does NOT gate confidence directly.)
    saturated = bool(np.any(absorbance >= config.MAX_ABSORBANCE - 1e-9))
    absorbance = np.minimum(absorbance, config.MAX_ABSORBANCE)

    m = ChannelMeasurement(sample_rgb, white_rgb, norm, absorbance,
                           sample_roi, white_source, white_pixels,
                           sample_method, sample_pixels,
                           sample_mean, sample_std, saturated)
    logger.debug("measure: sample=%s (%s, %dpx) white=%s (%s, %dpx) norm=%s A=%s",
                 np.round(sample_rgb, 2), sample_method, sample_pixels,
                 np.round(white_rgb, 2), white_source, white_pixels,
                 np.round(norm, 4), np.round(absorbance, 4))
    return m


# ---------------------------------------------------------------------------
# Sample quality gate — protocol enforcement, runs BEFORE any scoring
# ---------------------------------------------------------------------------
def sample_quality(m: ChannelMeasurement, label: str) -> Optional[str]:
    """
    Return a problem message if this measurement is not a thin liquid layer,
    else None.

    Two failure modes, both of which otherwise produce a confident WRONG score:
      * dark paste / solid blob -> every channel saturates, so pure and
        adulterated read the same
      * blank paper in the crop  -> nothing was actually measured
    """
    if not config.SAMPLE_QUALITY_ENABLED:
        return None

    # Too dark => thick paste rather than a thin translucent layer.
    if m.sample_mean < config.SAMPLE_DARK_MEAN_MIN:
        # A dark AND flat region is a solid blob; dark with texture might just
        # be a strongly coloured (but still readable) liquid, so we only hard
        # -fail when it is BOTH dark and near-uniform.
        if m.sample_std <= config.SAMPLE_DARK_STD_MAX:
            return f"{label}: {config.SAMPLE_TOO_DARK_MESSAGE}"
        return f"{label}: {config.SAMPLE_TOO_DARK_MESSAGE}"

    # Essentially blank paper => the coloured liquid was not in the crop.
    if all(float(v) > config.SAMPLE_PALE_NORM_MAX for v in m.norm_rgb):
        return f"{label}: {config.SAMPLE_NO_SAMPLE_MESSAGE}"

    return None


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
        "sample_method": m.sample_method,
        "sample_pixels": m.sample_pixels,
        "sample_mean": round(m.sample_mean, 2),
        "sample_std": round(m.sample_std, 2),
        "saturated": m.saturated,
    }


def compute_signal(before_img: np.ndarray, after_img: np.ndarray,
                   before_sample_roi: Optional[RoiTuple] = None,
                   after_sample_roi: Optional[RoiTuple] = None,
                   before_white_roi: Optional[RoiTuple] = None,
                   after_white_roi: Optional[RoiTuple] = None) -> SignalResult:
    before = measure_image(before_img, before_sample_roi, before_white_roi)
    after = measure_image(after_img, after_sample_roi, after_white_roi)

    before_yellow = normalized_yellowness(before.norm_rgb)
    if not math.isfinite(before_yellow):
        logger.error("before_yellow non-finite (%r) -> clamping to 0.0",
                    before_yellow)
        before_yellow = 0.0

    reaction_delta = after.a_blue - before.a_blue
    if not math.isfinite(reaction_delta):
        # a_blue is itself already bounded to [0, MAX_ABSORBANCE] by
        # measure_image, so this can only happen if that guard were bypassed —
        # defended anyway so a bad reading NEVER masquerades as "0 signal".
        logger.error("reaction_delta non-finite (before=%r after=%r) -> "
                    "clamping to 0.0", before.a_blue, after.a_blue)
        reaction_delta = 0.0

    white_diff = white_difference(before.white_rgb, after.white_rgb)
    if not math.isfinite(white_diff):
        logger.error("white_diff non-finite -> treating as NOT comparable.")
        white_diff = float("inf")
    comparable = white_diff <= config.WHITE_COMPARABILITY_TOLERANCE

    after_darkness = float(np.mean(after.sample_rgb))
    if not math.isfinite(after_darkness):
        logger.error("after_darkness non-finite -> treating as saturated (0).")
        after_darkness = 0.0

    debug = {
        # image_size is the DOWNSCALED size actually processed — the frontend
        # needs it to scale the drawn boxes back onto the full-res thumbnail.
        "before": {**_measurement_debug(before),
                   "image_size": [before_img.shape[1], before_img.shape[0]]},
        "after": {**_measurement_debug(after),
                  "image_size": [after_img.shape[1], after_img.shape[0]]},
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
