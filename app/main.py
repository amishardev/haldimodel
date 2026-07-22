"""
main.py
=======
FastAPI app for the Haldi curcumin-purity estimator.

Endpoints:
  GET  /            -> serve the single-page frontend
  POST /analyze     -> two images (+ optional ROIs) -> purity JSON
  POST /calibrate   -> add (reaction_strength, known_purity_%) pts; refits map
  GET  /calibrate   -> current calibration status (debug / inspection)
  GET  /health      -> liveness probe

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (calibration, colorimetry, confidence, config, estimator, gemma,
               qc, ratelimit, scene, selftest)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("haldi.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Seed real ground-truth calibration points on first run only — never
    # touches an existing (real user) calibration file.
    if calibration.seed_if_empty():
        logger.info("Calibration store seeded from CONFIG.SEED_CALIBRATION_POINTS.")
    # Pure-math startup self-tests: no images, no network. Prints PASS/FAIL for
    # each check and logs an error (does not crash the app) if any fail — see
    # app/selftest.py. This is the FIX-1/FIX-2 regression guard: it exists
    # specifically so "a valid strong signal silently renders as 0%" cannot
    # ship again unnoticed.
    if config.SELF_TEST_ON_STARTUP:
        ok = selftest.run_self_tests()
        if not ok:
            logger.error("STARTUP SELF-TESTS FAILED — see output above. The "
                        "app will still start, but investigate immediately.")
    yield


app = FastAPI(
    title="Haldi Curcumin-Purity Estimator",
    description="Deterministic smartphone-colorimetry screening tool "
                "(no neural network).",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# MIDDLEWARE ORDER MATTERS — do not swap these two blocks.
#
# Starlette's add_middleware() INSERTS AT THE FRONT, so whatever is registered
# LAST ends up OUTERMOST. The rate limiter is therefore registered first and
# CORS second, which makes CORS the outer layer that wraps everything.
#
# Why it matters: a 429 short-circuits and never reaches the inner handlers. If
# CORS were inside the limiter, that 429 would go back without an
# Access-Control-Allow-Origin header and the browser would surface an opaque
# "Failed to fetch" instead of the real "too many requests" message.
# ---------------------------------------------------------------------------

# --- Rate limiting (registered FIRST => inner) -----------------------------
@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    if not config.RATE_LIMIT_ENABLED or request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    ip = ratelimit.client_ip(request)
    limit = ratelimit.limit_for_path(path)
    allowed, remaining, retry_after = ratelimit.check(f"{ip}:{path}", limit)

    if not allowed:
        logger.warning("Rate limit hit: ip=%s path=%s limit=%d/min", ip, path,
                       limit)
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"Too many requests. Limit is {limit} per minute. "
                          f"Try again in {retry_after}s.",
                "retry_after": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# --- CORS (registered LAST => OUTERMOST, so even a 429 carries the headers) -
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
        "calibration": calibration.status()["mode_label"],
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
    # CONSISTENCY MODE: optional repeat readings of the SAME sample. When
    # supplied, reaction_strength is averaged across pairs and the spread is
    # reported, so protocol noise is visible instead of hidden.
    before_image_2: Optional[UploadFile] = File(None),
    after_image_2: Optional[UploadFile] = File(None),
    before_image_3: Optional[UploadFile] = File(None),
    after_image_3: Optional[UploadFile] = File(None),
) -> JSONResponse:
    # --- read + decode both images -----------------------------------------
    try:
        before_raw = await before_image.read()
        after_raw = await after_image.read()
    except Exception as exc:  # unreadable upload stream
        raise HTTPException(status_code=400,
                            detail=f"Could not read upload: {exc}")

    # Reject absurd uploads BEFORE decoding them (decoding is what costs RAM).
    max_bytes = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    for label, raw in (("BEFORE", before_raw), ("AFTER", after_raw)):
        if len(raw) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} image is {len(raw)/1024/1024:.1f} MB; the "
                       f"limit is {config.MAX_UPLOAD_MB} MB.")

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
    #
    # The blur/exposure region prefers: user ROI > deterministic auto-detected
    # sample box > blind centre crop. On real phone photos, the stain is
    # rarely centred — checking blur on a blind centre crop can land on plain
    # paper (near-zero texture -> near-zero Laplacian variance), which then
    # falsely reads as "out of focus" even on a perfectly sharp photo. The
    # auto-detected box is used here (via scene.auto_sample_bbox, which is
    # pure colour-math, NOT Gemma) purely to pick a better QC region — the
    # real Layer 1 scene understanding (which may call Gemma) still only runs
    # AFTER this gate passes, so this costs nothing extra when Gemma is on.
    b_roi_eff = (b_sample or scene.auto_sample_bbox(before_img)
                or colorimetry.default_sample_roi(bw, bh))
    a_roi_eff = (a_sample or scene.auto_sample_bbox(after_img)
                or colorimetry.default_sample_roi(aw, ah))
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

    # FIX 1: wrap the entire measurement pipeline (Layers 1-3) in try/except.
    # Any unexpected failure returns a STRUCTURED error — never a silent
    # numeric 0 and never an unhandled crash with no explanation.
    try:
        payload = await _run_analysis_pipeline(
            before_img, after_img, b_sample, a_sample, b_white, a_white,
            qc_result, before_image_2, after_image_2,
            before_image_3, after_image_3, max_bytes,
        )
    except Exception as exc:
        logger.exception("ANALYZE pipeline crashed unexpectedly.")
        return JSONResponse(status_code=500, content={
            "error": True,
            "detail": f"Internal analysis error: {type(exc).__name__}: {exc}",
            "qc_passed": True,
            "comparable": None,
            "sample_ok": None,
            "purity_index": None,
            "purity_display": None,
            "band": None,
            "confidence": None,
            "disclaimer": config.DISCLAIMER,
            "debug": {"exception_type": type(exc).__name__,
                     "exception": str(exc)},
        })
    return JSONResponse(payload)


async def _run_analysis_pipeline(
    before_img, after_img, b_sample, a_sample, b_white, a_white,
    qc_result, before_image_2, after_image_2, before_image_3, after_image_3,
    max_bytes,
) -> dict:
    """
    Layers 1-3 of the pipeline, returning a plain payload dict.

    Split out of analyze() so the whole thing can be wrapped in one
    try/except at the call site (FIX 1: never let an unexpected failure here
    surface as anything other than a clear, structured error).
    """
    # =====================================================================
    # LAYER 1 — SCENE UNDERSTANDING (Gemma, or deterministic auto-detect)
    # Decides WHERE to measure. Never contributes to the number.
    # =====================================================================
    scene_pair = scene.understand(before_img, after_img)

    # A ROI the user drew always wins over Gemma's suggestion.
    if b_sample is None:
        b_sample = scene_pair.before.sample_bbox
    else:
        scene_pair.before.sample_bbox = b_sample
        scene_pair.before.sample_source = "user"
    if a_sample is None:
        a_sample = scene_pair.after.sample_bbox
    else:
        scene_pair.after.sample_bbox = a_sample
        scene_pair.after.sample_source = "user"

    # The paper patch becomes the white reference: sample and paper share the
    # same light, so dividing one by the other cancels the lighting. This is
    # the main accuracy lever in the whole pipeline.
    if b_white is None:
        b_white = scene_pair.before.paper_bbox
    else:
        scene_pair.before.paper_bbox = b_white
        scene_pair.before.paper_source = "user"
    if a_white is None:
        a_white = scene_pair.after.paper_bbox
    else:
        scene_pair.after.paper_bbox = a_white
        scene_pair.after.paper_source = "user"

    # =====================================================================
    # LAYER 2 — RELATIVE COLOUR MATH (this, and only this, makes the number)
    # =====================================================================
    signal = colorimetry.compute_signal(
        before_img, after_img,
        before_sample_roi=b_sample, after_sample_roi=a_sample,
        before_white_roi=b_white, after_white_roi=a_white,
    )

    # --- optional Gemma qualitative note (never affects the number) --------
    gemma_note = gemma.get_gemma_note(before_img, after_img)

    # PRIMARY signal: magnitude of the curcumin-specific reaction.
    reaction_strength = abs(signal.reaction_delta)

    # --- CONSISTENCY MODE: measure any repeat pairs of the SAME sample -----
    # A bad replicate is skipped rather than failing the whole request: the
    # point of this mode is to reveal noise, not to add new failure paths.
    replicate_strengths: List[float] = []
    for idx, (b_up, a_up) in enumerate(
            ((before_image_2, after_image_2),
             (before_image_3, after_image_3)), start=2):
        if b_up is None or a_up is None:
            continue
        try:
            b_raw, a_raw = await b_up.read(), await a_up.read()
            if len(b_raw) > max_bytes or len(a_raw) > max_bytes:
                logger.warning("Replicate %d skipped: over size limit.", idx)
                continue
            rep = colorimetry.compute_signal(
                colorimetry.decode_image(b_raw), colorimetry.decode_image(a_raw))
            replicate_strengths.append(abs(rep.reaction_delta))
        except Exception as exc:
            logger.warning("Replicate %d skipped (%s).", idx, exc)

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
            "debug": {"mode": None, "calibration_points_used": 0,
                      "reaction_strength": round(reaction_strength, 5),
                      "qc": qc_result.details, **signal.debug},
        }
        logger.info("ANALYZE not comparable: white_diff=%.4f > tol=%.4f "
                    "(reaction_strength=%.5f before_yellow=%.5f)",
                    signal.white_diff, config.WHITE_COMPARABILITY_TOLERANCE,
                    reaction_strength, signal.before_yellow)
        return payload

    # --- SAMPLE QUALITY GATE: enforce the thin-liquid protocol -------------
    # A thick paste saturates every channel, so pure and adulterated read the
    # same. Refuse and say what to change rather than return a wrong number.
    quality_issues = [msg for msg in (
        colorimetry.sample_quality(signal.before, "Before photo"),
        colorimetry.sample_quality(signal.after, "After photo"),
    ) if msg]
    if quality_issues:
        logger.info("ANALYZE rejected by sample-quality gate: %s", quality_issues)
        return {
            **base,
            "comparable": None,
            "sample_ok": False,
            "sample_issues": quality_issues,
            "warning": quality_issues[0],
            "purity_index": None,
            "purity_display": None,
            "band": None,
            "dye_flag": False,
            "dye_note": None,
            "saturation_note": None,
            "debug": {"mode": None, "calibration_points_used": 0,
                      "qc": qc_result.details, **signal.debug},
        }

    # --- FIX 1: hard saturation block ---------------------------------------
    # If the AFTER photo is genuinely near-black (ROI brightness < DARK_
    # THRESHOLD), the reading is not measurable — say so explicitly with a
    # dedicated message and STOP here. This is a deliberate, literal, final
    # safety net: even if SAMPLE_QUALITY_ENABLED were turned off or its gate
    # missed a case, a truly saturated read can never fall through to the
    # estimator/confidence machinery and risk being displayed as a bare "0%".
    if signal.after_darkness < config.DARK_THRESHOLD:
        logger.info("ANALYZE blocked: after_darkness=%.1f < DARK_THRESHOLD=%.1f "
                    "(reaction_strength=%.5f would have been computed but is "
                    "not trustworthy).", signal.after_darkness,
                    config.DARK_THRESHOLD, reaction_strength)
        return {
            **base,
            "comparable": None,
            "sample_ok": False,
            "saturated": True,
            "sample_issues": [config.SATURATED_RETAKE_MESSAGE],
            "warning": config.SATURATED_RETAKE_MESSAGE,
            "purity_index": None,
            "purity_display": None,
            "band": None,
            "dye_flag": False,
            "dye_note": None,
            "saturation_note": config.SATURATED_RETAKE_MESSAGE,
            "debug": {"mode": None, "calibration_points_used": 0,
                      "after_darkness": round(signal.after_darkness, 2),
                      "dark_threshold": config.DARK_THRESHOLD,
                      "qc": qc_result.details, **signal.debug},
        }

    # --- CONSISTENCY MODE: average repeats, expose the spread --------------
    strengths = [reaction_strength] + replicate_strengths
    if len(strengths) > 1:
        reaction_strength = float(sum(strengths) / len(strengths))
        reaction_spread = float(max(strengths) - min(strengths))
        # Relative tolerance — see the CONFIG note; an absolute limit would be
        # the size of the whole signal and would never fire.
        tolerance = max(config.CONSISTENCY_MIN_ABS_SPREAD,
                        config.CONSISTENCY_MAX_SPREAD_FRAC * reaction_strength)
        inconsistent = reaction_spread > tolerance
    else:
        reaction_spread = 0.0
        inconsistent = False

    # --- purity estimate from PRIMARY signal (heuristic or calibrated) -----
    # NOTE: derived purely from reaction_strength, which is arithmetic over
    # pixel means. Gemma contributes nothing to this value.
    est = estimator.estimate_purity(reaction_strength)

    # =====================================================================
    # LAYER 3 — FUSION: how much do we trust this measurement?
    # =====================================================================
    conf = confidence.assess(scene_pair, signal.before, signal.after,
                             est.raw_ratio, after_darkness=signal.after_darkness)

    # --- dye cross-check: yellow but weak curcumin reaction ----------------
    dye_flag = (signal.before_yellow >= config.DYE_YELLOW_MIN
                and reaction_strength <= config.DYE_REACTION_MAX)
    dye_note = config.DYE_NOTE if dye_flag else None

    # Note: a genuine saturation block already happened earlier (see the
    # DARK_THRESHOLD check above) — by this point after_darkness is always
    # >= DARK_THRESHOLD, so there is nothing left to warn about here.
    saturation_note = None

    cal_status = calibration.status()

    payload = {
        **base,
        "comparable": True,
        "warning": config.CONSISTENCY_WARNING if inconsistent else None,
        "sample_ok": True,
        "sample_issues": [],
        # Layer 3: when confidence is Low we deliberately withhold the hard
        # number and tell the user what to fix instead.
        "confidence": conf.level,
        "confidence_score": conf.score,
        "confidence_reasons": conf.reasons,
        "low_confidence": conf.is_low,
        "low_confidence_message": (
            config.LOW_CONFIDENCE_PREFIX + "; ".join(conf.reasons)
            if conf.is_low and conf.reasons else None),
        "scene_source": scene_pair.source,
        "scene": scene.debug_dict(scene_pair),
        "purity_index": None if conf.is_low else round(est.purity_index, 1),
        "purity_display": None if conf.is_low else est.display_value,
        "over_range": est.over_range,
        "over_range_note": config.OVER_RANGE_NOTE if est.over_range else None,
        # FIX 2: whether the score came from real calibration data or the
        # REACTION_REF guess — shown directly in the UI, not buried in debug.
        "calibration_mode": est.mode,
        "calibration_mode_label": cal_status["mode_label"],
        "calibration_points_used": est.calibration_points_used,
        # consistency mode (single read => 1 replicate, spread 0)
        "reaction_strength": round(reaction_strength, 5),
        "replicates": len(strengths),
        "reaction_spread": round(reaction_spread, 5),
        "consistency_warning": (config.CONSISTENCY_WARNING
                                if inconsistent else None),
        "band": est.band,
        "dye_flag": bool(dye_flag),
        "dye_note": dye_note,
        "saturation_note": saturation_note,
        "debug": {
            "mode": est.mode,
            "calibration_points_used": est.calibration_points_used,
            "calibration_points": cal_status["points"],
            "calibration_fit": cal_status["fit"],
            "purity_index": round(est.purity_index, 1),
            "raw_ratio_uncapped": est.raw_ratio,
            "over_range": est.over_range,
            "reaction_strength_mean": round(reaction_strength, 5),
            "reaction_strength_replicates": [round(s, 5) for s in strengths],
            "reaction_spread": round(reaction_spread, 5),
            "reaction_ref": config.REACTION_REF,
            "purity_index_math": round(est.purity_index, 1),  # always present
            "confidence_breakdown": conf.breakdown,
            "confidence_score": conf.score,
            "scene": scene.debug_dict(scene_pair),
            "qc": qc_result.details,
            **signal.debug,
        },
    }
    logger.info("ANALYZE result: purity_index=%.1f band=%r mode=%s dye_flag=%s "
                "reaction_strength=%.5f before_yellow=%.5f "
                "confidence=%s scene=%s",
                est.purity_index, est.band, est.mode, dye_flag,
                reaction_strength, signal.before_yellow,
                conf.level, scene_pair.source)
    return payload


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
    # `known_purity_percent` is a DIRECTLY known purity (0-100), e.g. from a
    # lab-verified pure sample or a known dilution ratio.
    # `known_curcumin_percent` is accepted as a legacy alias — but note it is
    # NOT the same scale (mass-% vs purity-%); new points should use the
    # purity field directly.
    known_purity_percent: Optional[float] = Field(
        None, ge=0, le=100, description="Known purity % (0-100) for this sample")
    known_curcumin_percent: Optional[float] = Field(
        None, ge=0, description="Legacy alias — treated as known_purity_percent")

    def value(self) -> Optional[float]:
        return _first_not_none(self.reaction_strength, self.yellow_value,
                               self.S_value)

    def purity(self) -> Optional[float]:
        return _first_not_none(self.known_purity_percent,
                               self.known_curcumin_percent)


class CalibrationRequest(BaseModel):
    # Accept either a single flat point or a list of points.
    reaction_strength: Optional[float] = None
    yellow_value: Optional[float] = None  # back-compat alias
    S_value: Optional[float] = None       # back-compat alias
    known_purity_percent: Optional[float] = None
    known_curcumin_percent: Optional[float] = None  # legacy alias
    points: Optional[List[CalibrationPoint]] = None


@app.post("/calibrate")
def calibrate(req: CalibrationRequest) -> dict:
    pairs: List[tuple] = []
    for p in (req.points or []):
        v = p.value()
        purity = p.purity()
        if v is None or purity is None:
            raise HTTPException(
                status_code=400,
                detail="Each point needs reaction_strength (or yellow_value) "
                       "and known_purity_percent.")
        pairs.append((v, purity))

    flat_val = _first_not_none(req.reaction_strength, req.yellow_value,
                               req.S_value)
    flat_purity = _first_not_none(req.known_purity_percent,
                                  req.known_curcumin_percent)
    if flat_val is not None and flat_purity is not None:
        pairs.append((flat_val, flat_purity))

    if not pairs:
        raise HTTPException(
            status_code=400,
            detail="Provide reaction_strength + known_purity_percent, or a "
                   "'points' list.")

    for reaction_strength, purity in pairs:
        calibration.add_point(reaction_strength, purity)

    status = calibration.status()
    status["disclaimer"] = config.DISCLAIMER
    return status


@app.get("/calibrate")
def calibrate_status() -> dict:
    status = calibration.status()
    status["disclaimer"] = config.DISCLAIMER
    return status
