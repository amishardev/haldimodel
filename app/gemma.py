"""
gemma.py
========
OPTIONAL qualitative vision layer. Enabled only when the GEMMA_ENDPOINT env var
is set. Sends both images to an OpenAI-compatible chat/completions vision
endpoint and returns a short qualitative note. If the endpoint is unset, or the
request fails for any reason, this returns None and the app carries on
completely unaffected.

Gemma NEVER produces the purity number — we only ever surface its text as a
secondary "AI observation (qualitative only)" note.

The request shape lives in ONE place: _build_payload(). Edit it there to match
a different endpoint contract.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.request
from typing import List, Optional

import cv2
import numpy as np

from . import config

logger = logging.getLogger("haldi.gemma")


def is_enabled() -> bool:
    return bool(os.environ.get(config.GEMMA_ENV_VAR))


def _downscale_jpeg_b64(image_rgb: np.ndarray) -> str:
    """Downscale (keeps payload light) and return base64 JPEG string."""
    h, w = image_rgb.shape[:2]
    scale = min(1.0, config.GEMMA_MAX_IMAGE_DIM / float(max(h, w)))
    if scale < 1.0:
        image_rgb = cv2.resize(image_rgb, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise ValueError("Failed to JPEG-encode image for Gemma.")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _build_payload(before_rgb: np.ndarray, after_rgb: np.ndarray) -> dict:
    """OpenAI-compatible multimodal chat payload. Edit here for other APIs."""
    return build_vision_payload(config.GEMMA_PROMPT, [before_rgb, after_rgb],
                                max_tokens=200)


def build_vision_payload(prompt: str, images: List[np.ndarray],
                         max_tokens: int = 200) -> dict:
    """Generic OpenAI-compatible multimodal payload (shared by note + QC)."""
    def img_part(b64: str) -> dict:
        return {"type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    content: List[dict] = [{"type": "text", "text": prompt}]
    content += [img_part(_downscale_jpeg_b64(img)) for img in images]
    return {
        "model": config.GEMMA_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }


def call_vision(prompt: str, images: List[np.ndarray],
                max_tokens: int = 200) -> Optional[str]:
    """
    POST a vision request to GEMMA_ENDPOINT and return the raw text reply.
    Returns None if disabled or on ANY failure — callers must fail open.
    """
    endpoint = os.environ.get(config.GEMMA_ENV_VAR)
    if not endpoint:
        return None
    try:
        payload = build_vision_payload(prompt, images, max_tokens)
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        api_key = os.environ.get(config.GEMMA_API_KEY_ENV_VAR)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(
                req, timeout=config.GEMMA_TIMEOUT_SECONDS) as r:
            body = json.loads(r.read().decode("utf-8"))
        return _extract_text(body)
    except Exception as exc:
        logger.warning("Vision call failed (continuing without it): %s", exc)
        return None


def _extract_text(resp: dict) -> Optional[str]:
    """Pull the text out of an OpenAI-compatible (or a few common) responses."""
    try:
        return resp["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    for key in ("text", "response", "output"):
        val = resp.get(key) if isinstance(resp, dict) else None
        if isinstance(val, str):
            return val.strip()
    return None


def get_gemma_note(before_rgb: np.ndarray,
                   after_rgb: np.ndarray) -> Optional[str]:
    """Return a short qualitative note, or None if disabled / on any failure."""
    endpoint = os.environ.get(config.GEMMA_ENV_VAR)
    if not endpoint:
        return None  # feature disabled -> skip silently
    try:
        payload = _build_payload(before_rgb, after_rgb)
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        api_key = os.environ.get(config.GEMMA_API_KEY_ENV_VAR)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req,
                                    timeout=config.GEMMA_TIMEOUT_SECONDS) as r:
            body = json.loads(r.read().decode("utf-8"))
        note = _extract_text(body)
        if note:
            logger.info("Gemma note received (%d chars).", len(note))
        else:
            logger.warning("Gemma responded but no text could be extracted.")
        return note
    except Exception as exc:  # never let the optional layer break analysis
        logger.warning("Gemma layer skipped due to error: %s", exc)
        return None
