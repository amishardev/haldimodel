"""
confidence.py  —  LAYER 3: FUSION
=================================
Combines Layer 1 (scene understanding) with Layer 2 (the measurement) into a
High / Medium / Low confidence level.

Confidence NEVER changes the number. Layer 2 arithmetic owns the value; this
module only decides how loudly we stand behind it, and — when confidence is
Low — whether we should decline to show a hard number at all.

Components (weights in CONFIG):
  usable          both regions reported measurable
  align_ok        before/after show the same sample and setup
  thin_layer      layer_quality == "thin" for both photos
  not_saturated   after-photo ROI is not near-black (after_darkness >= DARK_THRESHOLD)
  signal_in_range raw ratio sits inside the comfortably linear window

IMPORTANT: not_saturated is judged on after_darkness (overall ROI brightness),
NOT on whether any single channel's absorbance hit MAX_ABSORBANCE. A strong,
valid, pure reaction can legitimately push ONE channel (typically blue) to the
absorbance cap without the read being an artifact — the cap exists to bound the
MATH, not to diagnose sample quality. Gating confidence on that flag instead of
on overall darkness previously and systematically penalised the strongest
genuine signals, including this app's own 100%-purity calibration anchor.

A component we cannot evaluate (Gemma disabled) scores CONFIDENCE_UNKNOWN_CREDIT
rather than 0 — absence of evidence must not look like evidence of a problem.
Without scene understanding the level is capped at Medium, because we have no
way to verify what was actually measured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config

logger = logging.getLogger("haldi.confidence")


@dataclass
class ConfidenceResult:
    score: float                                  # 0..1
    level: str                                    # "High" | "Medium" | "Low"
    reasons: List[str] = field(default_factory=list)   # why it is not higher
    breakdown: Dict[str, object] = field(default_factory=dict)
    capped: bool = False

    @property
    def is_low(self) -> bool:
        return self.level == "Low"


def _level(score: float) -> str:
    if score >= config.CONFIDENCE_HIGH_MIN:
        return "High"
    if score >= config.CONFIDENCE_MEDIUM_MIN:
        return "Medium"
    return "Low"


def assess(scene, before_m, after_m, raw_ratio: float,
           after_darkness: Optional[float] = None) -> ConfidenceResult:
    """
    scene          : scene.ScenePair
    before_m       : colorimetry.ChannelMeasurement (before)
    after_m        : colorimetry.ChannelMeasurement (after)
    raw_ratio      : UNCAPPED purity ratio from Layer 2
    after_darkness : mean brightness (0-255) of the measured AFTER pixels.
                     If omitted, falls back to the per-channel saturation flag
                     (kept only so this function still works when called
                     without the darkness figure — main.py always supplies it).
    """
    w = config.CONFIDENCE_WEIGHTS
    unknown = config.CONFIDENCE_UNKNOWN_CREDIT
    gemma_used = scene.source == "gemma"

    checks: Dict[str, object] = {}
    reasons: List[str] = []
    score = 0.0

    # --- usable -----------------------------------------------------------
    if gemma_used:
        usable = bool(scene.before.usable and scene.after.usable)
        checks["usable"] = usable
        score += w["usable"] * (1.0 if usable else 0.0)
        if not usable:
            # Both photos usually report the same problem — say it once.
            seen = list(dict.fromkeys(
                x.strip() for x in (scene.before.reject_reason,
                                    scene.after.reject_reason) if x.strip()))
            reasons.append(" / ".join(seen) if seen
                           else "a photo was reported unmeasurable")
    else:
        checks["usable"] = "unknown"
        score += w["usable"] * unknown

    # --- alignment --------------------------------------------------------
    if scene.align_ok is None:
        checks["align_ok"] = "unknown"
        score += w["align_ok"] * unknown
    else:
        checks["align_ok"] = scene.align_ok
        score += w["align_ok"] * (1.0 if scene.align_ok else 0.0)
        if not scene.align_ok:
            reasons.append("before/after photos do not show the same setup")

    # --- thin layer -------------------------------------------------------
    qualities = (scene.before.layer_quality, scene.after.layer_quality)
    if "unknown" in qualities:
        checks["thin_layer"] = "unknown"
        score += w["thin_layer"] * unknown
    else:
        thin = all(q == "thin" for q in qualities)
        checks["thin_layer"] = thin
        score += w["thin_layer"] * (1.0 if thin else 0.0)
        if not thin:
            bad = [q for q in qualities if q != "thin"]
            reasons.append(f"sample layer looks {'/'.join(sorted(set(bad)))} "
                           f"— use a thinner liquid layer")

    # --- saturation: genuine near-black read, not merely "hit the cap" ----
    if after_darkness is not None:
        saturated = after_darkness < config.DARK_THRESHOLD
    else:
        saturated = bool(before_m.saturated or after_m.saturated)
    checks["not_saturated"] = not saturated
    score += w["not_saturated"] * (0.0 if saturated else 1.0)
    if saturated:
        reasons.append("reading is saturated (sample too dark/thick)")

    # --- signal in the linear window --------------------------------------
    in_range = (config.CONFIDENCE_SIGNAL_MIN_RATIO <= raw_ratio
                <= config.CONFIDENCE_SIGNAL_MAX_RATIO)
    checks["signal_in_range"] = in_range
    score += w["signal_in_range"] * (1.0 if in_range else 0.0)
    if not in_range:
        if raw_ratio > config.CONFIDENCE_SIGNAL_MAX_RATIO:
            reasons.append("signal is over-range — use a thinner/diluted sample")
        else:
            reasons.append("signal is very weak — check the sample and lighting")

    score = float(max(0.0, min(1.0, score)))
    level = _level(score)

    # Without scene understanding we cannot verify WHAT was measured, so we do
    # not claim High no matter how clean the arithmetic looks.
    capped = False
    if not gemma_used and level == "High":
        level = config.CONFIDENCE_CAP_WITHOUT_GEMMA
        capped = True
        reasons.append("scene understanding unavailable — regions auto-detected")

    result = ConfidenceResult(score=round(score, 3), level=level,
                              reasons=reasons, breakdown=checks, capped=capped)
    logger.info("CONFIDENCE %s (%.2f) breakdown=%s reasons=%s",
                level, score, checks, reasons)
    return result
