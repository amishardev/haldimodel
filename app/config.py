"""
config.py
=========
ALL tunable constants for the Haldi (turmeric) curcumin-purity estimator live
here, in one clearly commented CONFIG block. Edit values here to re-tune the
app; nothing else in the codebase hard-codes these numbers.

Scientific basis (smartphone colorimetry, NO neural network / NO dataset):
  * PRIMARY signal = the STRENGTH of the reagent (NAVDHI REAGENT3) reaction, i.e. the
    MAGNITUDE of the BLUE-channel absorbance change between AFTER and BEFORE:
        reaction_strength = |A_blue(after) - A_blue(before)|.
    The reaction is specific to curcumin (NOT to metanil yellow / tartrazine
    dye, and NOT to pale dilution adulterants like besan/starch/chalk), so it is
    the true discriminator: a pure extract reacts strongly, a diluted or dyed one
    barely reacts. purity_index scales reaction_strength against REACTION_REF.
    (Yellowness alone fails here because besan is ALSO yellow.)
  * DYE-CHECK signal = the YELLOWNESS of the plain (BEFORE) extract,
    (meanR + meanG)/2 - meanB on the white-normalised sample ROI. Used ONLY for
    the dye flag: looks yellow but reacts weakly => the yellow is not curcumin.
"""

# ===========================================================================
# ============================ CONFIG (edit me) =============================
# ===========================================================================

# --- Sample ROI (the vial / liquid) ----------------------------------------
# Default sample ROI when the user does not draw one: a centred SQUARE whose
# area is this fraction of the whole image (0.25 => "middle 25%").
DEFAULT_SAMPLE_ROI_AREA_FRAC = 0.25

# --- AUTOMATIC SAMPLE DETECTION --------------------------------------------
# The blind centre crop was the single biggest source of wrong scores: when the
# sample is a stain/smear on white paper (rather than liquid filling a vial),
# the crop averages in mostly background. Measured on a real-world-style photo
# it diluted the signal 34x (reaction 0.208 -> 0.006) and turned a good sample
# into "1% - likely adulterated".
#
# Instead of averaging a rectangle, we build a MASK of pixels that actually
# differ from the white reference, and average only those.
#
#   deviation = 1 - min(R/Rw, G/Gw, B/Bw)
#     ~0.00 for white paper
#     ~0.83 for a yellow turmeric stain
#     ~0.89 for a dark post-reagent blob
AUTO_SAMPLE_DETECT = True
# A pixel counts as "sample" at or above this deviation from white.
SAMPLE_DEVIATION_THRESHOLD = 0.25
# A pixel that deviates from white must ALSO be either coloured (saturated)
# or genuinely dark to count as sample — this excludes shadow/lighting
# vignette on plain paper, which deviates from white (it's darker) but is
# NOT sample: it stays low-saturation/neutral. Verified against 8 real phone
# photos: without this, shadow/vignette alone could claim 70%+ of the frame.
SAMPLE_SAT_MIN = 0.20      # HSV saturation (0-1) at/above which a pixel counts as "coloured"
SAMPLE_DARK_VAL_MAX = 0.45  # HSV value (0-1) at/below which a pixel counts as "genuinely dark"
# If fewer than this fraction of pixels qualify, fall back to taking the most
# deviant SAMPLE_FALLBACK_PERCENT of pixels (never leaves us with nothing).
SAMPLE_MIN_PIXEL_FRAC = 0.002
SAMPLE_FALLBACK_PERCENT = 10.0
# Keep only the largest connected blob. This drops the loose powder specks
# scattered around a smear, which would otherwise skew the mean.
SAMPLE_LARGEST_BLOB_ONLY = True

# --- White reference ROI (white paper in frame) ----------------------------
# The white reference is REQUIRED for a meaningful, comparable read. If the user
# does not mark a white patch, fall back to the brightest WHITE_REFERENCE_TOP_
# PERCENT % of pixels (which should be the in-frame white paper).
WHITE_REFERENCE_TOP_PERCENT = 2.0
# Minimum pixel count for the auto white reference to be trusted before we
# fall back to simply taking the N brightest pixels.
WHITE_REFERENCE_MIN_PIXELS = 25

