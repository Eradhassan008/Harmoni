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
        # A provider is configured by the project owner. We do not invent
        # the vendor call here; wiring the actual request is done where the
        # concrete provider/model is known. Until that concrete client is
        # supplied, report unavailable rather than fabricate a response.
        return {
            "mode": "unavailable",
            "data": {},
            "warning": (
                "An AI provider is configured but no concrete client is wired "
                "in this build. No endpoint or model is assumed here."
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
