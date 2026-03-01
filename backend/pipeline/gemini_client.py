"""
Shared Gemini client with automatic retry on rate limits (free tier friendly).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_DAILY_LIMIT = 250  # gemini-2.5-flash free tier

# Simple daily request counter (resets on new day)
_request_count = 0
_request_date = date.today()


def get_usage() -> dict:
    """Return current daily Gemini API usage stats."""
    global _request_count, _request_date
    if date.today() != _request_date:
        _request_count = 0
        _request_date = date.today()
    return {
        "used": _request_count,
        "limit": _DAILY_LIMIT,
        "remaining": max(0, _DAILY_LIMIT - _request_count),
        "date": str(_request_date),
    }


def get_client(api_key: str | None = None) -> genai.Client:
    key = api_key or os.environ["GEMINI_API_KEY"]
    return genai.Client(api_key=key)


def generate_json(
    client: genai.Client,
    model: str,
    prompt: str,
    max_retries: int = _MAX_RETRIES,
) -> str:
    """
    Call Gemini with JSON response mode. Retries automatically on 429
    rate limit errors with exponential backoff.
    Returns the raw JSON text from the response.
    """
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            global _request_count, _request_date
            if date.today() != _request_date:
                _request_count = 0
                _request_date = date.today()
            _request_count += 1
            return response.text
        except (ClientError, ServerError) as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = 2 ** attempt * 5  # 5s, 10s, 20s, 40s, 80s
                reason = "Rate limited" if e.code == 429 else "Server unavailable"
                logger.warning(f"{reason}. Waiting {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
