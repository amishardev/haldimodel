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

## How to calibrate later (improves accuracy when data exists)

The estimator sits behind one clean function,
`estimate_purity(reaction_strength)` in [`app/estimator.py`](app/estimator.py).
Feed it real samples with lab-known curcumin content and it switches from the
heuristic to a fitted linear map automatically — **no code changes, no retraining
of the app.**

```bash
# add calibration points: the reaction_strength your sample produced + lab-known %
curl -X POST http://127.0.0.1:8000/calibrate \
  -H "Content-Type: application/json" \
  -d '{"reaction_strength": 0.01, "known_curcumin_percent": 0.2}'

curl -X POST http://127.0.0.1:8000/calibrate \
  -H "Content-Type: application/json" \
  -d '{"reaction_strength": 0.53, "known_curcumin_percent": 8.0}'

# inspect current calibration state
curl http://127.0.0.1:8000/calibrate
```

Once **≥ 2 points** exist, the app fits
`curcumin_% = slope·reaction_strength + intercept` (`numpy.polyfit`, degree 1)
and uses that instead of `REACTION_REF`. Points are stored in
`calibration_data.json`. (`yellow_value` / `S_value` are accepted as legacy
aliases for `reaction_strength`.)

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
| POST   | `/calibrate` | Add `(reaction_strength, known_curcumin_percent)` point(s).    |
| GET    | `/calibrate` | Current calibration status.                                    |
| GET    | `/health`    | Liveness probe.                                                |

`/analyze` returns (a normal, comparable read):

```json
{
  "comparable": true,
  "warning": null,
  "purity_index": 96.3,
  "band": "Premium-range",
  "reaction_strength": 0.53,
  "reaction_delta": -0.53,
  "before_yellow": 0.349,
  "white_diff": 0.0,
  "white_tolerance": 0.08,
  "dye_flag": false,
  "dye_note": null,
  "saturation_note": null,
  "gemma_note": null,
  "disclaimer": "Indicative / relative estimate for screening only. Not a certified lab measurement.",
  "debug": { "mode": "heuristic", "estimated_curcumin_percent": null, "reaction_strength": 0.53, "before": {"sample_rgb": []}, "after": {}, "white_before": [], "white_after": [], "...": "norms, absorbances, after_darkness, purity_index" }
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
