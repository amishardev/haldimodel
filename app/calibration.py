"""
calibration.py
==============
Persists (reaction_strength, known_purity_percent) calibration points as a
small JSON file and, once there are >= CALIBRATION_MIN_POINTS, fits a degree-1
map DIRECTLY on purity:

        purity_% = slope * reaction_strength + intercept   (numpy.polyfit deg 1)

Until enough points exist, the estimator falls back to the REACTION_REF
heuristic. This is the "improve later when data exists" hook — no retraining of
the app is required; estimator.estimate_purity() picks up the fit automatically.

SEEDING: on first run (no calibration file yet), the store is seeded with the
two real measured ground-truth points in config.SEED_CALIBRATION_POINTS, so the
app starts CALIBRATED instead of guessing. An existing file (real user data) is
never touched by seeding.
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


def _point_purity(p: dict) -> Optional[float]:
    """
    y-value of a stored point: a DIRECTLY known purity_% (0-100).

    Legacy files may instead hold 'curcumin_percent' from the earlier
    mass-fraction design; those are not on the same scale as purity and are
    skipped rather than silently misinterpreted.
    """
    if "known_purity_percent" in p:
        return float(p["known_purity_percent"])
    if "curcumin_percent" in p:
        logger.warning("Skipping legacy calibration point with "
                       "'curcumin_percent' (%.3f) — that field is mass-%%, "
                       "not purity-%%, and is not comparable. Re-add it as "
                       "known_purity_percent.", p["curcumin_percent"])
        return None
    return None


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


def _save_points(points: List[dict]) -> None:
    with open(_path(), "w", encoding="utf-8") as fh:
        json.dump({"points": points}, fh, indent=2)


def add_point(reaction_strength: float, known_purity_percent: float) -> int:
    """Append one calibration point and persist. Returns new total count."""
    with _lock:
        points = load_points()
        points.append({"reaction_strength": float(reaction_strength),
                       "known_purity_percent": float(known_purity_percent)})
        _save_points(points)
    logger.info("Added calibration point reaction_strength=%.5f purity=%.2f%% "
                "(total=%d)", reaction_strength, known_purity_percent,
                len(points))
    return len(points)


def seed_if_empty() -> bool:
    """
    Write config.SEED_CALIBRATION_POINTS if no calibration file exists yet.

    Never overwrites a real (possibly user-collected) calibration file.
    Returns True if seeding happened.
    """
    with _lock:
        if os.path.exists(_path()):
            return False
        points = [{"reaction_strength": float(rs),
                  "known_purity_percent": float(pct)}
                 for rs, pct in config.SEED_CALIBRATION_POINTS]
        if len(points) < 2:
            return False
        _save_points(points)
    logger.info("Seeded calibration_data.json with %d ground-truth point(s) "
                "from CONFIG.", len(points))
    return True


def fit() -> Optional[Tuple[float, float, int]]:
    """
    Return (slope, intercept, n_points) for purity = slope*x + intercept, or
    None if there are fewer than CALIBRATION_MIN_POINTS usable points.

    n_points is the count actually used in the fit — surfaced so the UI can
    show "calibrated (N points)" rather than a bare mode string.
    """
    points = load_points()
    xs, ys = [], []
    for p in points:
        y = _point_purity(p)
        if y is None:
            continue
        xs.append(_point_value(p))
        ys.append(y)

    if len(xs) < config.CALIBRATION_MIN_POINTS:
        return None

    x = np.array(xs, dtype=np.float64)
    y = np.array(ys, dtype=np.float64)
    if np.allclose(x, x[0]):  # degenerate: all same x -> no slope possible
        logger.warning("Calibration points share one reaction_strength value; "
                       "using heuristic instead.")
        return None

    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept), len(xs)


def status() -> dict:
    """Human/debug-friendly snapshot of the calibration state."""
    points = load_points()
    f = fit()
    return {
        "num_points": len(points),
        "min_points_required": config.CALIBRATION_MIN_POINTS,
        "mode": "calibrated" if f else "heuristic",
        "mode_label": (f"calibrated ({f[2]} points)" if f
                      else "uncalibrated heuristic"),
        "fit": {"slope": f[0], "intercept": f[1], "n_points": f[2]} if f else None,
        "points": points,
    }
