# Haldi Curcumin-Purity Estimator 🌿

A small **FastAPI + web** tool that gives an **indicative** curcumin-purity score
(0–100%) for turmeric (*haldi*) powder from **two smartphone photos** of the same
alcohol extract:

* **Before** — the extract on its own.
* **After** — the same extract after adding **NAVDHI REAGENT₃** reagent.

It uses a **deterministic colour-science pipeline** — *no neural network, no
training data*. The **primary** number comes from the **strength of the NAVDHI REAGENT₃
reaction** (the curcumin-specific blue-channel change), and the plain-extract
**yellowness** is used only as a **dye cross-check**. An optional vision model
can only add a *qualitative* side-note.

> ⚠️ **Indicative / relative estimate for screening only. Not a certified lab
> measurement.** This tool certifies nothing and accuses no brand of anything.

---

## Quick start

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run the server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000** in your browser.

---

## How the science works

**Primary signal = strength of the NAVDHI REAGENT₃ reaction.** NAVDHI REAGENT₃ reacts specifically
with curcumin — *not* with yellow dyes (metanil yellow, tartrazine) and *not*
with pale dilution adulterants (**besan / gram flour, starch, chalk**). So the
reaction is the true discriminator: a pure extract reacts strongly, a diluted or
dyed one barely reacts. We take the **magnitude** of the blue-channel absorbance
change:

```
reaction_delta    = A_blue(after) − A_blue(before)      # A = -log10(norm)
reaction_strength = | reaction_delta |                  # curcumin-specific
```

> Why not yellowness? Because **besan is also yellow**. In real samples pure
> (yellow 0.349) and 50% besan (yellow 0.316) look almost identical in colour —
> but their reactions differ hugely (**−0.53 vs −0.01**). The reaction separates
> them; the colour does not.

**Cross-check signal = plain-extract yellowness (dye flag only).** Measured on
the **white-normalised** BEFORE sample:

```
before_yellow = (normR + normG) / 2 − normB
```

If a sample is **yellow but barely reacts**, that yellow probably isn't curcumin
→ **dye flag** ("yellow but no curcumin reaction: likely diluted/dyed").

### The pipeline (see `app/colorimetry.py`)

For **each** image: take the **Sample ROI** (centred ≈ 25% square by default) and
a **White reference ROI** (white paper in frame; fallback = brightest ~2% of
pixels), then white-normalise each channel `norm = sample / white` (clipped to
`0.001–1.0`) and take absorbance `A = -log10(norm)`.

Then:

1. **`reaction_strength`** (primary) → **`purity_index`**
   `= clip(reaction_strength / REACTION_REF × 100, 0, 100)`, where `REACTION_REF`
   is the reaction strength of a pure/premium sample (score 100; default 0.55).
2. **`before_yellow`** (cross-check) → dye flag.
3. **Band:** `<30` Low, `30–60` Below average, `60–85` Good, `>85` Premium-range.

**White-reference comparability gate (mandatory).** The two photos must share the
same white background/lighting. We compare the two white points by
brightness-invariant chromaticity; if they differ beyond
`WHITE_COMPARABILITY_TOLERANCE` the app returns **“Images not comparable — retake
both with same white background, lighting and distance.”** *instead of* a score
(the raw signed values are still shown so you can see why).

**Dye flag:** `before_yellow` HIGH but `reaction_delta` LOW → *“Yellow but little
curcumin reaction: possible added colour.”*

**Saturation guard:** if the AFTER ROI is near-black (mean channels below
`DARK_THRESHOLD`) the app notes *“reaction saturated, using extract colour
only.”* — the primary signal only uses the BEFORE image, so the score still
stands.

> `YELLOW_MIN`, `YELLOW_REF`, the comparability tolerance and every threshold are
> **literature-anchored heuristics, not measured values**, and live in one place:
> the **CONFIG block** in [`app/config.py`](app/config.py). Calibrate to make
> scores physically meaningful (below).

---

## How calibration works (two-point, seeded with real ground truth)

