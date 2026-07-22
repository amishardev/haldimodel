"""
qc.py
=====
Image quality control — rejects photos that are genuinely unmeasurable, and
NOTHING else.

DESIGN RULE #1: FAIL OPEN.
Every uncertain path resolves to "usable". A good photo wrongly rejected is a
worse bug than a slightly noisy score. Concretely, we ANALYSE anyway when:
  * Gemma is disabled, unreachable, slow, or errors out
  * Gemma returns non-JSON, or JSON we can't parse
  * Gemma refuses / talks about safety, policy or moderation
  * Gemma gives a rejection reason that isn't in the allowlist
  * anything at all raises an exception

DESIGN RULE #2: THE MODEL IS NOT A MODERATOR.
Gemma may only reject for the fixed technical reason codes in
config.QC_ALLOWED_REJECT_REASONS (blurry / too_dark / too_bright /
liquid_not_visible / no_white_reference). Any content-, safety- or
appropriateness-based objection is discarded outright — a photo of turmeric is
never "inappropriate", and this tool must never refuse to look at a spice.

Two layers:
  Layer 1  deterministic local checks (always on, CPU, cannot moralise)
  Layer 2  optional Gemma vision QC (only when GEMMA_ENDPOINT is set)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import numpy as np

from . import config, gemma

logger = logging.getLogger("haldi.qc")


@dataclass
class QCResult:
    passed: bool                               # False => refuse to score
    blocking: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    gemma_note: Optional[str] = None
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer 1 — deterministic local checks (no AI, cannot over-reject on "content")
# ---------------------------------------------------------------------------
def blur_score(image_rgb: np.ndarray) -> float:
    """Variance of the Laplacian. Higher = sharper."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def clipped_fraction(roi_rgb: np.ndarray) -> float:
    """Fraction of pixels that are blown out (255) in any channel."""
    if roi_rgb.size == 0:
        return 0.0
    return float((roi_rgb >= 255).any(axis=-1).mean())


def local_checks(before_rgb: np.ndarray, after_rgb: np.ndarray,
                 before_roi_rgb: np.ndarray,
                 after_roi_rgb: np.ndarray) -> QCResult:
    """Objective, deterministic quality checks. Lenient by design."""
    blocking: List[str] = []
    warnings: List[str] = []

    # Sharpness is judged on the BEFORE photo ONLY, and it gates the pair:
    # both photos come from the same camera and setup seconds apart.
    # The AFTER photo deliberately goes near-black as the reaction develops,
    # and a dark, low-contrast image ALWAYS has a low Laplacian variance — so
    # blur-checking it would reject exactly the strong reactions we want most.
    b_blur = blur_score(before_rgb)
    a_blur = blur_score(after_rgb)
    if b_blur < config.QC_BLUR_MIN:
        blocking.append("blurry (before photo out of focus)")

    a_mean_all = float(after_rgb.mean())
    if a_blur < config.QC_BLUR_MIN and a_mean_all >= config.QC_DARK_MEAN_MIN:
        # Only advisory, and only when the after photo is NOT dark (i.e. the
        # low variance really is focus, not low contrast).
        warnings.append("After photo looks soft; keep the camera steady.")

    # Darkness is checked on the BEFORE photo ONLY, for the same reason.
    b_mean = float(before_roi_rgb.mean()) if before_roi_rgb.size else 0.0
    if b_mean < config.QC_DARK_MEAN_MIN:
        blocking.append("too_dark (before photo is essentially black)")

    b_clip = clipped_fraction(before_roi_rgb)
    a_clip = clipped_fraction(after_roi_rgb)
    if b_clip > config.QC_CLIP_MAX_FRAC:
        blocking.append("too_bright (glare / blown out on before photo)")
    if a_clip > config.QC_CLIP_MAX_FRAC:
        warnings.append("Glare on the after photo may reduce accuracy.")

    details = {
        "blur_before": round(b_blur, 1),
        "blur_after": round(a_blur, 1),
        "blur_min": config.QC_BLUR_MIN,
        "before_roi_mean": round(b_mean, 1),
        "after_mean": round(a_mean_all, 1),
        "clipped_before": round(b_clip, 4),
        "clipped_after": round(a_clip, 4),
    }
    return QCResult(passed=not blocking, blocking=blocking,
                    warnings=warnings, details=details)


