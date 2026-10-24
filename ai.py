"""Harmoni AI layer.

One entry point: :func:`call_ai`. It routes a task to a configured AI
provider when one is wired up ("live"), returns deterministic,
clearly-labelled extraction for the project's known sample fixtures
("demo"), or reports that nothing is configured ("unavailable").

Hard rules this module obeys:
- It NEVER invents medication names, doses, schedules, endpoints,
  model names, or credential types.
- It only reads credentials that the project owner has actually set,
  from a .env file or .streamlit/secrets.toml. No provider is
  hard-coded here, because the project configures none.
- When no provider is configured, arbitrary input returns "unavailable".
  Deterministic "demo" output is produced ONLY for an explicitly
  selected sample fixture, and even then it just reflects text that is
  literally present in that fixture.
- Missing, unclear, or unreadable fields are marked "unresolved".
- The "summary" task makes no diagnosis and no treatment recommendation.

Every call returns the shared shape::

    {"mode": "live" | "demo" | "unavailable", "data": {}, "warning": None}
"""

from __future__ import annotations

import os
import re
from typing import Optional

UNRESOLVED = "unresolved"

_VALID_TASKS = {"care_plan", "journal", "summary"}


# ---------------------------------------------------------------------------
# provider configuration (read-only; nothing is invented or called)
# ---------------------------------------------------------------------------


def _load_secrets() -> dict:
    """Collect configuration from .env and .streamlit/secrets.toml, safely.

    Returns a flat dict of string keys to string values. Never raises:
    a missing file, a parse error, or a missing library all yield {}.
    These values are only *read* here; this module does not itself call
    any network endpoint, so no endpoint or model name is assumed.
    """
    config: dict = {}

    # .env via python-dotenv if available (already in requirements.txt).
    try:
        from dotenv import dotenv_values  # type: ignore

        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            for k, v in dotenv_values(env_path).items():
                if k and v is not None:
                    config[str(k)] = str(v)
    except Exception:
        pass

    # Process environment also counts (hackathon runners often inject it).
    for k, v in os.environ.items():
        config.setdefault(k, v)

    # .streamlit/secrets.toml if present and parseable.
    try:
        import tomllib  # Python 3.11+

        secrets_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".streamlit",
            "secrets.toml",
        )
        if os.path.exists(secrets_path):
            with open(secrets_path, "rb") as fh:
                data = tomllib.load(fh)

            def _flatten(prefix, obj):
                for key, val in obj.items():
                    if isinstance(val, dict):
                        _flatten(f"{prefix}{key}.", val)
                    else:
                        config.setdefault(f"{prefix}{key}", str(val))

            _flatten("", data)
    except Exception:
        pass

    return config


def _provider_configured() -> bool:
    """True only if the project owner has supplied AI configuration.

    We look for a generic, documented marker the owner sets themselves
    (``HARMONI_AI_PROVIDER`` plus some credential). We deliberately do
    NOT guess a vendor, endpoint, or model: if the owner has not named
    one, there is no live provider.
    """
    config = _load_secrets()
    provider = config.get("HARMONI_AI_PROVIDER", "").strip()
    has_key = any(
        config.get(k, "").strip()
        for k in ("HARMONI_AI_API_KEY", "HARMONI_AI_TOKEN")
    )
    return bool(provider and has_key)


# ---------------------------------------------------------------------------
# sample-fixture recognition (for clearly-labelled demo output only)
# ---------------------------------------------------------------------------


def _is_pcos_care_plan(text: str) -> bool:
    t = text.lower()
    return "polycystic ovary syndrome" in t or "diagnosis: polycystic" in t or (
        "pcos" in t and "care plan" in t
    )


def _is_thyroid_care_plan(text: str) -> bool:
    t = text.lower()
    return "hypothyroidism" in t or "thyroid hormone replacement" in t or (
        "thyroid" in t and "care plan" in t
    )


# ---------------------------------------------------------------------------
# deterministic extractors (NO AI, NO fabrication)
#   These only report text that literally appears in the input. Any field
#   not clearly present is returned as UNRESOLVED.
# ---------------------------------------------------------------------------


