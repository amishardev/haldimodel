"""
calibration.py
==============
Persists (reaction_strength, known_curcumin_%) calibration points as a small
JSON file and, once there are >= CALIBRATION_MIN_POINTS, fits a degree-1 map

        curcumin_% = slope * reaction_strength + intercept   (numpy.polyfit deg 1)

Until enough points exist, the estimator falls back to the REACTION_REF
heuristic. This is the "improve later when data exists" hook — no retraining of
the app is required; estimator.estimate_purity() picks up the fit automatically.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import List, Optional, Tuple

import numpy as np

from . import config

logger = logging.getLogger("haldi.calibration")
_lock = threading.Lock()


def _path() -> str:
    """Absolute path to the calibration JSON (project root, next to app/)."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        config.CALIBRATION_FILE)


def _point_value(p: dict) -> float:
    """x-value of a stored point: reaction_strength (legacy 'yellow'/'S')."""
    for key in ("reaction_strength", "yellow", "S"):
        if key in p:
            return float(p[key])
    return 0.0


def load_points() -> List[dict]:
    path = _path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("points", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read calibration file: %s", exc)
        return []


def add_point(reaction_strength: float, known_curcumin_percent: float) -> int:
    """Append one calibration point and persist. Returns new total count."""
    with _lock:
        points = load_points()
        points.append({"reaction_strength": float(reaction_strength),
                       "curcumin_percent": float(known_curcumin_percent)})
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump({"points": points}, fh, indent=2)
    logger.info("Added calibration point reaction_strength=%.5f pct=%.3f "
                "(total=%d)", reaction_strength, known_curcumin_percent,
                len(points))
    return len(points)


def fit() -> Optional[Tuple[float, float]]:
    """Return (slope, intercept) for curcumin_% = slope*reaction + intercept."""
    points = load_points()
    if len(points) < config.CALIBRATION_MIN_POINTS:
        return None
    x = np.array([_point_value(p) for p in points], dtype=np.float64)
    pct = np.array([float(p["curcumin_percent"]) for p in points],
                   dtype=np.float64)
    if np.allclose(x, x[0]):  # degenerate: all same x -> no slope possible
        logger.warning("Calibration points share one value; using heuristic.")
        return None
    slope, intercept = np.polyfit(x, pct, 1)
    return float(slope), float(intercept)


def status() -> dict:
    """Human/debug-friendly snapshot of the calibration state."""
    points = load_points()
    f = fit()
    return {
        "num_points": len(points),
        "min_points_required": config.CALIBRATION_MIN_POINTS,
        "mode": "calibrated" if f else "heuristic",
        "fit": {"slope": f[0], "intercept": f[1]} if f else None,
        "points": points,
    }