The estimator sits behind one clean function,
`estimate_purity(reaction_strength)` in [`app/estimator.py`](app/estimator.py).
Calibration points map `reaction_strength` DIRECTLY to a known `purity_%`
(0-100) — `purity = slope·reaction_strength + intercept`, fitted with
`numpy.polyfit` (degree 1). **No code changes, no retraining of the app.**

On first run, `calibration_data.json` is auto-seeded (see
`config.SEED_CALIBRATION_POINTS`) with two real measured points:

```
(reaction_strength=0.3816, known_purity=50)   — a 50%-adulterated reference
(reaction_strength=0.6418, known_purity=100)  — a pure/premium reference
```

giving `slope ≈ 192.2, intercept ≈ -23.3`. An existing calibration file is
**never** overwritten by seeding.

```bash
# add more calibration points as you measure real samples
curl -X POST http://127.0.0.1:8000/calibrate \
  -H "Content-Type: application/json" \
  -d '{"reaction_strength": 0.50, "known_purity_percent": 72.5}'

# inspect current calibration state
curl http://127.0.0.1:8000/calibrate
```

The UI shows whether a result is **"calibrated (N points)"** or the
**"uncalibrated heuristic"** fallback (`REACTION_REF`, only used if fewer
than 2 valid points exist). A calibration fit with a non-positive slope is
automatically rejected (it would make purity *fall* as the reaction gets
*stronger*) and the app falls back to the heuristic instead. `yellow_value` /
`S_value` are accepted as legacy aliases for `reaction_strength`;
`known_curcumin_percent` is accepted as a legacy alias for
`known_purity_percent`.

### Startup self-tests

Every app start runs pure-math sanity checks (no images, no network) and
prints `PASS`/`FAIL` for each — this is the regression guard for the exact
bug class described below. Run them standalone any time:

```bash
python -m app.selftest
```

**Plugging in a trained model later:** `estimator.py` has a clearly marked
`TODO(model)` block showing exactly where a fitted scikit-learn regressor drops
in — the endpoints, dye flag, frontend and disclaimers all stay the same.

---

## Optional Gemma vision layer (qualitative only)

Fully optional and **off by default**. If you set `GEMMA_ENDPOINT`, both images
are POSTed to that OpenAI-compatible vision endpoint and the model is asked
**only** for a qualitative description (uniform vs cloudy liquid, unusual
brightness, visible particles). It is shown as a secondary *"AI observation
(qualitative only)"* note and **never** produces the purity number.

```bash
export GEMMA_ENDPOINT="https://your-endpoint/v1/chat/completions"
export GEMMA_API_KEY="sk-..."   # optional bearer token
uvicorn app.main:app --reload
```

If `GEMMA_ENDPOINT` is unset (or the call fails), it is skipped silently and the
app works fully without it. The request shape and model name live in the CONFIG
block and `app/gemma.py`.

---

## API

| Method | Path         | Purpose                                                        |
|--------|--------------|----------------------------------------------------------------|
| GET    | `/`          | Serve the single-page frontend.                                |
| POST   | `/analyze`   | multipart: `before_image`, `after_image`, optional ROI fields. |
| POST   | `/calibrate` | Add `(reaction_strength, known_purity_percent)` point(s).      |
| GET    | `/calibrate` | Current calibration status.                                    |
| GET    | `/health`    | Liveness probe.                                                |

`/analyze` returns (a normal, comparable read):

```json
{
  "comparable": true,
  "warning": null,
  "purity_index": 100.0,
  "purity_display": "100%",
  "band": "Premium-range",
  "confidence": "Medium",
  "low_confidence": false,
  "calibration_mode": "calibrated",
  "calibration_mode_label": "calibrated (2 points)",
  "reaction_strength": 0.6418,
  "reaction_delta": 0.6418,
  "before_yellow": 0.349,
  "white_diff": 0.0,
  "white_tolerance": 0.08,
  "dye_flag": false,
  "dye_note": null,
  "saturation_note": null,
  "gemma_note": null,
  "disclaimer": "Indicative / relative estimate for screening only. Not a certified lab measurement.",
  "debug": { "mode": "calibrated", "calibration_points_used": 2, "calibration_fit": {"slope": 192.16, "intercept": -23.33}, "reaction_strength_mean": 0.6418, "purity_index_math": 100.0, "confidence_breakdown": {}, "before": {"sample_rgb": []}, "after": {}, "white_before": [], "white_after": [], "...": "norms, absorbances, after_darkness" }
}
```