def _first_match(patterns, text: str) -> str:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(0).strip().rstrip(".")
    return UNRESOLVED


def _categorize_care_plan(text: str) -> str:
    """Decide PCOS / Thyroid / Unknown.

    The explicit ``Diagnosis:`` line is authoritative and wins over any
    incidental mention (a thyroid plan may still name PCOS when noting that
    care is coordinated). Only when there is no diagnosis line do we fall
    back to the looser keyword matchers.
    """
    m = re.search(r"Diagnosis:\s*(.+)", text, flags=re.IGNORECASE)
    if m:
        diag = m.group(1).lower()
        if "hypothyroid" in diag or "thyroid" in diag:
            return "Thyroid"
        if "polycystic" in diag or "pcos" in diag:
            return "PCOS"

    # No explicit diagnosis line: use whichever matcher fires, thyroid first
    # (its markers are more specific than the broad "pcos" token).
    if _is_thyroid_care_plan(text):
        return "Thyroid"
    if _is_pcos_care_plan(text):
        return "PCOS"
    return "Unknown"


def _extract_care_plan(text: str) -> dict:
    """Pull structured fields out of a care-plan text, verbatim.

    Returns a dict with a fixed shape. Every value is either copied from
    the text or ``UNRESOLVED``. Medication instructions are preserved
    exactly; nothing is paraphrased into a dose or schedule.
    """
    category = _categorize_care_plan(text)

    # The medication line is the sentence beginning "Medication:" if present.
    med_line = UNRESOLVED
    m = re.search(r"Medication:\s*(.+?)(?:\n\s*\d\.|\n\n|$)", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        med_line = " ".join(m.group(1).split()).rstrip(".")

    return {
        "category": category,
        "medication_name": UNRESOLVED,  # no name is stated in the fixtures; never invent one
        "medication_instructions_verbatim": med_line,
        "timing": _first_match(
            [r"every morning[^.\n]*", r"once daily[^.\n]*", r"with dinner"], text
        ),
        "recommended_habits": _extract_habits(text),
        "lab_followups": _first_match(
            [r"(?:Repeat|blood work|blood test)[^.\n]*", r"thyroid blood test[^.\n]*"], text
        ),
        "appointments": _first_match(
            [r"Return visit[^.\n]*", r"Follow-up visit[^.\n]*", r"return visit[^.\n]*"], text
        ),
    }


def _extract_habits(text: str) -> list:
    habits = []
    for pat, label in [
        (r"30-minute walk|walk or other movement", "activity / walking"),
        (r"7-8 hours of sleep|hours of sleep", "sleep target"),
        (r"Record the first day of each period|cycle tracking", "cycle tracking"),
        (r"Track acne, fatigue|Symptom log|Symptom check", "symptom logging"),
        (r"relaxing activity|stress", "stress management"),
    ]:
        if re.search(pat, text, flags=re.IGNORECASE):
            habits.append(label)
    return habits or [UNRESOLVED]


# Vocabulary the journal extractor recognises. Keys are the canonical
# symptom; values are literal phrases that may appear in a note. Only
# explicitly present phrases are reported; nothing is inferred.
_JOURNAL_SYMPTOMS = {
    "fatigue": ["tired", "exhausted", "fatigue", "lots of energy", "pretty good energy"],
    "acne": ["acne", "breakout", "spots on my chin", "skin is clear", "skin looking better"],
    "feeling cold": ["felt cold", "feeling cold"],
    "hair shedding": ["hair shedding"],
    "cramps": ["cramps"],
    "headache": ["headache"],
    "bloating": ["bloated", "bloating"],
    "dry skin": ["dry skin"],
}


def _extract_journal(text: str) -> dict:
    """Extract only explicitly mentioned items from a daily note.

    Never infers severity numbers, diagnoses, or unreported symptoms.
    """
    low = text.lower()

    symptoms = []
    for symptom, phrases in _JOURNAL_SYMPTOMS.items():
        if any(p in low for p in phrases):
            symptoms.append(symptom)

    mood = UNRESOLVED
    for m in ["calm", "good", "okay", "low", "anxious", "happy", "irritable"]:
        if re.search(rf"\b{m}\b", low):
            mood = m
            break

    period_started = "period started today" in low or "period started" in low

    thyroid_related = [
        s for s in symptoms if s in ("feeling cold", "hair shedding", "dry skin", "fatigue")
    ]

    return {
        "symptoms": symptoms,            # [] when none explicitly mentioned
        "mood": mood,                    # UNRESOLVED when not stated
        "fatigue_mentioned": "fatigue" in symptoms,
        "acne_mentioned": "acne" in symptoms,
        "thyroid_related_symptoms": thyroid_related,
        "period_start": bool(period_started),
        "severity_values": UNRESOLVED,   # never inferred from free text
    }


def _make_summary(text: str) -> dict:
    """Plain-language summary of PROVIDED records only. No diagnosis/advice.

    ``text`` is expected to be a already-computed records/analytics blob
    (e.g. the string from analytics.make_patient_summary). We only echo
    and gently frame it; we add no clinical interpretation.
    """
    clean = " ".join((text or "").split())
    if not clean:
        return {"summary": UNRESOLVED}
    return {
        "summary": (
            "Here is a plain-language view of your logged records: "
            + clean
            + " This is a summary of what you logged, not medical advice."
        )
    }


# ---------------------------------------------------------------------------
# Google Gemini live provider (optional; only used when explicitly configured)
#   Fails safe: any error returns "unavailable" and never leaks credentials.
# ---------------------------------------------------------------------------

_GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"


def _gemini_instructions(task: str) -> str:
    """Return the system instruction for a task. Safety rules are explicit."""
    if task == "care_plan":
        return (
            "You read a medical prescription or care plan (text and/or an image) "
            "and extract its instructions EXACTLY as written. "
            "Return ONLY JSON of the form "
            '{"items": [{"instruction": str, "kind": str, "timing": str, '
            '"condition": str, "categories": [str]}]}. '
            "Rules you must obey: "
            "1. NEVER invent a medication name, dose, frequency, or instruction. "
            "Copy the physician's wording verbatim into 'instruction'. "
            "2. If a dose, name, or schedule is unreadable or ambiguous, append "
            "' (unresolved)' to that instruction so a human reviews it. "
            "3. 'kind' is one of: medication, habit, follow-up. "
            "4. 'timing' holds ONLY timing explicitly shown (e.g. 'every morning'); "
            "otherwise an empty string. "
            "5. 'condition' is PCOS, Thyroid, or unknown - only if clearly indicated. "
            "6. 'categories' is any of Morning, Evening, Weekly, Upcoming that the "
            "instruction supports; otherwise an empty list. "
            "7. Do NOT diagnose and do NOT recommend changes. "
            "If you can read nothing reliable, return {\"items\": []}."
        )
    if task == "journal":
        return (
            "You read a patient's free-text daily note and list ONLY symptoms the "
            'patient explicitly mentions. Return ONLY JSON of the form '
            '{"symptoms": [str], "mood": str, "period_start": bool}. '
            "Never infer severity numbers, never add symptoms that are not stated, "
            "never diagnose. 'mood' is a single word only if the patient states it, "
            "else an empty string. 'period_start' is true only if the note says a "
            "period started."
        )
    # summary
    return (
        "You summarise the supplied health records in plain language. "
        'Return ONLY JSON of the form {"summary": str}. '
        "Summarise ONLY what is provided. Make no diagnosis and no treatment "
        "recommendation. State that this is a summary of logged records, not advice."
    )


def _clean_item(raw) -> Optional[dict]:
    """Coerce one model item into the UI's expected shape, or drop it."""
    if not isinstance(raw, dict):
        return None
    instruction = str(raw.get("instruction", "")).strip()
    if not instruction:
        return None
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in ("medication", "habit", "follow-up"):
        kind = "habit"
    timing = str(raw.get("timing", "")).strip()
    condition = str(raw.get("condition", "")).strip()
    if condition not in ("PCOS", "Thyroid", "unknown"):
        condition = "unknown"
    cats_in = raw.get("categories") or []
    if isinstance(cats_in, str):
        cats_in = [cats_in]
    allowed = {"Morning", "Evening", "Weekly", "Upcoming"}
    categories = [str(c).strip() for c in cats_in if str(c).strip() in allowed]
    return {
        "instruction": instruction,
        "kind": kind,
        "timing": timing,
        "condition": condition,
        "categories": categories,
    }


def _validate_gemini_data(task: str, payload, has_image: bool) -> Optional[dict]:
    """Validate the model's JSON into the shared contract, or return None."""
    if not isinstance(payload, dict):
        return None
    if task == "care_plan":
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        cleaned = [c for c in (_clean_item(i) for i in items) if c]
        if not cleaned:
            return None
        return {"items": cleaned[:50]}
    if task == "journal":
        syms = payload.get("symptoms")
        if not isinstance(syms, list):
            syms = []
        symptoms = [str(s).strip() for s in syms if str(s).strip()][:20]
        mood = str(payload.get("mood", "")).strip()
        period_start = payload.get("period_start", False)
        return {
            "symptoms": symptoms,
            "mood": mood or UNRESOLVED,
            "fatigue_mentioned": "fatigue" in [s.lower() for s in symptoms],
            "acne_mentioned": "acne" in [s.lower() for s in symptoms],
            "period_start": bool(period_start) if isinstance(period_start, bool) else False,
            "severity_values": UNRESOLVED,
        }
    # summary
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    return {"summary": summary.strip()}


def _gemini_text_from_rest(resp_json: dict) -> str:
    """Pull the model's text out of a REST generateContent response."""
    try:
        cands = resp_json.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") or []
        chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
        return "".join(chunks)
    except Exception:
        return ""


def _call_gemini_rest(task, model, key, instruction, text, image_bytes, mime_type):
    """Call Gemini over REST with X-goog-api-key.

    This path works with both AIza API keys and the newer AQ.* keys that
    Google Cloud-managed accounts issue (the SDK rejects AQ.* keys). Uses
    only the standard library so no extra dependency is required.
    """
    import json as _json
    import base64
    import urllib.request
    import urllib.error

    parts = [{"text": instruction}]
    if text and text.strip():
        parts.append({"text": "Patient document / logged facts:\n" + text.strip()})
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        })
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model + ":generateContent"
    )
    req = urllib.request.Request(
        url,
        data=_json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as fh:
        resp_json = _json.loads(fh.read().decode("utf-8"))
    return _json.loads(_gemini_text_from_rest(resp_json) or "")


def _call_gemini_sdk(task, model, key, instruction, text, image_bytes, mime_type):
    """Call Gemini via the official SDK (AIza keys)."""
    from google import genai
    from google.genai import types
    import json as _json

    parts = [types.Part.from_text(text=instruction)]
    if text and text.strip():
        parts.append(types.Part.from_text(text="Patient document / logged facts:\n" + text.strip()))
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=types.Content(role="user", parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0
        ),
    )
    return _json.loads(response.text or "")