# ---------------------------------------------------------------------------
# Layer 2 — optional Gemma vision QC (fail-open, allowlist-only)
# ---------------------------------------------------------------------------
def _looks_like_refusal(text: str) -> bool:
    """True if the reply reads as a moderation/safety refusal rather than QC."""
    low = text.lower()
    return any(marker in low for marker in config.QC_REFUSAL_MARKERS)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a reply (tolerates ```json fences)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?|```", " ", text)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def gemma_checks(before_rgb: np.ndarray, after_rgb: np.ndarray) -> QCResult:
    """
    Ask Gemma ONLY about photo quality. Returns a passing QCResult on every
    uncertain path — see the FAIL OPEN rule at the top of this module.
    """
    reply = gemma.call_vision(config.QC_PROMPT, [before_rgb, after_rgb],
                              max_tokens=config.QC_MAX_TOKENS)
    if not reply:
        # Disabled, unreachable, timed out, or empty -> analyse anyway.
        return QCResult(passed=True, details={"gemma_qc": "unavailable"})

    # Parse JSON FIRST. If the model returned a valid verdict, the allowlist
    # below already strips any moderation-flavoured reason while KEEPING a
    # legitimate technical one — so we must not discard the whole reply just
    # because a moderation word appears somewhere inside it.
    parsed = _extract_json(reply)
    if parsed is None:
        # Prose, not a verdict. Only here does refusal language matter, and it
        # is never a reason to reject the user's photo.
        if _looks_like_refusal(reply):
            logger.warning("Gemma QC returned refusal/moderation prose; "
                           "ignoring it and proceeding. Reply: %.160s", reply)
            return QCResult(passed=True,
                            details={"gemma_qc": "refusal_ignored"})
        logger.warning("Gemma QC reply was not JSON; proceeding. %.160s", reply)
        return QCResult(passed=True, details={"gemma_qc": "unparseable"})

    usable = parsed.get("usable")
    note = parsed.get("note") if isinstance(parsed.get("note"), str) else None
    raw_reasons = parsed.get("reasons") or []
    if not isinstance(raw_reasons, list):
        raw_reasons = []
    raw_reasons = [str(r).strip().lower() for r in raw_reasons]

    # Only allowlisted technical reasons can ever block. Everything else the
    # model invented (moderation, "unidentifiable substance", etc.) is dropped.
    allowed = [r for r in raw_reasons
               if r in config.QC_ALLOWED_REJECT_REASONS]
    discarded = [r for r in raw_reasons if r not in allowed]
    if discarded:
        logger.warning("Gemma QC proposed non-allowlisted reasons %s — "
                       "discarded.", discarded)

    details = {
        "gemma_qc": "ok",
        "usable": usable,
        "reasons_raw": raw_reasons,
        "reasons_used": allowed,
        "reasons_discarded": discarded,
    }

    # Block only if: explicitly unusable AND a valid technical reason AND
    # blocking is enabled. Anything else -> pass (optionally with a warning).
    if usable is False and allowed and config.QC_GEMMA_CAN_BLOCK:
        return QCResult(passed=False, blocking=allowed,
                        gemma_note=note, details=details)

    warnings: List[str] = []
    if usable is False and not allowed:
        logger.warning("Gemma QC said unusable with no valid reason; passing.")
        details["gemma_qc"] = "unusable_without_valid_reason_ignored"
    elif allowed:
        warnings.append("Photo quality note: " + ", ".join(allowed))

    return QCResult(passed=True, warnings=warnings, gemma_note=note,
                    details=details)


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------
def run_qc(before_rgb: np.ndarray, after_rgb: np.ndarray,
           before_roi_rgb: np.ndarray, after_roi_rgb: np.ndarray) -> QCResult:
    """Run both QC layers. Never raises — any exception means 'pass'."""
    if not config.QC_ENABLED:
        return QCResult(passed=True, details={"qc": "disabled"})
    try:
        local = local_checks(before_rgb, after_rgb,
                             before_roi_rgb, after_roi_rgb)
        # If the objective checks already reject it, don't spend a GPU call.
        if not local.passed:
            logger.info("QC blocked locally: %s", local.blocking)
            return local

        vision = gemma_checks(before_rgb, after_rgb)
        merged_details = {**local.details, **vision.details}
        if not vision.passed:
            logger.info("QC blocked by Gemma: %s", vision.blocking)
            return QCResult(passed=False, blocking=vision.blocking,
                            warnings=local.warnings + vision.warnings,
                            gemma_note=vision.gemma_note,
                            details=merged_details)
        return QCResult(passed=True,
                        warnings=local.warnings + vision.warnings,
                        gemma_note=vision.gemma_note,
                        details=merged_details)
    except Exception as exc:  # QC must never break analysis
        logger.warning("QC raised (%s); proceeding with analysis.", exc)
        return QCResult(passed=True, details={"qc": f"error_ignored: {exc}"})
