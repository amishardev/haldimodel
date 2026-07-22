"""
estimator.py
============
The clean interface between the PRIMARY signal (reaction_strength — the MAGNITUDE
of the curcumin-specific reagent blue-channel reaction) and the reported purity
index. main.py only ever calls estimate_purity(reaction_strength) — swap the
internals here to change how the reaction maps to a score without touching the
rest of the app.

Two modes, chosen automatically:
  * heuristic  : purity = clip( reaction_strength / REACTION_REF * 100, 0, 100 )
                 (no calibration data yet — REACTION_REF is a literature guess)
  * calibrated : purity = slope * reaction_strength + intercept, fitted
                 DIRECTLY on (reaction_strength, known_purity_%) points via
                 numpy.polyfit degree 1. Purity is fitted straight — there is
                 no curcumin-%-by-mass indirection to get wrong.

BULLETPROOFING (this module never returns a non-finite or silently-zeroed
value — see FIX 1):
  * every result passes through an isfinite check before being returned;
  * a calibration fit with slope <= 0 is REJECTED and we fall back to the
    heuristic, because a non-positive slope would make purity DECREASE as
    reaction_strength increases — violating the one invariant this whole app
    depends on (see check_monotonic below, exercised by app/selftest.py).

# ---------------------------------------------------------------------------
# TODO(model): This function is the SINGLE place to plug in a trained model.
# When a labelled dataset exists, drop a fitted scikit-learn regressor here,
# e.g.:
#
#     import joblib
#     _MODEL = joblib.load("curcumin_model.pkl")   # trained offline
#
#     def estimate_purity(reaction_strength: float) -> PurityEstimate:
#         raw_ratio = float(_MODEL.predict([[reaction_strength]])[0])
#         return _finish(raw_ratio, "model")
#
# Nothing else in the app needs to change — the endpoint, dye flag, frontend
# and disclaimers all stay the same.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Optional

from . import calibration, config

logger = logging.getLogger("haldi.estimator")


@dataclass
class PurityEstimate:
    purity_index: float                          # clamped 0-100, for the gauge
    band: str
    mode: str                                     # "heuristic" | "calibrated" | "model"
    calibration_points_used: int = 0              # 0 in heuristic mode
    raw_ratio: float = 0.0                        # UNCAPPED value, for debugging
    over_range: bool = False                      # raw_ratio > 100 => not linear
    display_value: str = ""                       # what the UI should print


def band_for_score(score: float) -> str:
    """Map a 0-100 score to a CONFIG band label."""
    for upper, label in config.BANDS:
        if score < upper:
            return label
    return config.BANDS[-1][1]


def _clip_purity(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def _safe_ratio(raw_ratio: float, context: str) -> float:
    """NEVER let a non-finite ratio escape this module. Log loudly if it tries."""
    if not math.isfinite(raw_ratio):
        logger.error("Non-finite raw_ratio (%r) in %s -> clamping to 0.0. "
                    "This should be unreachable; investigate upstream.",
                    raw_ratio, context)
        return 0.0
    return float(raw_ratio)


def _finish(raw_ratio: float, mode: str,
            calibration_points_used: int = 0) -> PurityEstimate:
    """
    Build the estimate from an UNCAPPED ratio.

    An over-range reading is reported as such rather than being silently
    flattened to "100%": pinning at 100 is exactly how a too-thick sample used
    to masquerade as a perfect one.
    """
    raw_ratio = _safe_ratio(raw_ratio, mode)
    purity = _clip_purity(raw_ratio)
    over = raw_ratio > 100.0
    display = config.OVER_RANGE_LABEL if over else f"{purity:.0f}%"
    return PurityEstimate(purity, band_for_score(purity), mode,
                          calibration_points_used, round(raw_ratio, 2),
                          over, display)


def check_monotonic(reaction_strengths: Iterable[float]) -> bool:
    """
    True iff estimate_purity() is non-decreasing over the given (sorted)
    sequence of reaction_strength values. Used by app/selftest.py; also acts
    as living documentation of the one invariant this whole app depends on.
    """
    values = [estimate_purity(rs).purity_index for rs in reaction_strengths]
    for a, b in zip(values, values[1:]):
        if b < a - 1e-9:
            logger.error("MONOTONICITY VIOLATION: purity went %.3f -> %.3f "
                        "for increasing reaction_strength.", a, b)
            return False
    return True


def estimate_purity(reaction_strength: float) -> PurityEstimate:
    """Map curcumin reaction strength -> purity index (0-100) + band + mode."""
    if not math.isfinite(reaction_strength):
        logger.error("estimate_purity received non-finite reaction_strength "
                    "(%r) -> treating as 0.0.", reaction_strength)
        reaction_strength = 0.0

    fitted = calibration.fit()
    if fitted is not None:
        slope, intercept, n_points = fitted
        # A non-positive slope would make purity FALL as the reaction gets
        # STRONGER — the opposite of what this instrument measures. Never
        # trust a fit like that; fall back to the heuristic instead.
        if slope <= 0:
            logger.error("Calibration fit has non-positive slope (%.4f) — "
                        "would violate monotonicity. Falling back to the "
                        "REACTION_REF heuristic instead.", slope)
        else:
            raw_ratio = slope * reaction_strength + intercept
            return _finish(raw_ratio, "calibrated", n_points)

    # Heuristic fallback: scale against REACTION_REF (a pure/premium sample).
    # REACTION_REF is a literature-anchored constant, NOT measured.
    if config.REACTION_REF <= 0:
        logger.error("REACTION_REF is non-positive (%r) — returning 0.",
                    config.REACTION_REF)
        return _finish(0.0, "heuristic")
    raw_ratio = reaction_strength / config.REACTION_REF * 100.0
    return _finish(raw_ratio, "heuristic")