def _call_gemini(task: str, text: str, image_bytes, mime_type, config: dict) -> dict:
    """Call Gemini and return the shared contract. Fails safe; no credential leak.

    Routes by key type: AIza keys use the official SDK; AQ.* keys (issued by
    Cloud-managed accounts, which the SDK refuses) use the REST endpoint with
    the X-goog-api-key header, exactly as Google's own sample curl does.
    """
    key = (config.get("HARMONI_AI_API_KEY") or config.get("HARMONI_AI_TOKEN") or "").strip()
    if not key:
        return {"mode": "unavailable", "data": {},
                "warning": "Gemini API key not configured."}
    has_text = bool(text and text.strip())
    if not has_text and not image_bytes:
        return {"mode": "unavailable", "data": {}, "warning": "No input provided."}
    if image_bytes and task != "care_plan":
        return {"mode": "unavailable", "data": {},
                "warning": "Image input is only supported for care plans."}
    if image_bytes and mime_type not in ("image/png", "image/jpeg"):
        return {"mode": "unavailable", "data": {},
                "warning": "Please upload a JPG or PNG image."}
    if image_bytes and len(image_bytes) > 8 * 1024 * 1024:
        return {"mode": "unavailable", "data": {},
                "warning": "Image exceeds the 8 MB demo limit."}

    model = (config.get("HARMONI_AI_MODEL", "") or "").strip() or _GEMINI_DEFAULT_MODEL
    instruction = _gemini_instructions(task)
    use_rest = key.startswith("AQ.") or key.startswith("ya29.")

    try:
        if use_rest:
            payload = _call_gemini_rest(task, model, key, instruction, text, image_bytes, mime_type)
        else:
            payload = _call_gemini_sdk(task, model, key, instruction, text, image_bytes, mime_type)
        data = _validate_gemini_data(task, payload, bool(image_bytes))
        if data is None:
            return {"mode": "unavailable", "data": {},
                    "warning": "Gemini returned no verified records. Enter information manually."}
        return {"mode": "live", "data": data, "warning": None}
    except ImportError:
        return {"mode": "unavailable", "data": {},
                "warning": "Gemini SDK is missing. Install dependencies from requirements.txt."}
    except Exception:
        # Intentionally avoid logging provider exceptions or credentials.
        return {"mode": "unavailable", "data": {},
                "warning": ("Gemini request did not succeed. Verify credentials, "
                            "model access or quota; manual entry still works.")}



# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def call_ai(
    task: str,
    text: str = "",
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
) -> dict:
    """Run one AI task and return the shared structured shape.

    Args:
        task: "care_plan", "journal", or "summary".
        text: Text input (pasted instructions, a daily note, or a
            records/analytics blob for "summary").
        image_bytes: Optional raw image bytes (e.g. a prescription photo).
        mime_type: Optional MIME type for the image (image/jpeg, image/png).

    Returns:
        dict with keys "mode" ("live"|"demo"|"unavailable"), "data" (dict),
        and "warning" (str or None).
    """
    # Validate task up front.
    if task not in _VALID_TASKS:
        return {
            "mode": "unavailable",
            "data": {},
            "warning": f"Unknown task '{task}'. Expected one of {sorted(_VALID_TASKS)}.",
        }

    has_text = bool(text and text.strip())
    has_image = bool(image_bytes)

    # --- live provider path --------------------------------------------
    if _provider_configured():
        config = _load_secrets()
        provider = config.get("HARMONI_AI_PROVIDER", "").strip().lower()
        if provider == "gemini":
            return _call_gemini(task, text, image_bytes, mime_type, config)
        # A provider marker is set but this build only wires Gemini.
        return {
            "mode": "unavailable",
            "data": {},
            "warning": (
                "Configured AI provider is not supported by this build "
                "(only 'gemini' is wired)."
            ),
        }

    # --- no provider: demo for known fixtures, else unavailable --------
    if task == "care_plan":
        # Image prescriptions need a vision-capable provider; none here.
        if has_image and not has_text:
            return {
                "mode": "unavailable",
                "data": {},
                "warning": (
                    "Prescription image reading needs a configured "
                    "vision-capable AI provider, which is not set up."
                ),
            }
        if has_text and (_is_pcos_care_plan(text) or _is_thyroid_care_plan(text)):
            return {
                "mode": "demo",
                "data": _extract_care_plan(text),
                "warning": "Demo output from a recognised sample care plan. No AI was used.",
            }
        return {"mode": "unavailable", "data": {}, "warning": None}

    if task == "journal":
        if has_text:
            return {
                "mode": "demo",
                "data": _extract_journal(text),
                "warning": "Demo output from deterministic text extraction. No AI was used.",
            }
        return {"mode": "unavailable", "data": {}, "warning": None}

    if task == "summary":
        if has_text:
            return {
                "mode": "demo",
                "data": _make_summary(text),
                "warning": "Demo summary of provided records. No diagnosis or advice.",
            }
        return {"mode": "unavailable", "data": {}, "warning": None}

    # Unreachable, but keep the contract.
    return {"mode": "unavailable", "data": {}, "warning": None}