# --- White normalisation / absorbance --------------------------------------
# norm_ch = sample_ch / white_ch, clipped to this (min, max) before -log10.
# Floor is intentionally tiny (1e-4): -log10 of ANYTHING in (0, 1] is finite,
# so this floor exists only to guarantee we never hand log10 a literal zero.
# The value is NOT what bounds the top of the absorbance scale — MAX_ABSORBANCE
# does that, unconditionally, after the log. So lowering this floor cannot
# produce a wrong (too-low) reading; it only removes a needless early clamp.
NORM_CLIP_MIN = 1e-4
NORM_CLIP_MAX = 1.0
# Hard cap on per-channel absorbance. Beyond this the reading is saturated and
# no longer proportional to concentration, so we refuse to pretend otherwise.
MAX_ABSORBANCE = 1.7
# Floor for the white-reference denominator (0-255 pixel scale) before
# dividing. Prevents true division-by-zero on a pure-black white patch; the
# NORM_CLIP above then bounds the resulting ratio regardless of how small the
# real denominator was.
WHITE_DENOM_MIN = 1.0

# --- White-reference comparability gate ------------------------------------
# The two photos must be shot on the same white background / lighting. We compare
# the two white points by brightness-invariant chromaticity (L1 distance between
# the normalised white RGBs). If it exceeds this tolerance the images are NOT
# comparable and we return a warning instead of a score.
WHITE_COMPARABILITY_TOLERANCE = 0.08

# --- PRIMARY signal: reaction strength -> purity_index ----------------------
# reaction_strength = |A_blue(after) - A_blue(before)|      (curcumin-specific)
# purity_index = clip( reaction_strength / REACTION_REF * 100, 0, 100 ).
#
#   !!! IMPORTANT !!!
#   REACTION_REF is a LITERATURE-ANCHORED HEURISTIC, *not* a measured constant:
#   the reaction strength expected from a pure / premium sample (score 100).
#   Calibrate with real samples via POST /calibrate to replace it with a fit.
REACTION_REF = 1.2     # reaction_strength of a pure premium extract -> 100

# When the raw (uncapped) ratio exceeds 100 the sample was too concentrated /
# too thick for the linear range, so we say so instead of silently showing a
# flat "100%" that hides the problem.
OVER_RANGE_LABEL = ">100 (over-range — retake thinner sample)"
OVER_RANGE_NOTE = (
    "Reading is over-range: the sample is too concentrated or too thick to "
    "measure linearly. Dilute it or use a thinner layer and retake."
)

# --- TWO-POINT CALIBRATION (replaces the REACTION_REF guess once seeded) ---
# Calibration points map reaction_strength -> a DIRECTLY KNOWN purity_%
# (0-100), fitted as  purity = slope * reaction_strength + intercept
# (numpy.polyfit degree 1). Seeded here with real measured ground truth so the
# app starts calibrated instead of guessing:
#     (0.3816, 50)  -> a 50%-adulterated (besan) reference sample
#     (0.6418, 100) -> a pure / premium reference sample
# These two points give slope ~= 192.2, intercept ~= -23.3 (verified in
# app/selftest.py). Seeded ONLY if calibration_data.json does not already
# exist — an existing file (real user data) is never overwritten.
SEED_CALIBRATION_POINTS = [
    (0.3816, 50.0),
    (0.6418, 100.0),
]

# --- Band labels from the 0-100 score --------------------------------------
# (upper_exclusive_bound, label). Evaluated low -> high; last entry is the top.
BANDS = [
    (30.0, "Low / likely adulterated or low-grade"),
    (60.0, "Below average"),
    (85.0, "Good"),
    (float("inf"), "Premium-range"),
]

# --- Dye / adulteration cross-check flag -----------------------------------
# Raised when the plain extract looks yellow but the reaction is weak
# (yellow colour that is NOT curcumin -> diluted / added colour).
DYE_YELLOW_MIN = 0.25    # before_yellow >= this counts as "looks yellow"
DYE_REACTION_MAX = 0.05  # reaction_strength <= this counts as "little reaction"

# --- Saturation guard -------------------------------------------------------
# If the AFTER ROI mean across all channels is below this (0-255), the reaction
# has gone near-black / saturated and the reading is no longer trustworthy.
# This BLOCKS scoring (a clear "saturated, retake" message — never a bare
# number that a fragile null-check downstream could mis-render as "0%").
DARK_THRESHOLD = 40.0
SATURATED_RETAKE_MESSAGE = (
    "Reading saturated — the sample/reaction is too dark to measure "
    "reliably. Retake with a thinner, more dilute liquid layer."
)

