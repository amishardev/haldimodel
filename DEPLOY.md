# Deployment — Netlify frontend + Windows backend + Cloudflare Tunnel

The project is split into two independently deployed halves:

```
frontend/   ->  static site  ->  Netlify        (public HTTPS)
app/        ->  FastAPI      ->  your Windows server
                                    |
                              Cloudflare Tunnel  (public HTTPS, no port-forward)
```

The browser loads the page from Netlify, then calls your Windows box through the
tunnel URL. No ports opened on your router, no public IP needed.

---

## Part 1 — Backend on the Windows server

```bat
git clone https://github.com/amishardev/haldimodel.git
cd haldimodel
run-backend.bat
```

`run-backend.bat` creates the venv, installs dependencies on first run, and
starts uvicorn on `0.0.0.0:8000`. Verify locally:

```bat
curl http://localhost:8000/health
```

You should see `{"status":"ok", ...}`. Manual equivalent if you prefer:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set CORS_ORIGINS=*
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Run it as a background Windows service (optional)

So it survives logout/reboot, using [NSSM](https://nssm.cc/):

```bat
nssm install HaldiBackend "C:\path\to\haldimodel\.venv\Scripts\python.exe" ^
      "-m uvicorn app.main:app --host 0.0.0.0 --port 8000"
nssm set HaldiBackend AppDirectory "C:\path\to\haldimodel"
nssm start HaldiBackend
```

---

## Part 2 — Cloudflare Tunnel (permanent URL)

### Install

```bat
winget install --id Cloudflare.cloudflared
```

(or download `cloudflared-windows-amd64.exe` from the cloudflared releases page
and rename it to `cloudflared.exe` on your PATH)

### ⚠️ Permanent vs temporary — read this first

| | Needs a domain? | URL survives restart? |
|---|---|---|
| **Named tunnel** (recommended) | **Yes** — a domain on Cloudflare | ✅ Permanent |
| **Quick tunnel** | No | ❌ New random URL every restart |

A genuinely *permanent* link requires a domain added to your Cloudflare account
(the domain can be cheap; Cloudflare's DNS/tunnel usage here is free). Without
one you can only get `*.trycloudflare.com` URLs that change on every restart —
which means editing `frontend/config.js` and redeploying every single time.

### A. Named tunnel — permanent (do this one)

```bat
REM 1. one-time browser login, pick your domain
cloudflared tunnel login

REM 2. create the tunnel (prints a UUID — note it down)
cloudflared tunnel create haldi-api

REM 3. point a subdomain at it
cloudflared tunnel route dns haldi-api haldi-api.yourdomain.com
```

Create `C:\Users\<YOU>\.cloudflared\config.yml`:

```yaml
tunnel: <PASTE-TUNNEL-UUID-HERE>
credentials-file: C:\Users\<YOU>\.cloudflared\<PASTE-TUNNEL-UUID-HERE>.json

ingress:
  - hostname: haldi-api.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Run it, then install as a service so it auto-starts:

```bat
cloudflared tunnel run haldi-api

REM once it works, make it permanent:
cloudflared service install
```

Your permanent backend URL is now **`https://haldi-api.yourdomain.com`**.

### B. Quick tunnel — testing only

```bat
cloudflared tunnel --url http://localhost:8000
```

Prints a random `https://xxxx-yyyy.trycloudflare.com`. Fine for a demo, but it
changes every restart.

### Verify the tunnel

```bat
curl https://haldi-api.yourdomain.com/health
```

---

## Part 3 — Frontend on Netlify

1. Push this repo to GitHub (see below).
2. Netlify → **Add new site** → **Import an existing project** → pick
   `amishardev/haldimodel`.
3. Settings are already declared in `frontend/netlify.toml`:
   - Base directory: `frontend`
   - Build command: *(empty — pure static, no build)*
   - Publish directory: `frontend`
4. Deploy.

### Point the frontend at your backend

Edit **`frontend/config.js`** — this is the only line that matters:

```js
window.HALDI_API_BASE = "https://haldi-api.yourdomain.com";
```

Commit and push; Netlify redeploys automatically.

> **Test without redeploying:** append `?api=` to your Netlify URL —
> `https://your-site.netlify.app/?api=https://haldi-api.yourdomain.com`
> The value is remembered in localStorage. Clear it with a bare `?api=`.

### Lock down CORS (recommended once live)

On the Windows server, replace `*` with your real site:

```bat
set CORS_ORIGINS=https://your-site.netlify.app
```

---

## Part 4 — Optional: Gemma photo QC on your GPU server

Entirely optional. The app scores fine without it. When enabled it **only**
judges photo quality (blurry / too dark / glare / no vial / no white card) and
**can never change the purity number**.

On the GPU box:

```bash
ollama pull gemma3:4b && ollama serve          # or: vllm serve google/gemma-3-4b-it
```

On the Windows server, before starting the backend:

```bat
set GEMMA_ENDPOINT=http://YOUR-GPU-HOST:11434/v1/chat/completions
set GEMMA_API_KEY=
```

(If you use Ollama, set `GEMMA_MODEL = "gemma3:4b"` in `app/config.py`.)

Confirm with `curl https://haldi-api.yourdomain.com/health` →
`"gemma_enabled": true`.

**It is built to fail open.** If the GPU box is down, slow, returns junk, or
the model refuses on "moderation" grounds, the photo is **accepted** and the
analysis proceeds. It can only reject for the five technical reason codes in
`QC_ALLOWED_REJECT_REASONS`; anything else it invents is discarded and logged.
To make it advisory-only, set `QC_GEMMA_CAN_BLOCK = False` in `app/config.py`.

---

## Pushing to GitHub

```bat
git init
git add .
git commit -m "Haldi curcumin-purity estimator: split frontend/backend + QC"
git branch -M main
git remote add origin https://github.com/amishardev/haldimodel.git
git push -u origin main
```

If the repo already has commits, use `git pull --rebase origin main` first.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Netlify page loads, "Failed to fetch" on Analyse | `HALDI_API_BASE` wrong/empty, or the tunnel is down. Check `/health` in a browser. |
| CORS error in console | `CORS_ORIGINS` doesn't include your Netlify URL. Set it to `*` to confirm, then tighten. |
| Logo missing on Netlify | `frontend/static/navdhi-logo.png` must be committed. |
| Backend URL changes constantly | You're on a quick tunnel — switch to a named tunnel (needs a domain). |
| Every photo rejected | Shouldn't happen (fail-open). Check the `debug.qc` block in the response; set `QC_ENABLED = False` to bypass entirely. |