A saturated (near-black) or protocol-violating read returns a clear message
instead of a bare number — `purity_index` is `null`, never `0`:

```json
{
  "sample_ok": false,
  "saturated": true,
  "warning": "Reading saturated — the sample/reaction is too dark to measure reliably. Retake with a thinner, more dilute liquid layer.",
  "purity_index": null,
  "purity_display": null
}
```

When the two whites don't match, `comparable` is `false`, `purity_index`/`band`
are `null`, and `warning` carries the retake message — but `reaction_strength`,
`before_yellow`, `white_diff` and the full `debug` block are **still returned**.

Optional ROI fields (`before_sample_roi`, `after_sample_roi`,
`before_white_roi`, `after_white_roi`) accept `"a,b,c,d"` — values in `[0,1]` are
fractions of the image, otherwise absolute pixels.

---

## Project layout

```
haldimodel/
├── app/
│   ├── config.py        # ALL tunable constants (one CONFIG block)
│   ├── colorimetry.py   # deterministic colour-science pipeline
│   ├── estimator.py     # estimate_purity(reaction_strength) <- swap in a model
│   ├── calibration.py   # JSON store + numpy linear fit
│   ├── gemma.py         # optional qualitative vision layer
│   ├── main.py          # FastAPI endpoints
│   └── static/index.html# single-page frontend (Tailwind CDN)
├── requirements.txt
└── README.md
```

---

## Required imaging protocol

For a comparable, meaningful read, shoot **both** photos the same way:

* **Same white background** — put a **sheet of white paper** in frame in *both*
  photos (it is the light reference). Mismatched whites → no score.
* **Same lighting and same camera distance** (~9 cm) for the before/after pair.
* A **thin, consistent liquid layer** (same vial, same fill depth) so thickness
  doesn't change the colour.
* The vial/liquid filling a small central ROI; steady, no reflections/glare.

## Reliability: bulletproof pipeline, never a silent 0

Every numeric step (white-normalisation, `-log10`, absorbance) is guarded with
`isfinite`/`nan_to_num` checks that log loudly and clamp to a safe bound rather
than let a degenerate input (empty ROI, pure-black patch, corrupt pixels)
silently become `0`. A calibration fit with a non-positive slope is rejected
(it would make purity *fall* as the reaction gets *stronger* — the one
invariant the whole app depends on) and the app falls back to the heuristic
instead. The `/analyze` pipeline is wrapped in try/except: any unexpected
failure returns a structured `{"error": true, "detail": "..."}` response, never
an unexplained crash or a fake number.

A genuinely saturated (near-black) AFTER photo is **blocked** with a clear
"retake, too dark" message — `purity_index` is `null`, not `0`. This matters
in the API/JSON sense specifically: `null` and `0` are different values, and a
client must not conflate them (in JavaScript, `Number(null) === 0` — check for
`typeof x === "number"` first, as `app/static/index.html` now does).

## Known limitations

* `REACTION_REF` and every threshold are **heuristics, not measured** — treat the
  index as *relative* until you calibrate.
* The score relies on the NAVDHI REAGENT₃ reaction being visible and consistent; the AFTER
  photo must actually capture the reacted colour (add reagent, mix, then shoot).
* Yellowness alone cannot tell curcumin from besan/yellow dye — that is exactly
  why the reaction strength (not colour) drives the score.
* Uncontrolled lighting, coloured backgrounds, reflections, a missing white
  reference, or different before/after setups reduce accuracy or block the score.
* This is a screening aid, **not** a lab assay, and cannot detect every
  adulterant.