# --- SAMPLE QUALITY GATE (protocol enforcement, runs before scoring) -------
# The dominant source of bad readings is protocol, not maths: users photograph
# a thick wet paste instead of the thin clear liquid. A paste saturates every
# channel, so pure and adulterated both pin at the same value. Rather than
# return a confident wrong number, we refuse and explain what to change.
SAMPLE_QUALITY_ENABLED = True

# ROI mean brightness (0-255) below this = a dark paste/solid blob, not liquid.
SAMPLE_DARK_MEAN_MIN = 45.0
# A dark AND near-uniform ROI is a solid blob (real liquid has some texture and
# gradient). Only applied when the ROI is already on the dark side.
SAMPLE_DARK_STD_MAX = 12.0
# If every normalised channel is above this, the crop is essentially blank
# paper — the coloured liquid was not captured.
SAMPLE_PALE_NORM_MAX = 0.95

SAMPLE_TOO_DARK_MESSAGE = (
    "Sample too dark/thick — photograph the thin clear liquid, not the paste."
)
SAMPLE_NO_SAMPLE_MESSAGE = (
    "No sample detected in crop — crop the coloured liquid area."
)

# --- CONSISTENCY MODE (optional repeat readings) ---------------------------
# The user may upload the SAME sample 2-3 times. We average reaction_strength
# and report the spread (max-min) so protocol noise becomes visible instead of
# being hidden behind one confident-looking number.
CONSISTENCY_MAX_REPLICATES = 3
# The tolerance must be RELATIVE to the signal. An absolute limit is useless
# here: real reaction_strength values are ~0.1-0.5, so a fixed 0.15 would be
# nearly the whole signal and the warning would never fire.
# Inconsistent when:  spread > max(CONSISTENCY_MIN_ABS_SPREAD,
#                                  CONSISTENCY_MAX_SPREAD_FRAC * mean)
CONSISTENCY_MAX_SPREAD_FRAC = 0.20   # 20% of the mean reading
CONSISTENCY_MIN_ABS_SPREAD = 0.01    # floor, so tiny signals don't over-trigger
CONSISTENCY_WARNING = (
    "Readings inconsistent — fix lighting/exposure/sample thickness."
)

# --- User-facing messages (tunable text) -----------------------------------
NOT_COMPARABLE_MESSAGE = (
    "Images not comparable — retake both with same white background, lighting "
    "and distance."
)
DYE_NOTE = (
    "Yellow but no curcumin reaction: likely diluted/dyed."
)
SATURATION_NOTE = (
    "Reaction saturated (after image near-black); using extract colour only."
)

# --- Calibration store ------------------------------------------------------
# Where (yellow_value, known_curcumin_%) calibration points are persisted (JSON).
CALIBRATION_FILE = "calibration_data.json"
# Need at least this many points before we trust a fitted linear map.
CALIBRATION_MIN_POINTS = 2

# --- Optional Gemma vision layer (fully optional; qualitative note only) ----
# Set the GEMMA_ENDPOINT environment variable to enable. If unset, it is
# skipped silently and the app works fully without it. Gemma NEVER produces the
# purity number — only a secondary, qualitative description.
GEMMA_ENV_VAR = "GEMMA_ENDPOINT"        # env var holding the POST URL
GEMMA_API_KEY_ENV_VAR = "GEMMA_API_KEY" # optional bearer token env var
GEMMA_MODEL = "gemma-3-4b-it"           # sent as the "model" field
GEMMA_TIMEOUT_SECONDS = 20
GEMMA_MAX_IMAGE_DIM = 512               # downscale before sending (keeps it light)
# The ONLY thing we ask Gemma for is a short qualitative description.
GEMMA_PROMPT = (
    "You are assisting a turmeric screening tool. These are two photos of a "
    "liquid turmeric extract (image 1 = before a reagent, image 2 = after). In "
    "2-3 short sentences describe ONLY qualitative visual observations: does the "
    "liquid look uniform or cloudy, is the brightness unusual, are there visible "
    "undissolved particles or sediment? Do NOT estimate purity, concentration, "
    "quality, or give any number or score."
)

