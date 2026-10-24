"""Harmoni AI layer (stub).

One entry point: call_ai. The real implementation will route a task
to a model (live), fall back to canned output (demo), or report that
nothing is wired up (unavailable). For now it always returns the
"unavailable" shape so the app runs without any API key.
"""

from __future__ import annotations

from typing import Optional


def call_ai(
    task: str,
    text: str = "",
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
) -> dict:
    """Run an AI task.

    Args:
        task: Name of the task to perform.
        text: Optional text input.
        image_bytes: Optional raw image bytes.
        mime_type: Optional MIME type for the image.

    Returns:
        dict with keys:
          - "mode":    "live" | "demo" | "unavailable"
          - "data":    dict of results
          - "warning": str | None
    """
    # Stub: no model wired up yet.
    return {"mode": "unavailable", "data": {}, "warning": None}
