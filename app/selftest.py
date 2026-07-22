"""
selftest.py
===========
Pure-math startup sanity checks — NO images, NO network, and no permanent
mutation of the real calibration_data.json file. Runs automatically at app
startup (see main.py's lifespan) and prints PASS/FAIL for each check. Also
runnable standalone:

    python -m app.selftest

These exist specifically to catch the exact class of bug this fix addresses: a
valid, strong signal (reaction_strength=0.6418, our OWN 100%-purity
calibration anchor) silently rendering as "0%" instead of its real value.
See the FIX 1 / FIX 2 notes in app/config.py, app/estimator.py and
app/confidence.py for the full story.

(a) reaction_strength 0.6418 -> ~100
(b) reaction_strength 0.3816 -> ~50
(c) a saturated (near-black) synthetic ROI -> retake message, never 0
(d) higher signal always scores >= lower signal (monotonic), including
    under an adversarial (negative-slope) calibration fit
"""

from __future__ import annotations

import logging
import sys

import numpy as np

from . import calibration, colorimetry, config, estimator

logger = logging.getLogger("haldi.selftest")

_PASS, _FAIL = "PASS", "FAIL"


def _report(label: str, ok: bool, detail: str = "") -> bool:
    tag = _PASS if ok else _FAIL
    line = f"[SELFTEST] {tag:4s}  {label}"
    if detail:
        line += f"   ({detail})"
    print(line)
    if not ok:
        logger.error("Self-test FAILED: %s %s", label, detail)
    return ok


def test_calibration_formula() -> bool:
    """(a)/(b): the seeded ground-truth points must fit correctly."""
    xs = np.array([p[0] for p in config.SEED_CALIBRATION_POINTS], dtype=np.float64)
    ys = np.array([p[1] for p in config.SEED_CALIBRATION_POINTS], dtype=np.float64)
    slope, intercept = np.polyfit(xs, ys, 1)
    tol = config.SELF_TEST_TOLERANCE

    ok_slope = abs(slope - 192.2) < 2.0
    ok_intercept = abs(intercept - (-23.3)) < 2.0
    _report("calibration fit slope ~= 192.2", ok_slope, f"slope={slope:.3f}")
    _report("calibration fit intercept ~= -23.3", ok_intercept,
            f"intercept={intercept:.3f}")

    purity_pure = slope * 0.6418 + intercept
    purity_besan = slope * 0.3816 + intercept
    ok_pure = abs(purity_pure - 100.0) < tol
    ok_besan = abs(purity_besan - 50.0) < tol
    _report("(a) reaction_strength=0.6418 -> ~100", ok_pure,
            f"got {purity_pure:.1f}%")
    _report("(b) reaction_strength=0.3816 -> ~50", ok_besan,
            f"got {purity_besan:.1f}%")
    return ok_slope and ok_intercept and ok_pure and ok_besan


def test_estimator_wiring() -> bool:
    """
    Exercise the REAL estimate_purity() through calibration.fit()'s exact
    3-tuple contract — WITHOUT touching the real calibration_data.json file.
    calibration.fit is monkeypatched for the duration of this test only, and
    always restored (even on failure) via try/finally.
    """
    xs = np.array([p[0] for p in config.SEED_CALIBRATION_POINTS], dtype=np.float64)
    ys = np.array([p[1] for p in config.SEED_CALIBRATION_POINTS], dtype=np.float64)
    slope, intercept = np.polyfit(xs, ys, 1)

    original_fit = calibration.fit
    calibration.fit = lambda: (float(slope), float(intercept), len(xs))
    try:
        est_pure = estimator.estimate_purity(0.6418)
        est_besan = estimator.estimate_purity(0.3816)
    finally:
        calibration.fit = original_fit

    tol = config.SELF_TEST_TOLERANCE
    ok_pure = (est_pure.mode == "calibrated"
               and abs(est_pure.purity_index - 100.0) < tol)
    ok_besan = (est_besan.mode == "calibrated"
                and abs(est_besan.purity_index - 50.0) < tol)
    _report("estimator: pure sample -> ~100% (calibrated, not 0)", ok_pure,
            f"purity_index={est_pure.purity_index} mode={est_pure.mode}")
    _report("estimator: 50%-besan -> ~50% (calibrated, not 0)", ok_besan,
            f"purity_index={est_besan.purity_index} mode={est_besan.mode}")

    # The exact regression this fix targets: a stronger signal must never
    # collapse to a number at or below a weaker signal's score.
    no_collapse = (est_pure.purity_index > 0 and est_besan.purity_index > 0
                   and est_pure.purity_index >= est_besan.purity_index)
    _report("no 0% collapse: pure's score >= besan's score", no_collapse,
            f"pure={est_pure.purity_index} besan={est_besan.purity_index}")
    return ok_pure and ok_besan and no_collapse


def test_saturated_not_zero() -> bool:
    """
    (c) A genuinely saturated (near-black, uniform) synthetic ROI must yield a
    clear retake message from the sample-quality gate — never a bare 0.
    Purely synthetic numpy arrays; no real photograph is involved.
    """
    size = 40
    black = np.full((size, size, 3), 8, dtype=np.uint8)  # near-black, uniform

    m = colorimetry.measure_image(black)
    msg = colorimetry.sample_quality(m, "synthetic saturated ROI")
    ok = msg is not None and "dark" in msg.lower()
    _report("(c) saturated black ROI -> retake message (never a bare 0)",
            ok, f"message={msg!r}")
    return ok


def test_monotonic() -> bool:
    """(d) A higher reaction_strength must never score lower than a weaker one."""
    original_fit = calibration.fit
    calibration.fit = lambda: None  # force heuristic mode for a clean sweep
    try:
        values = [0.0, 0.05, 0.1216, 0.3816, 0.5, 0.6418, 0.9, 1.2, 1.5, 3.0]
        ok = estimator.check_monotonic(values)
    finally:
        calibration.fit = original_fit
    _report("(d) purity is monotonic non-decreasing (heuristic mode)", ok)

    # Also verify the guard against a BAD (negative-slope) calibration fit: it
    # must be rejected and fall back to the heuristic, never silently produce
    # a decreasing score as the signal gets stronger.
    calibration.fit = lambda: (-50.0, 80.0, 2)  # adversarial: negative slope
    try:
        est_low = estimator.estimate_purity(0.1)
        est_high = estimator.estimate_purity(0.9)
        guard_ok = (est_low.mode == "heuristic" and est_high.mode == "heuristic"
                    and est_high.purity_index >= est_low.purity_index)
    finally:
        calibration.fit = original_fit
    _report("(d) negative-slope calibration rejected (stays monotonic)",
            guard_ok,
            f"low={est_low.purity_index} high={est_high.purity_index}")
    return ok and guard_ok


def run_self_tests() -> bool:
    print("\n" + "=" * 70)
    print("HALDI STARTUP SELF-TESTS")
    print("=" * 70)
    results = [
        test_calibration_formula(),
        test_estimator_wiring(),
        test_saturated_not_zero(),
        test_monotonic(),
    ]
    all_ok = all(results)
    print("-" * 70)
    print(f"SELF-TESTS: {'ALL PASS' if all_ok else 'SOME FAILED'} "
          f"({sum(results)}/{len(results)} groups passed)")
    print("=" * 70 + "\n")
    return all_ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(0 if run_self_tests() else 1)