# --- IMAGE QUALITY CONTROL (QC) --------------------------------------------
# Two layers, both deliberately PERMISSIVE. The guiding rule is FAIL-OPEN:
# when in doubt we ANALYSE. A wrongly-rejected good photo is a worse bug than a
# slightly noisy score, so every uncertain path resolves to "usable".
#
# Layer 1 = deterministic local checks (always on, no GPU, cannot "moralise").
# Layer 2 = optional Gemma vision QC (only if GEMMA_ENDPOINT is set).
QC_ENABLED = True

# -- Layer 1 thresholds (lenient on purpose) --
# Variance of the Laplacian, measured on the auto-detected sample region.
# This app MEASURES MEAN PIXEL COLOUR, not edges — it does not need sharp
# focus the way an edge-detection or OCR task would, so this only exists to
# catch a photo destroyed by heavy motion blur, not ordinary soft macro focus.
# Calibrated against 8 real macro phone photos of liquid stains (which,
# having soft/feathered edges rather than hard printed lines, score as low as
# 6.5 while still being perfectly usable) vs. a synthetically destroyed photo
# (heavy Gaussian blur) at 0.7 — 3.0 sits well clear of the real destroyed
# case while accepting every real usable photo measured so far.
QC_BLUR_MIN = 3.0
# Mean brightness (0-255) of the BEFORE sample ROI. Only catches near-black.
# NOTE: applied to BEFORE only — the AFTER photo is *expected* to go dark when
# the reaction develops, so darkness there is normal, not a defect.
QC_DARK_MEAN_MIN = 25.0
# Fraction of blown-out (==255) pixels in the sample ROI that means "glare".
QC_CLIP_MAX_FRAC = 0.35

# -- Layer 2: Gemma QC (anti-over-rejection rules) --
# May Gemma actually BLOCK an analysis, or only warn? Even when True, it can
# only block for a reason in QC_ALLOWED_REJECT_REASONS below.
QC_GEMMA_CAN_BLOCK = True
# The ONLY reasons an image may ever be rejected. Anything else Gemma invents
# (especially content/safety/moderation talk) is DISCARDED and treated as pass.
QC_ALLOWED_REJECT_REASONS = [
    "blurry",
    "too_dark",
    "too_bright",
    "liquid_not_visible",
    "no_white_reference",
]
# If the model replies with refusal/moderation language, we treat the whole QC
# response as invalid and PASS. Photo quality is the only question being asked.
QC_REFUSAL_MARKERS = [
    "i cannot", "i can't", "i'm unable", "i am unable", "as an ai",
    "i'm sorry", "i am sorry", "cannot assist", "can't assist",
    "not appropriate", "inappropriate", "policy", "guidelines",
    "unable to provide", "i won't", "i will not",
]
# Strict, quality-only prompt. It explicitly forbids moderation refusals and
# tells the model to default to usable when unsure.
QC_PROMPT = (
    "You are a CAMERA QUALITY CHECKER for a laboratory colour-measurement tool.\n"
    "The two photos show a turmeric (haldi) spice extract in a glass or vial, "
    "photographed for a colour test. This is ordinary food and laboratory "
    "content and is always acceptable to analyse.\n\n"
    "YOUR ONLY JOB is to judge whether the photos are TECHNICALLY good enough to "
    "measure colour from. You are NOT a content moderator.\n\n"
    "HARD RULES:\n"
    "- NEVER mark images unusable for safety, moderation, appropriateness, "
    "legality or content reasons. That is not your job and such an answer is "
    "invalid.\n"
    "- NEVER reject because the sample looks dirty, unusual, discoloured, "
    "cloudy, or because you cannot identify the substance. Odd-looking samples "
    "are exactly what this tool is for.\n"
    "- NEVER refuse to answer. Always return the JSON.\n"
    "- If you are unsure for ANY reason, answer usable: true.\n\n"
    "Mark usable: false ONLY if a photo is genuinely unmeasurable, using only "
    "these reason codes:\n"
    '  "blurry"            - badly out of focus\n'
    '  "too_dark"          - essentially black, no colour readable\n'
    '  "too_bright"        - blown out / heavy glare over the liquid\n'
    '  "liquid_not_visible"- no vial/liquid visible in the frame at all\n'
    '  "no_white_reference"- no white paper/card visible in the frame\n\n'
    "Reply with STRICT JSON only, no prose, no markdown:\n"
    '{"usable": true, "reasons": [], "note": "one short sentence"}'
)
QC_MAX_TOKENS = 250

