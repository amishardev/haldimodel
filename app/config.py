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
NORM_CLIP_MIN = 0.001
NORM_CLIP_MAX = 1.0

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
REACTION_REF = 0.55    # reaction_strength of a pure premium extract -> 100

# Curcumin content (% by mass) treated as "premium" / full-scale (score 100).
# Used to convert a calibrated curcumin-% prediction back onto the 0-100 scale.
PREMIUM_CURCUMIN_PCT = 8.0

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
# has gone near-black / saturated and the reaction cross-check is unreliable.
DARK_THRESHOLD = 40.0

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
# Variance of the Laplacian: sharp phone photos are typically >100, badly
# out-of-focus <10. 25 only catches genuinely unusable blur.
QC_BLUR_MIN = 25.0
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
