"""
main.py
=======
FastAPI app for the Haldi curcumin-purity estimator.

Endpoints:
  GET  /            -> serve the single-page frontend
  POST /analyze     -> two images (+ optional ROIs) -> purity JSON
  POST /calibrate   -> add (reaction_strength, known_curcumin_%) pts; refits map
  GET  /calibrate   -> current calibration status (debug / inspection)
  GET  /health      -> liveness probe

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import calibration, colorimetry, config, estimator, gemma, qc

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("haldi.main")

app = FastAPI(
    title="Haldi Curcumin-Purity Estimator",
    description="Deterministic smartphone-colorimetry screening tool "
                "(no neural network).",
    version="1.0.0",
)

# --- CORS: the frontend may be hosted elsewhere (Netlify) ------------------
_origins = os.environ.get(config.CORS_ORIGINS_ENV_VAR,
                          config.CORS_ORIGINS_DEFAULT)
_origin_list = [o.strip() for o in _origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origin_list,
    # Credentials cannot be combined with the "*" wildcard; we don't use
    # cookies/auth, so this stays False.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS allowed origins: %s", _origin_list)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Serve static assets (the Navdhi logo, etc.) under /static.
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# GET /  -> frontend
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "gemma_enabled": gemma.is_enabled(),
        "qc_enabled": config.QC_ENABLED,
        "qc_gemma_can_block": config.QC_GEMMA_CAN_BLOCK,
        "cors_origins": _origin_list,
    }


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------
@app.post("/analyze")
async def analyze(
    before_image: UploadFile = File(...),
    after_image: UploadFile = File(...),
    # Optional ROI fields: "a,b,c,d". Values in [0,1] = fractions of the image;
    # otherwise absolute pixels. Sample ROIs default to a centred 25%-area crop;
    # white ROIs default to the auto brightest-2% fallback.
    before_sample_roi: Optional[str] = Form(None),
    after_sample_roi: Optional[str] = Form(None),
    before_white_roi: Optional[str] = Form(None),
    after_white_roi: Optional[str] = Form(None),
) -> JSONResponse:
    # --- read + decode both images -----------------------------------------
    try:
        before_raw = await before_image.read()
        after_raw = await after_image.read()
    except Exception as exc:  # unreadable upload stream
        raise HTTPException(status_code=400,
                            detail=f"Could not read upload: {exc}")

    try:
        before_img = colorimetry.decode_image(before_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"BEFORE image: {exc}")
    try:
        after_img = colorimetry.decode_image(after_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"AFTER image: {exc}")

    bh, bw = before_img.shape[:2]
    ah, aw = after_img.shape[:2]

    # --- parse optional ROIs (fractions or pixels) -------------------------
    b_sample = colorimetry.parse_roi(before_sample_roi, bw, bh)
    a_sample = colorimetry.parse_roi(after_sample_roi, aw, ah)
    b_white = colorimetry.parse_roi(before_white_roi, bw, bh)
    a_white = colorimetry.parse_roi(after_white_roi, aw, ah)

    # --- IMAGE QC gate (fail-open; see app/qc.py) --------------------------
    # Runs before the pipeline so an unusable pair costs nothing. It can only
    # reject for objective technical reasons — never content/moderation.
    b_roi_eff = b_sample or colorimetry.default_sample_roi(bw, bh)
    a_roi_eff = a_sample or colorimetry.default_sample_roi(aw, ah)
    qc_result = qc.run_qc(before_img, after_img,
                          colorimetry.crop(before_img, b_roi_eff),
                          colorimetry.crop(after_img, a_roi_eff))
    if not qc_result.passed:
        logger.info("ANALYZE rejected by QC: %s", qc_result.blocking)
        return JSONResponse({
            "qc_passed": False,
            "qc_issues": qc_result.blocking,
            "qc_warnings": qc_result.warnings,
            "qc_note": qc_result.gemma_note,
            "warning": config.QC_BLOCKED_MESSAGE,
            "comparable": None,
            "purity_index": None,
            "band": None,
            "reaction_strength": None,
            "reaction_delta": None,
            "before_yellow": None,
            "dye_flag": False,
            "dye_note": None,
            "saturation_note": None,
            "gemma_note": None,
            "disclaimer": config.DISCLAIMER,
            "debug": {"qc": qc_result.details},
        })

    # --- core colour-science pipeline --------------------------------------
    signal = colorimetry.compute_signal(
        before_img, after_img,
        before_sample_roi=b_sample, after_sample_roi=a_sample,
        before_white_roi=b_white, after_white_roi=a_white,
    )

    # --- optional Gemma qualitative note (never affects the number) --------
    gemma_note = gemma.get_gemma_note(before_img, after_img)

    # PRIMARY signal: magnitude of the curcumin-specific reaction.
    reaction_strength = abs(signal.reaction_delta)

    # Raw, signed, ALWAYS-exposed values (so two different reads never look the
    # same and the debug panel can always show them).
    base = {
        "qc_passed": True,
        "qc_issues": [],
        "qc_warnings": qc_result.warnings,
        "qc_note": qc_result.gemma_note,
        "reaction_strength": round(reaction_strength, 5),
        "reaction_delta": round(signal.reaction_delta, 5),
        "before_yellow": round(signal.before_yellow, 5),
        "white_diff": round(signal.white_diff, 5),
        "white_tolerance": config.WHITE_COMPARABILITY_TOLERANCE,
        "gemma_note": gemma_note,
        "disclaimer": config.DISCLAIMER,
    }

    # --- comparability gate: same white background/lighting required -------
    if not signal.comparable:
        payload = {
            **base,
            "comparable": False,
            "warning": config.NOT_COMPARABLE_MESSAGE,
            "purity_index": None,
            "band": None,
            "dye_flag": False,
            "dye_note": None,
            "saturation_note": None,
            "debug": {"mode": None, "estimated_curcumin_percent": None,
                      "reaction_strength": round(reaction_strength, 5),
                      "qc": qc_result.details, **signal.debug},
        }
        logger.info("ANALYZE not comparable: white_diff=%.4f > tol=%.4f "
                    "(reaction_strength=%.5f before_yellow=%.5f)",
                    signal.white_diff, config.WHITE_COMPARABILITY_TOLERANCE,
                    reaction_strength, signal.before_yellow)
        return JSONResponse(payload)

    # --- purity estimate from PRIMARY signal (heuristic or calibrated) -----
    est = estimator.estimate_purity(reaction_strength)

    # --- dye cross-check: yellow but weak curcumin reaction ----------------
    dye_flag = (signal.before_yellow >= config.DYE_YELLOW_MIN
                and reaction_strength <= config.DYE_REACTION_MAX)
    dye_note = config.DYE_NOTE if dye_flag else None

    # --- saturation guard: after image near-black -> reaction unreliable ---
    saturation_note = (config.SATURATION_NOTE
                       if signal.after_darkness < config.DARK_THRESHOLD else None)

    payload = {
        **base,
        "comparable": True,
        "warning": None,
        "purity_index": round(est.purity_index, 1),
        "band": est.band,
        "dye_flag": bool(dye_flag),
        "dye_note": dye_note,
        "saturation_note": saturation_note,
        "debug": {
            "mode": est.mode,
            "estimated_curcumin_percent": est.estimated_curcumin_percent,
            "purity_index": round(est.purity_index, 1),
            "reaction_strength": round(reaction_strength, 5),
            "qc": qc_result.details,
            **signal.debug,
        },
    }
    logger.info("ANALYZE result: purity_index=%.1f band=%r mode=%s dye_flag=%s "
                "reaction_strength=%.5f before_yellow=%.5f sat=%s",
                payload["purity_index"], est.band, est.mode, dye_flag,
                reaction_strength, signal.before_yellow,
                saturation_note is not None)
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# POST /calibrate  (and GET for inspection)
# ---------------------------------------------------------------------------
def _first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


class CalibrationPoint(BaseModel):
    # `reaction_strength` is the primary signal the sample produced.
    # `yellow_value` / `S_value` are accepted as back-compat aliases.
    reaction_strength: Optional[float] = Field(
        None, description="reaction_strength value for this sample")
    yellow_value: Optional[float] = Field(None, description="alias (legacy)")
    S_value: Optional[float] = Field(None, description="alias (legacy)")
    known_curcumin_percent: float = Field(
        ..., ge=0, description="Lab-known curcumin % by mass")

    def value(self) -> Optional[float]:
        return _first_not_none(self.reaction_strength, self.yellow_value,
                               self.S_value)


class CalibrationRequest(BaseModel):
    # Accept either a single flat point or a list of points.
    reaction_strength: Optional[float] = None
    yellow_value: Optional[float] = None  # back-compat alias
    S_value: Optional[float] = None       # back-compat alias
    known_curcumin_percent: Optional[float] = None
    points: Optional[List[CalibrationPoint]] = None


@app.post("/calibrate")
def calibrate(req: CalibrationRequest) -> dict:
    pairs: List[tuple] = []
    for p in (req.points or []):
        v = p.value()
        if v is None:
            raise HTTPException(
                status_code=400,
                detail="Each point needs reaction_strength (or yellow_value).")
        pairs.append((v, p.known_curcumin_percent))

    flat_val = _first_not_none(req.reaction_strength, req.yellow_value,
                               req.S_value)
    if flat_val is not None and req.known_curcumin_percent is not None:
        pairs.append((flat_val, req.known_curcumin_percent))

    if not pairs:
        raise HTTPException(
            status_code=400,
            detail="Provide reaction_strength + known_curcumin_percent, or a "
                   "'points' list.")

    for reaction_strength, pct in pairs:
        calibration.add_point(reaction_strength, pct)

    status = calibration.status()
    status["disclaimer"] = config.DISCLAIMER
    return status


@app.get("/calibrate")
def calibrate_status() -> dict:
    status = calibration.status()
    status["disclaimer"] = config.DISCLAIMER
    return status