# Message shown when QC blocks an analysis (never accusatory, always actionable).
QC_BLOCKED_MESSAGE = (
    "Photo quality too low to measure. Please retake both photos: "
    "steady/in focus, even lighting, the liquid clearly visible, and a piece of "
    "white paper in frame."
)

# --- RATE LIMITING & RESOURCE GUARDS ---------------------------------------
# Why this matters: a single 12 MP analysis peaks around 428 MB of RAM and
# ~0.45 s of CPU. An unprotected public endpoint can be trivially OOM-ed, so
# these three guards work together:
#   1. downscale  -> makes each request much cheaper
#   2. size cap   -> rejects absurd uploads before decoding
#   3. rate limit -> caps how often any one client can queue work
RATE_LIMIT_ENABLED = True

# Per-IP sliding window, requests per minute.
# /analyze is the expensive one; a human screening samples needs ~1 per 5 s.
RATE_LIMIT_ANALYZE_PER_MIN = 15
RATE_LIMIT_CALIBRATE_PER_MIN = 30
RATE_LIMIT_DEFAULT_PER_MIN = 120        # /health, /static, etc.
RATE_LIMIT_WINDOW_SECONDS = 60

# Behind ngrok / Cloudflare the socket IP is the proxy, so every user would
# share one bucket. Read the real client IP from X-Forwarded-For instead.
# Set False only if the app is exposed directly with no proxy in front.
RATE_LIMIT_TRUST_PROXY_HEADERS = True

# Reject an upload larger than this (per image) before it is decoded.
# A 12 MP phone JPEG is ~5 MB, so 25 MB is generous.
MAX_UPLOAD_MB = 25

# Downscale the long edge of each image before processing. The pipeline only
# takes MEANS over regions, so this is essentially scale-invariant, but it cuts
# RAM and CPU by roughly an order of magnitude on 12 MP photos.
# Set to 0 to disable downscaling.
#   NOTE: ROIs sent as FRACTIONS (what the frontend sends) are unaffected.
#   Absolute-PIXEL ROIs must be expressed against the downscaled image.
MAX_PROCESS_DIM = 1600

# ===========================================================================
# LAYER 1 — SCENE UNDERSTANDING (Gemma vision, optional)
# ===========================================================================
# Gemma answers WHERE and HOW (which region is the thin liquid, which patch is
# clean paper under the same light, is the pair aligned). It NEVER answers HOW
# MUCH — the purity number is always produced by Layer 2 arithmetic.
# If GEMMA_ENDPOINT is unset or anything fails, we fall back to auto-detect:
#   sample = most saturated / most deviant coloured blob
#   paper  = brightest WHITE_REFERENCE_TOP_PERCENT of pixels
SCENE_MAX_TOKENS = 700

# Reject a returned box smaller than this fraction of the image (VLMs sometimes
# emit degenerate or hallucinated boxes) and fall back to auto-detect.
SCENE_MIN_BOX_FRAC = 0.0004
# Side of the auto-detected white-paper patch, as a fraction of the image's
# short side. Big enough to average out noise, small enough to sit on clean
# paper without swallowing the sample.
PAPER_PATCH_FRAC = 0.12
VALID_LAYER_QUALITY = ["thin", "thick", "paste"]

