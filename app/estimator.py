"""
estimator.py
============
The clean interface between the PRIMARY signal (reaction_strength — the MAGNITUDE
of the curcumin-specific NAVDHI REAGENT3 blue-channel reaction) and the reported purity
index. main.py only ever calls estimate_purity(reaction_strength) — swap the
internals here to change how the reaction maps to a score without touching the
rest of the app.

Two modes, chosen automatically:
  * heuristic  : purity = clip( reaction_strength / REACTION_REF * 100, 0, 100 )
                                                                        (no data)
  * calibrated : curcumin_% = slope*reaction_strength + intercept (fitted from
                 >=2 points), then scaled onto 0-100 with PREMIUM_CURCUMIN_PCT
                 as full-scale.

# ---------------------------------------------------------------------------
# TODO(model): This function is the SINGLE place to plug in a trained model.
# When a labelled dataset exists, drop a fitted scikit-learn regressor here,
# e.g.:
#
#     import joblib
#     _MODEL = joblib.load("curcumin_model.pkl")   # trained offline
#
#     def estimate_purity(reaction_strength: float) -> PurityEstimate:
#         pct = float(_MODEL.predict([[reaction_strength]])[0])  # or more features
#         purity = _pct_to_purity(pct)
#         return PurityEstimate(purity, band_for_score(purity), "model", pct)
#
# Nothing else in the app needs to change — the endpoint, dye flag, frontend
# and disclaimers all stay the same.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import calibration, config


@dataclass
class PurityEstimate:
    purity_index: float
    band: str
    mode: str                                    # "heuristic" | "calibrated" | "model"
    estimated_curcumin_percent: Optional[float]  # populated when calibrated/model


def band_for_score(score: float) -> str:
    """Map a 0-100 score to a CONFIG band label."""
    for upper, label in config.BANDS:
        if score < upper:
            return label
    return config.BANDS[-1][1]


def _clip_purity(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def _pct_to_purity(pct: float) -> float:
    """Scale a curcumin % onto 0-100 using PREMIUM_CURCUMIN_PCT as full-scale."""
    return _clip_purity(pct / config.PREMIUM_CURCUMIN_PCT * 100.0)


def estimate_purity(reaction_strength: float) -> PurityEstimate:
    """Map curcumin reaction strength -> purity index (0-100) + band + mode."""
    fitted = calibration.fit()
    if fitted is not None:
        slope, intercept = fitted
        pct = slope * reaction_strength + intercept
        purity = _pct_to_purity(pct)
        return PurityEstimate(purity, band_for_score(purity),
                              "calibrated", round(float(pct), 3))

    # Heuristic fallback: scale against REACTION_REF (a pure/premium sample).
    # REACTION_REF is a literature-anchored constant, NOT measured.
    if config.REACTION_REF <= 0:
        purity = 0.0
    else:
        purity = _clip_purity(reaction_strength / config.REACTION_REF * 100.0)
    return PurityEstimate(purity, band_for_score(purity), "heuristic", None)
