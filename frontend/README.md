# Haldi frontend (Netlify)

Pure static site — one HTML file, a logo, and a config file. **No build step, no
Node, no Python.**

```
frontend/
├── index.html            the whole UI
├── config.js             <-- EDIT THIS: your backend tunnel URL
├── netlify.toml          Netlify settings (already configured)
└── static/
    └── navdhi-logo.png
```

## Deploy

Netlify → Add new site → Import from GitHub → pick this repo. `netlify.toml`
already sets base = `frontend`, publish = `frontend`, no build command.

## Point it at the backend

Edit `config.js`:

```js
window.HALDI_API_BASE = "https://haldi-api.yourdomain.com";
```

Leave it `""` only if FastAPI is serving the page itself (local dev at
`http://127.0.0.1:8000`) — the same `index.html` works in both modes.

**Quick test without redeploying:**
`https://your-site.netlify.app/?api=https://your-tunnel-url`
(remembered in localStorage; clear with a bare `?api=`)

See [../DEPLOY.md](../DEPLOY.md) for the backend + Cloudflare Tunnel setup.