SCENE_PROMPT = (
    "You are a VISION LOCATOR for a laboratory colour-measurement tool. You "
    "locate regions. You NEVER estimate concentration, purity or quality — "
    "another system does the measurement.\n\n"
    "You are given TWO photos of a turmeric extract test:\n"
    "  IMAGE 1 = BEFORE reagent\n"
    "  IMAGE 2 = AFTER reagent\n\n"
    "For EACH image find:\n"
    "  sample_bbox : the THIN TRANSLUCENT COLOURED LIQUID to measure.\n"
    "                Prefer a thin, evenly-lit liquid layer.\n"
    "                AVOID dark thick paste or a solid blob — those saturate\n"
    "                the reading and make the measurement useless.\n"
    "  paper_bbox  : a CLEAN WHITE PAPER patch NEAR the sample, under the\n"
    "                SAME lighting (no shadow, no glare, no sample on it).\n"
    "                This is the lighting reference, so it must be lit the\n"
    "                same as the sample.\n\n"
    "Also report:\n"
    "  usable        : false ONLY if the region truly cannot be measured\n"
    "                  (no liquid visible, hopeless blur, total darkness).\n"
    "                  NEVER false for content/safety/appropriateness reasons.\n"
    "                  If unsure, answer true.\n"
    "  reject_reason : short text, only when usable is false.\n"
    "  layer_quality : \"thin\" | \"thick\" | \"paste\"\n"
    "  align_ok      : true if BOTH photos show the same sample in the same\n"
    "                  place with the same setup/framing.\n\n"
    "Boxes are [x, y, width, height] in PIXELS of that image.\n"
    "Reply with STRICT JSON only — no prose, no markdown fences:\n"
    '{"before":{"sample_bbox":[0,0,0,0],"paper_bbox":[0,0,0,0],"usable":true,'
    '"reject_reason":"","layer_quality":"thin"},'
    '"after":{"sample_bbox":[0,0,0,0],"paper_bbox":[0,0,0,0],"usable":true,'
    '"reject_reason":"","layer_quality":"thin"},"align_ok":true}'
)

# ===========================================================================
# LAYER 3 — FUSION / CONFIDENCE
# ===========================================================================
# Confidence never changes the number; it decides how loudly we stand behind
# it. Weights sum to 1.0.
CONFIDENCE_WEIGHTS = {
    "usable": 0.25,        # Gemma says both regions are measurable
    "align_ok": 0.20,      # before/after show the same setup
    "thin_layer": 0.25,    # layer_quality == "thin" for both
    # NOTE: "not_saturated" uses after_darkness < DARK_THRESHOLD (a genuinely
    # near-black read), NOT "did any single channel touch MAX_ABSORBANCE". A
    # strong, valid, pure reaction can legitimately push ONE channel to the
    # absorbance cap without being an artifact — penalizing that would
    # systematically punish exactly the strongest genuine signals (including
    # this app's own 100%-purity calibration anchor). See app/confidence.py.
    "not_saturated": 0.20,
    "signal_in_range": 0.10,  # raw ratio sits inside the linear window
}
# A component we genuinely cannot evaluate (Gemma disabled) scores this rather
# than 0 — absence of evidence must not masquerade as evidence of a problem.
CONFIDENCE_UNKNOWN_CREDIT = 0.5
CONFIDENCE_HIGH_MIN = 0.80
CONFIDENCE_MEDIUM_MIN = 0.50
# Without scene understanding we cannot honestly claim "High".
CONFIDENCE_CAP_WITHOUT_GEMMA = "Medium"
# Raw-ratio window treated as a clean, comfortably linear reading.
CONFIDENCE_SIGNAL_MIN_RATIO = 5.0
CONFIDENCE_SIGNAL_MAX_RATIO = 100.0
LOW_CONFIDENCE_PREFIX = "Low-confidence, retake: "

# --- Startup self-tests -----------------------------------------------------
# Pure-math sanity checks (no images, no network) that run once at process
# startup and print PASS/FAIL for each. Catches exactly the class of bug this
# fix addresses: a signal that silently renders as "0%" instead of its real
# value or a clear message. See app/selftest.py; also runnable standalone via
# `python -m app.selftest`.
SELF_TEST_ON_STARTUP = True
# Tolerance (purity points) for the calibration-anchor self-tests (a)/(b).
SELF_TEST_TOLERANCE = 2.0

# --- CORS (frontend hosted separately, e.g. Netlify) -----------------------
# Comma-separated origins allowed to call this API, via the CORS_ORIGINS env
# var. "*" allows any origin (fine here: the API is public and stateless).
# Example: CORS_ORIGINS="https://haldi.netlify.app,http://localhost:8000"
CORS_ORIGINS_ENV_VAR = "CORS_ORIGINS"
CORS_ORIGINS_DEFAULT = "*"

# --- Fixed disclaimer shown with every number ------------------------------
DISCLAIMER = (
    "Indicative / relative estimate for screening only. "
    "Not a certified lab measurement."
)

# ===========================================================================
# ========================== end CONFIG block ===============================
# ===========================================================================
