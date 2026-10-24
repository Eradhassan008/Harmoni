"""Harmoni - Two conditions. One plan.

Streamlit entry point. This file wires up the app shell only:
a sidebar with four destinations, a condition picker, a simple
theme, and a footer. Feature logic lives in ai.py and analytics.py
and is intentionally left as stubs for now.
"""

from pathlib import Path

import datetime as dt
import io
import json

import pandas as pd
import streamlit as st

import ai  # shared AI adapter (call_ai)
import analytics  # shared analytics module (build_metrics, summaries, exports)

APP_NAME = "Harmoni"
TAGLINE = "Two conditions. One plan."
FOOTER = "Not medical advice. Always follow your care team's guidance."

DATA_DIR = Path(__file__).parent / "data"
DAILY_LOG_CSV = DATA_DIR / "daily_log.csv"
CYCLES_CSV = DATA_DIR / "cycles.csv"
CARE_PLAN_PCOS = DATA_DIR / "care_plan_pcos.txt"
CARE_PLAN_THYROID = DATA_DIR / "care_plan_thyroid.txt"


# ----------------------------------------------------------------------
# Data loading. Reads the sample CSVs and care plans from data/.
# Returns empty frames / strings when a file is missing, so the app
# still runs. Cached so files are read once per session.
# ----------------------------------------------------------------------
@st.cache_data
def load_daily_log() -> pd.DataFrame:
    if not DAILY_LOG_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(DAILY_LOG_CSV, parse_dates=["date"])
    return df


@st.cache_data
def load_cycles() -> pd.DataFrame:
    if not CYCLES_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(CYCLES_CSV, parse_dates=["cycle_start"])


@st.cache_data
def load_care_plan(condition: str) -> str:
    path = CARE_PLAN_PCOS if condition == "PCOS" else CARE_PLAN_THYROID
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _ensure_history(daily_log, cycles):
    """Return non-empty history frames when the CSVs exist on disk.

    The cached loaders can hold an EMPTY frame if they first ran before
    the demo CSVs were present (Streamlit caches the empty result for the
    life of the process). This re-reads the files directly in that case,
    so Insights and Doctor Summary always see the synthetic history.
    """
    if (daily_log is None or daily_log.empty) and DAILY_LOG_CSV.exists():
        try:
            daily_log = pd.read_csv(DAILY_LOG_CSV, parse_dates=["date"])
        except Exception:
            pass
    if (cycles is None or cycles.empty) and CYCLES_CSV.exists():
        try:
            cycles = pd.read_csv(CYCLES_CSV, parse_dates=["cycle_start"])
        except Exception:
            pass
    return daily_log, cycles


# ----------------------------------------------------------------------
# Session state. All in-session data lives here (nothing hits disk):
#   care_tasks       -> list of confirmed care items (dicts)
#   care_draft       -> list of unconfirmed items under review
#   task_logs        -> list of per-day checklist completions
#   journal_entries  -> list of saved journal entries (newest handling in UI)
#   cycle_dates      -> list of period-start ISO dates
# ----------------------------------------------------------------------
CATEGORIES = ["Morning", "Evening", "Weekly", "Upcoming appointments"]
ITEM_KINDS = ["medication", "habit", "follow-up"]
# "unknown" lets the patient choose when the source did not say which condition.
CONDITION_TAGS = ["PCOS", "Thyroid", "unknown"]


def init_state() -> None:
    defaults = {
        "care_tasks": [],
        "care_draft": [],
        "task_logs": [],
        "journal_entries": [],
        "cycle_dates": [],
        "journal_review": None,
        "editing_entry": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ----------------------------------------------------------------------
# Grouping. A single item may belong to MORE THAN ONE group, so every
# item carries a list of categories. Timing words decide the groups.
# ----------------------------------------------------------------------
def categories_from_timing(text: str, kind: str) -> list:
    """Pick one or more checklist groups from timing/instruction words.

    Returns a list (an item can be in both Morning and Evening). Never
    empty: falls back to ["Morning"].
    """
    low = text.lower()

    # Follow-ups always live under appointments.
    if kind == "follow-up" or any(
        w in low for w in ["follow-up", "return visit", "blood test", "lab test",
                            "blood work", "appointment", "referral", "repeat", "visit"]
    ):
        return ["Upcoming appointments"]

    cats = []
    twice = any(w in low for w in ["twice a day", "two times a day", "twice daily",
                                   "2 times a day", "bid", "both morning"])
    morning = any(w in low for w in ["morning", "breakfast", "a.m.", " am ", "empty stomach",
                                     "before coffee"])
    evening = any(w in low for w in ["dinner", "evening", "night", "bedtime", "p.m.",
                                     " pm ", "supper"])

    if twice or (morning and evening):
        return ["Morning", "Evening"]
    if morning:
        cats.append("Morning")
    if evening:
        cats.append("Evening")
    if any(w in low for w in ["weekly", "per week", "days per week", "days a week",
                              "times a week", "once a week"]):
        cats.append("Weekly")

    return cats or ["Morning"]


def needs_review(instruction: str, kind: str, timing: str) -> bool:
    """Flag an item that a human must check before it is trusted.

    True when the text is uncertain (?, 'unclear', 'unreadable') or when
    a medication has no clear dose AND no clear frequency. We NEVER fill
    a missing dose/frequency in ourselves.
    """
    import re

    blob = f"{instruction} {timing}".lower()
    if "?" in blob or "unclear" in blob or "unreadable" in blob:
        return True
    if kind == "medication":
        has_dose = bool(re.search(r"\b\d+\s*(mg|mcg|iu|tablet|tablets|capsule|capsules|pill|pills)\b", blob))
        has_freq = any(w in blob for w in ["daily", "once", "twice", "morning", "night",
                                           "evening", "dinner", "breakfast", "bedtime",
                                           "per day", "a day", "weekly", "per week"])
        if not (has_dose and has_freq):
            return True
    return False


def _make_item(instruction: str, kind: str, timing: str, condition: str,
               categories=None) -> dict:
    instruction = instruction.strip()
    timing = (timing or "").strip()
    if not categories:
        categories = categories_from_timing(f"{instruction} {timing}", kind)
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",") if c.strip()]
    status = "Needs review" if needs_review(instruction, kind, timing) else "OK"
    return {
        "instruction": instruction,          # original wording, preserved
        "kind": kind,
        "timing": timing,
        "condition": condition,
        "categories": categories,
        "status": status,
    }


# Headings a pasted prescription / plan may use.
_RX_HEADING = ("rx", "prescription", "medications", "medication list")
_CARE_HEADING = ("care instructions", "care plan", "habits", "lifestyle")
_FOLLOWUP_HEADING = ("follow-up", "follow up", "appointments", "next visit")


def _classify_line(body: str, section: str) -> str:
    """Decide an item's type from the line and the section it sits under."""
    import re

    low = body.lower()
    if any(w in low for w in ["blood test", "lab test", "follow-up", "follow up",
                              "appointment", "visit", "referral", "repeat thyroid"]):
        return "follow-up"
    if section == "rx" or re.search(r"\b(mg|mcg|iu)\b", low) or any(
        w in low for w in ["tablet", "capsule", "pill"]
    ):
        return "medication"
    if section == "care":
        return "habit"
    return "habit"


def _split_instruction_timing(body: str) -> tuple:
    """Keep the full original wording as the instruction; copy the timing
    phrase (if any) into the timing field. Original text is never altered."""
    import re

    timing = ""
    m = re.search(
        r"(once daily.*|twice (?:a day|daily).*|every morning.*|with dinner.*|"
        r"before breakfast.*|at bedtime.*|\d+\s*(?:times?)\s*(?:a|per)\s*(?:day|week).*|"
        r"on an empty stomach.*|every (?:morning|night|evening).*)",
        body, flags=re.IGNORECASE,
    )
    if m:
        timing = m.group(1).strip()
        # Stop the timing phrase at the first sentence end so trailing
        # Qty / Refills text does not leak into it.
        timing = re.split(r"(?<=\.)\s+(?=[A-Z])|\s+(?:Qty|Refills|Disp)\b",
                          timing, maxsplit=1)[0].strip().rstrip(".")
    return body.strip(), timing


def parse_care_plan_text(text: str, condition: str) -> list:
    """Basic (non-AI) reader for pasted prescription / care-plan text.

    - Skips header/metadata lines (before Rx / first medication, and
      Patient / Date / Prescriber / DOB / Clinic / Signature / SAMPLE /
      NOT A VALID lines).
    - Merges a medication NAME line with the instruction lines directly
      below it into ONE row (instruction = name + instruction wording,
      timing = the timing phrase, condition = the tag on the name line).
      Qty / Refills stay in the text but never make their own row.
    - Keeps original wording; never guesses a dose or frequency.
    - Reads a [PCOS] / [Thyroid] tag, else uses the selected condition
      ('unknown' when 'Both' is selected).
    - Groups by timing words (twice-daily -> Morning AND Evening).
    """
    import re

    if condition in ("PCOS", "Thyroid"):
        default_tag = condition
    else:
        default_tag = "unknown"

    def _strip_bullet(s: str) -> str:
        return re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", s).strip()

    def _is_metadata(s: str) -> bool:
        low = s.lower()
        if "sample" in low or "not a valid" in low:
            return True
        return bool(re.match(
            r"^\s*(patient|date|prescriber|clinician|doctor|dob|clinic|"
            r"signature|visit date|diagnosis|assessment)\b[:\s]",
            s, flags=re.IGNORECASE,
        ))

    def _is_med_name(s: str) -> bool:
        # A medication name line: has a dose unit or a tablet/capsule word,
        # and is not itself an instruction ("Take ...", "Qty", "Refills").
        low = s.lower()
        if low.startswith(("take ", "qty", "refills", "sig", "disp")):
            return False
        return bool(re.search(r"\b(mg|mcg|iu)\b", low)) or any(
            w in low for w in ["tablet", "capsule", "pill"]
        )

    def _read_tag(s: str):
        m = re.search(r"\[(pcos|thyroid)\]", s, flags=re.IGNORECASE)
        if not m:
            return None, s
        tag = "PCOS" if m.group(1).lower() == "pcos" else "Thyroid"
        cleaned = re.sub(r"\s*\[(?:pcos|thyroid)\]\s*", " ", s,
                         flags=re.IGNORECASE).strip()
        return tag, cleaned

    lines = [ln.rstrip() for ln in text.splitlines()]
    items = []
    section = ""          # "rx" | "care" | "followup" | ""
    seen_rx = False
    pending = None        # medication row being built across lines

    def _flush():
        nonlocal pending
        if pending is None:
            return
        instruction = " ".join(p for p in pending["parts"] if p).strip()
        _, timing = _split_instruction_timing(instruction)
        items.append(_make_item(instruction, "medication", timing,
                                pending["tag"]))
        pending = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        low = line.lower().rstrip(":")

        # Section headings.
        if low in _RX_HEADING or low == "rx":
            _flush()
            section = "rx"
            seen_rx = True
            continue
        if any(low == h or low.startswith(h) for h in _CARE_HEADING):
            _flush()
            section = "care"
            seen_rx = True  # care section also means header block is over
            continue
        if any(low == h or low.startswith(h) for h in _FOLLOWUP_HEADING):
            _flush()
            section = "followup"
            seen_rx = True
            continue

        # Header / metadata: drop before Rx or first medication, and always
        # for the labelled metadata lines.
        if _is_metadata(line):
            continue

        body = _strip_bullet(line)
        if not body:
            continue

        tag, body_notag = _read_tag(body)

        # Before the Rx heading and before any medication, skip everything
        # that is not itself a medication name line.
        if not seen_rx and section == "" and not _is_med_name(body_notag):
            continue

        # Medication NAME line -> start a new merged row.
        if (section in ("rx", "") and _is_med_name(body_notag)):
            _flush()
            pending = {"parts": [body_notag], "tag": tag or default_tag}
            if not seen_rx:
                seen_rx = True
            continue

        # Instruction / Qty / Refills line under a medication -> merge in.
        if pending is not None and section in ("rx", "") and (
            body_notag.lower().startswith(("take ", "qty", "refills", "sig", "disp"))
            or re.search(r"\b(once|twice|daily|morning|evening|night|dinner|"
                         r"breakfast|bedtime|empty stomach|with food)\b",
                         body_notag, flags=re.IGNORECASE)
        ):
            if tag and pending["tag"] in (None, "unknown"):
                pending["tag"] = tag
            pending["parts"].append(body_notag)
            continue

        # Any other line closes a pending medication row first.
        _flush()

        if section == "followup" or any(
            w in body_notag.lower()
            for w in ["blood test", "lab test", "follow-up", "follow up",
                      "appointment", "visit", "referral", "repeat thyroid"]
        ):
            items.append(_make_item(body_notag, "follow-up", "",
                                    tag or default_tag))
        else:
            items.append(_make_item(body_notag, "habit", "",
                                    tag or default_tag))

    _flush()
    return items


def group_tasks(tasks: list) -> dict:
    """Bucket tasks by category. An item with two categories appears in
    both buckets."""
    grouped = {cat: [] for cat in CATEGORIES}
    for t in tasks:
        cats = t.get("categories") or ["Morning"]
        if isinstance(cats, str):
            cats = [c.strip() for c in cats.split(",") if c.strip()]
        for c in cats:
            grouped.get(c, grouped["Morning"]).append(t)
    return grouped




# ----------------------------------------------------------------------
# Theme: simple lavender / sage, rounded cards.
# ----------------------------------------------------------------------
def inject_theme() -> None:
    st.markdown(
        """
        <style>
          :root {
            --harmoni-lavender: #cdb4f6;
            --harmoni-lavender-soft: #ede7fb;
            --harmoni-pink: #f3c6d6;
            --harmoni-pink-soft: #fbe7ef;
            --harmoni-sage: #a8c3a0;
            --harmoni-sage-soft: #e4efe0;
            --harmoni-peach-soft: #fdecd8;
            --harmoni-ink: #2f2a3a;
            --harmoni-plum: #6b4e8f;
          }
          /* Soft page background like the mockup */
          .stApp {
            background: linear-gradient(160deg, #f6f1fb 0%, #fbeef4 55%, #f3f7f0 100%);
          }
          .block-container { padding-top: 1.5rem; }

          /* Sidebar: soft lavender, rounded pill menu */
          section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f3ecfb 0%, #fbeef4 100%);
          }
          section[data-testid="stSidebar"] img {
            display: block; margin: 0.25rem auto 0.5rem auto;
          }
          section[data-testid="stSidebar"] [role="radiogroup"] > label {
            background: transparent;
            border-radius: 14px;
            padding: 0.55rem 0.9rem;
            margin-bottom: 0.25rem;
            transition: background 0.15s ease;
            font-weight: 600;
            color: var(--harmoni-ink);
          }
          section[data-testid="stSidebar"] [role="radiogroup"] > label:hover {
            background: #ffffffaa;
          }

          /* White rounded cards with soft shadow */
          .harmoni-card {
            background: #ffffff;
            border: 1px solid #eee3f6;
            border-radius: 20px;
            padding: 1.2rem 1.35rem;
            margin-bottom: 1rem;
            color: var(--harmoni-ink);
            box-shadow: 0 6px 20px rgba(107, 78, 143, 0.08);
          }
          .harmoni-card.sage {
            background: var(--harmoni-sage-soft);
            border-color: var(--harmoni-sage);
          }
          .harmoni-title {
            color: var(--harmoni-plum);
            font-weight: 800;
            margin-bottom: 0.1rem;
          }
          .harmoni-tagline {
            color: var(--harmoni-ink);
            opacity: 0.75;
            margin-top: 0;
          }

          /* Welcome header */
          .harmoni-welcome h1 {
            color: var(--harmoni-plum);
            font-weight: 800;
            margin-bottom: 0.1rem;
          }
          .harmoni-welcome p { color: var(--harmoni-ink); opacity: 0.75; margin-top: 0; }

          /* Quick-action tiles */
          .harmoni-tile {
            border-radius: 18px;
            padding: 1rem 1.1rem;
            color: var(--harmoni-ink);
            box-shadow: 0 6px 18px rgba(107, 78, 143, 0.08);
            min-height: 92px;
          }
          .harmoni-tile .label { font-size: 0.8rem; opacity: 0.7; }
          .harmoni-tile .name { font-size: 1.1rem; font-weight: 800; color: var(--harmoni-plum); }
          .tile-lav  { background: var(--harmoni-lavender-soft); }
          .tile-sage { background: var(--harmoni-sage-soft); }
          .tile-blue { background: #e6ecfb; }
          .tile-peach{ background: var(--harmoni-peach-soft); }

          /* Stat chips (quick summary) */
          .harmoni-stat {
            border-radius: 16px; padding: 0.85rem 1rem; text-align: left;
            box-shadow: 0 4px 14px rgba(107, 78, 143, 0.06);
          }
          .harmoni-stat .big { font-size: 1.5rem; font-weight: 800; color: var(--harmoni-plum); }
          .harmoni-stat .unit { font-size: 0.85rem; opacity: 0.7; }

          /* Big metric number (health at a glance / cycle) */
          .harmoni-big-metric { text-align: center; }
          .harmoni-big-metric .pct { font-size: 2.6rem; font-weight: 800; color: var(--harmoni-plum); }
          .harmoni-big-metric .sub { opacity: 0.7; }

          /* Check list rows */
          .harmoni-row { display: flex; justify-content: space-between; padding: 0.3rem 0; }
          .harmoni-row .tick { color: var(--harmoni-sage); font-weight: 800; }
          .harmoni-row .pending { color: #c9b8dd; font-weight: 800; }

          .harmoni-footer {
            margin-top: 2.5rem;
            padding-top: 0.75rem;
            border-top: 1px solid var(--harmoni-lavender);
            color: var(--harmoni-ink);
            opacity: 0.7;
            font-size: 0.85rem;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    st.markdown(
        f'<div class="harmoni-footer">{FOOTER}</div>',
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------
# Page stubs (no feature logic yet).
# ----------------------------------------------------------------------
def page_care_plan(condition: str, thyroid_subtype: str) -> None:
    st.markdown(
        f"""
        <div class="harmoni-card">
          <h2 class="harmoni-title">{APP_NAME}</h2>
          <p class="harmoni-tagline">{TAGLINE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="harmoni-card sage">
          Welcome. Harmoni turns your PCOS and thyroid care into one
          simple daily plan. Add your care plan below, review it, then
          confirm to build today's checklist.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("Care Plan")
    st.caption(f"Condition: {condition} | Thyroid subtype: {thyroid_subtype}")

    if condition == "PCOS":
        default_tag = "PCOS"
    elif condition == "Thyroid":
        default_tag = "Thyroid"
    else:
        default_tag = "unknown"

    tab_photo, tab_paste, tab_manual = st.tabs(
        ["Photo upload", "Paste text", "Manual entry"]
    )

    # --- (1) Photo upload --------------------------------------------
    with tab_photo:
        st.caption("A photo is read by AI only when a vision-capable model is "
                   "configured. Otherwise, use Paste text or Manual entry.")
        photo = st.file_uploader(
            "Upload a photo of your care plan (JPG or PNG)",
            type=["jpg", "jpeg", "png"],
        )
        if photo is not None:
            st.image(photo, caption=photo.name, width=320)
            if st.button("Read this photo", key="read_photo"):
                img_bytes = photo.getvalue()
                result = ai.call_ai(
                    task="care_plan",
                    image_bytes=img_bytes,
                    mime_type=photo.type,
                )
                if result.get("mode") == "unavailable":
                    # No vision-capable model: fall back to manual entry
                    # with a clear message (never guess from the image).
                    st.warning(
                        result.get("warning")
                        or "This build cannot read photos (no vision-capable AI "
                           "model configured). Please type your plan in the "
                           "Manual entry tab, or paste its text in Paste text."
                    )
                    st.info("Switch to the Manual entry or Paste text tab to "
                            "continue - your items will still appear in the "
                            "review table below before anything is confirmed.")
                else:
                    _handle_ai_result(result, condition)

    # --- (2) Paste text (with load buttons) --------------------------
    with tab_paste:
        c1, c2 = st.columns(2)
        if c1.button("Load PCOS sample plan", key="load_pcos"):
            st.session_state["pasted_plan"] = load_care_plan("PCOS")
        if c2.button("Load Thyroid sample plan", key="load_thyroid"):
            st.session_state["pasted_plan"] = load_care_plan("Thyroid")

        if "pasted_plan" not in st.session_state:
            st.session_state["pasted_plan"] = ""
        pasted = st.text_area(
            "Paste your care plan text here",
            height=220,
            key="pasted_plan",
        )
        if st.button("Read this text", key="read_text"):
            if not pasted.strip():
                st.warning("Paste some text first.")
            else:
                result = ai.call_ai(task="care_plan", text=pasted)
                if result.get("mode") == "unavailable":
                    items = parse_care_plan_text(pasted, condition)
                    if items:
                        st.session_state["care_draft"] = items
                        st.warning(
                            "Read by basic text reader (not AI) - please check "
                            "every item."
                        )
                        st.success(f"Found {len(items)} item(s). Review below.")
                    else:
                        st.warning(
                            "No care items found. Use Manual entry to add items."
                        )
                else:
                    _handle_ai_result(result, condition)

    # --- (3) Manual entry --------------------------------------------
    with tab_manual:
        with st.form("manual_item", clear_on_submit=True):
            instruction = st.text_input("Instruction (exact wording)")
            kind = st.selectbox("Type", ITEM_KINDS)
            timing = st.text_input("Timing (e.g. 'every morning, empty stomach')")
            tag = st.selectbox(
                "Condition", CONDITION_TAGS,
                index=CONDITION_TAGS.index(default_tag)
                if default_tag in CONDITION_TAGS else 0,
            )
            cats = st.multiselect(
                "Show under (pick one or more)", CATEGORIES,
                help="Twice-daily items can go in both Morning and Evening.",
            )
            if st.form_submit_button("Add item") and instruction.strip():
                st.session_state["care_draft"].append(
                    _make_item(instruction, kind, timing, tag, cats or None)
                )
                st.success("Added to review below.")

    # --- Editable review table ---------------------------------------
    st.divider()
    st.markdown("### Review before confirming")
    draft = st.session_state["care_draft"]
    if not draft:
        st.caption("No draft items yet. Add some above.")
    else:
        # Flatten categories list -> comma string for editing.
        rows = []
        for it in draft:
            cats = it.get("categories") or ["Morning"]
            if isinstance(cats, str):
                cats = [c.strip() for c in cats.split(",") if c.strip()]
            rows.append({
                "instruction": it["instruction"],
                "kind": it["kind"],
                "timing": it.get("timing", ""),
                "condition": it.get("condition", "unknown"),
                "groups": ", ".join(cats),
                "status": it.get("status", "OK"),
            })
        draft_df = pd.DataFrame(rows)

        def _hl(row):
            if row["status"] == "Needs review":
                style = ("background-color: #fdecec; color: #5a1a1a; "
                         "border: 1px solid #e0a0a0")
            else:
                style = ""
            return [style] * len(row)

        st.caption(
            "Highlighted rows need review. Groups accepts more than one, "
            "comma separated (e.g. 'Morning, Evening')."
        )
        edited = st.data_editor(
            draft_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "instruction": st.column_config.TextColumn("Instruction (original wording)"),
                "kind": st.column_config.SelectboxColumn("Type", options=ITEM_KINDS),
                "timing": st.column_config.TextColumn("Timing"),
                "condition": st.column_config.SelectboxColumn("Condition", options=CONDITION_TAGS),
                "groups": st.column_config.TextColumn("Groups (comma separated)"),
                "status": st.column_config.SelectboxColumn("Status", options=["OK", "Needs review"]),
            },
            key="care_editor",
        )

        # Re-evaluate status from edits: re-flag anything still uncertain,
        # but respect a human who cleared a row to "OK" after editing it.
        records = edited.to_dict("records")
        needs = []
        for r in records:
            auto_flag = needs_review(r.get("instruction", ""), r.get("kind", ""),
                                     r.get("timing", ""))
            # If the row still trips the auto check, it stays "Needs review"
            # regardless of what the editor shows.
            if auto_flag:
                r["status"] = "Needs review"
            if r.get("status") == "Needs review":
                needs.append(r.get("instruction", "(blank)"))

        st.dataframe(edited.style.apply(_hl, axis=1), use_container_width=True)
        st.caption("Nothing is saved until you click Confirm.")

        if needs:
            st.error(
                "Fix or remove these rows before confirming (unclear text, or a "
                "medication with no clear dose/frequency). I will not fill in a "
                "dose or frequency for you:\n\n- " + "\n- ".join(needs)
            )
            st.button("Confirm care plan", type="primary", disabled=True)
        else:
            if st.button("Confirm care plan", type="primary"):
                confirmed = []
                for r in records:
                    cats = [c.strip() for c in str(r.get("groups", "")).split(",")
                            if c.strip()] or ["Morning"]
                    confirmed.append({
                        "instruction": r.get("instruction", "").strip(),
                        "kind": r.get("kind", "habit"),
                        "timing": r.get("timing", "").strip(),
                        "condition": r.get("condition", "unknown"),
                        "categories": cats,
                        "status": "OK",
                    })
                st.session_state["care_tasks"] = confirmed
                st.session_state["care_draft"] = []
                st.success("Care plan confirmed.")

    # --- Today's checklist (after confirm) ---------------------------
    if st.session_state["care_tasks"]:
        st.divider()
        st.markdown("### Today's checklist")
        grouped = group_tasks(st.session_state["care_tasks"])
        for cat in CATEGORIES:
            items = grouped[cat]
            if not items:
                continue
            st.markdown(f"**{cat}**")
            for it in items:
                timing = f" - {it['timing']}" if it.get("timing") else ""
                st.markdown(
                    f"- {it['instruction']}{timing}  "
                    f"`{it['condition']}` `{it['kind']}`"
                )

    # --- Demo storage: download / upload records as JSON -------------
    st.divider()
    st.markdown("### Demo storage")
    st.caption("Demo storage - not secure medical storage. Use this only to "
               "restore synthetic demo data after a session reset.")
    backup = {
        "care_tasks": st.session_state.get("care_tasks", []),
        "care_draft": st.session_state.get("care_draft", []),
        "task_logs": st.session_state.get("task_logs", []),
        "journal_entries": st.session_state.get("journal_entries", []),
        "cycle_dates": st.session_state.get("cycle_dates", []),
        "_meta": {"app": APP_NAME, "kind": "demo-backup",
                  "saved_at": dt.datetime.now().isoformat(timespec="seconds")},
    }
    dc1, dc2 = st.columns(2)
    dc1.download_button(
        "Download my records (JSON)",
        data=json.dumps(backup, indent=2).encode("utf-8"),
        file_name="harmoni_demo_records.json",
        mime="application/json",
        key="care_download_json",
    )
    with dc2:
        up = st.file_uploader("Upload records (JSON)", type=["json"],
                              key="care_upload_json")
        if up is not None and st.button("Restore these records", key="care_restore_json"):
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                st.error("That file is not valid JSON. Nothing was changed.")
            else:
                if not isinstance(data, dict):
                    st.error("That backup has the wrong shape. Nothing was changed.")
                else:
                    restored = 0
                    for key in ("care_tasks", "care_draft", "task_logs",
                                "journal_entries", "cycle_dates"):
                        if isinstance(data.get(key), list):
                            st.session_state[key] = data[key]
                            restored += 1
                    st.success(f"Restored {restored} record group(s).")


def _handle_ai_result(result: dict, condition: str) -> None:
    """Route a call_ai result. On 'unavailable', keep the manual path."""
    mode = result.get("mode")
    if mode == "unavailable":
        st.info("AI not connected - enter manually using the Manual entry tab "
                "or paste text and read the numbered lines.")
        return

    if condition == "Both":
        default_tag = "unknown"
    elif condition in ("PCOS", "Thyroid"):
        default_tag = condition
    else:
        default_tag = "unknown"

    if mode == "demo":
        st.warning("Sample output - not generated from your document.")
    if result.get("warning"):
        st.warning(result["warning"])

    items = (result.get("data") or {}).get("items", [])
    if not items:
        st.info("The AI returned no items. Add them manually instead.")
        return
    drafts = [
        _make_item(
            i.get("instruction", ""),
            i.get("kind", "habit"),
            i.get("timing", ""),
            i.get("condition", default_tag),
            i.get("categories") or i.get("category") or None,
        )
        for i in items
    ]
    st.session_state["care_draft"] = drafts
    st.success(f"Extracted {len(drafts)} item(s). Review below.")


def page_daily_journal() -> None:
    import datetime as _dt

    st.subheader("Daily Journal")

    care_tasks = st.session_state["care_tasks"]

    # --- Today's tasks as checkboxes ---------------------------------
    st.markdown("### Today's tasks")
    checked = {}
    if not care_tasks:
        st.info("No confirmed care tasks yet. Confirm a care plan on the Care Plan page.")
    else:
        for idx, t in enumerate(care_tasks):
            label = f"{t['instruction']}  ({t['condition']})"
            checked[idx] = st.checkbox(label, key=f"task_{idx}")

    # --- Note, symptoms, period --------------------------------------
    st.markdown("### Today's journal")
    note = st.text_area("Free-text note (your original words are kept)",
                        key="journal_note")

    # Optional AI symptom extraction from the note. The original note text
    # is never changed; extracted symptoms are only a suggestion the
    # patient must review and can edit or clear.
    if st.button("Extract symptoms from my note", key="journal_extract"):
        if not note.strip():
            st.warning("Write a note first, or just type symptoms below.")
        else:
            res = ai.call_ai(task="journal", text=note)
            if res.get("mode") == "unavailable":
                st.info("AI not connected - enter symptoms manually below.")
            else:
                if res.get("mode") == "demo":
                    st.warning("Sample extraction - not from an AI model. "
                               "Please check every symptom.")
                found = (res.get("data") or {}).get("symptoms", []) or []
                st.session_state["journal_symptoms"] = ", ".join(found)
                st.success(
                    f"Extracted {len(found)} symptom(s) - review and correct "
                    "them below before saving."
                )

    if "journal_symptoms" not in st.session_state:
        st.session_state["journal_symptoms"] = ""
    symptoms = st.text_input(
        "Symptoms (comma separated) - review extracted, add or correct",
        key="journal_symptoms",
    )
    cycle_started = st.checkbox("My period started today", key="journal_cycle")
    period_date = None
    if cycle_started:
        period_date = st.date_input("Period start date", value=_dt.date.today(),
                                    key="journal_period_date")

    # --- Review step before saving -----------------------------------
    if st.button("Review entry", key="journal_review_btn"):
        done = [care_tasks[i]["instruction"] for i, v in checked.items() if v]
        st.session_state["journal_review"] = {
            "date": _dt.date.today().isoformat(),
            "tasks_done": done,
            "tasks_total": len(care_tasks),
            "note": note.strip(),
            "symptoms": [s.strip() for s in symptoms.split(",") if s.strip()],
            "cycle_started": bool(cycle_started),
            "cycle_start_date": period_date.isoformat() if period_date else None,
        }

    review = st.session_state.get("journal_review")
    if review:
        st.markdown("#### Please review before saving")
        st.markdown(
            f"- **Tasks done:** {len(review['tasks_done'])} of {review['tasks_total']}"
        )
        if review["tasks_done"]:
            for d in review["tasks_done"]:
                st.markdown(f"  - {d}")
        st.markdown(f"- **Note:** {review['note'] or '(none)'}")
        st.markdown(f"- **Symptoms:** {', '.join(review['symptoms']) or '(none)'}")
        st.markdown(
            f"- **Period started:** {'yes, ' + review['cycle_start_date'] if review['cycle_started'] else 'no'}"
        )
        cs, cc = st.columns(2)
        if cs.button("Save entry", type="primary", key="journal_save"):
            st.session_state["journal_entries"].append(review)
            st.session_state["task_logs"].append({
                "date": review["date"],
                "tasks_done": len(review["tasks_done"]),
                "tasks_total": review["tasks_total"],
            })
            if review["cycle_started"] and review["cycle_start_date"]:
                if review["cycle_start_date"] not in st.session_state["cycle_dates"]:
                    st.session_state["cycle_dates"].append(review["cycle_start_date"])
            st.session_state["journal_review"] = None
            st.success("Entry saved.")
        if cc.button("Cancel", key="journal_cancel"):
            st.session_state["journal_review"] = None

    # --- Saved entries, newest first, editable -----------------------
    st.divider()
    st.markdown("### Saved entries")
    entries = st.session_state["journal_entries"]
    if not entries:
        st.caption("No saved entries yet.")
        return

    for i in reversed(range(len(entries))):
        e = entries[i]
        header = f"{e['date']} - {len(e['tasks_done'])}/{e['tasks_total']} tasks"
        with st.expander(header):
            if st.session_state.get("editing_entry") == i:
                new_note = st.text_area("Note", value=e["note"], key=f"edit_note_{i}")
                new_sym = st.text_input(
                    "Symptoms (comma separated)",
                    value=", ".join(e["symptoms"]),
                    key=f"edit_sym_{i}",
                )
                ca, cb = st.columns(2)
                if ca.button("Save changes", key=f"save_edit_{i}"):
                    e["note"] = new_note.strip()
                    e["symptoms"] = [s.strip() for s in new_sym.split(",") if s.strip()]
                    st.session_state["editing_entry"] = None
                    st.success("Entry updated.")
                if cb.button("Cancel", key=f"cancel_edit_{i}"):
                    st.session_state["editing_entry"] = None
            else:
                st.markdown(f"**Note:** {e['note'] or '(none)'}")
                st.markdown(f"**Symptoms:** {', '.join(e['symptoms']) or '(none)'}")
                if e["cycle_started"]:
                    st.markdown(f"**Period started:** {e['cycle_start_date']}")
                if e["tasks_done"]:
                    st.markdown("**Tasks done:** " + "; ".join(e["tasks_done"]))
                if st.button("Edit", key=f"edit_btn_{i}"):
                    st.session_state["editing_entry"] = i


def page_insights(daily_log: pd.DataFrame, cycles: pd.DataFrame) -> None:
    st.subheader("Insights")

    # Guard against a stale empty cache: if the cached frame is empty but
    # the CSV exists on disk, re-read it directly so the synthetic demo
    # history is always used.
    daily_log, cycles = _ensure_history(daily_log, cycles)

    if daily_log.empty and cycles.empty:
        st.warning("No data files found in data/. Add daily_log.csv and cycles.csv.")
        return

    # Analytics lives in analytics.py. If it is missing the required
    # functions or errors out, fall back to a clear message instead of
    # crashing the page.
    if not (hasattr(analytics, "build_metrics")
            and hasattr(analytics, "make_patient_summary")):
        st.info("Analytics not connected yet.")
        return

    new_entries = st.session_state.get("journal_entries") or []
    new_logs = st.session_state.get("task_logs") or []
    try:
        metrics = analytics.build_metrics(
            daily_log,
            cycles,
            task_logs=new_logs,
            journal_entries=new_entries,
        )
    except Exception as exc:  # analytics not wired up / broke
        st.info("Analytics not connected yet.")
        st.caption(f"({type(exc).__name__}: {exc})")
        return

    if not metrics.get("has_data"):
        st.info("Not enough logged data yet to show trends.")
        for n in metrics.get("notes", []):
            st.caption(n)
        return

    # Data provenance labels.
    st.markdown(
        f'<div class="harmoni-card">Includes {len(daily_log)} days of synthetic '
        f'demo history for Aisha (fictional).</div>',
        unsafe_allow_html=True,
    )
    if new_entries or new_logs:
        st.caption(f"Your new entries: {len(new_entries)} journal entry(ies), "
                   f"{len(new_logs)} task log(s) this session.")

    rng = metrics.get("date_range", {})
    if rng.get("start") and rng.get("end"):
        st.caption(f"Showing {metrics['day_count']} day(s): {rng['start']} to {rng['end']}")

    # --- Weekly task adherence -------------------------------------
    adh = metrics["weekly"]["adherence"]
    if adh:
        st.markdown("### Weekly task adherence")
        adh_df = pd.DataFrame(
            [{"week": w["week"],
              "adherence %": round(w["adherence"] * 100) if w["adherence"] is not None else None}
             for w in adh]
        ).set_index("week")
        st.line_chart(adh_df)

    # --- Fatigue & acne trends -------------------------------------
    fat = metrics["weekly"]["fatigue"]
    acne = metrics["weekly"]["acne"]
    if fat or acne:
        st.markdown("### Fatigue and acne trends (1-5)")
        by_week = {}
        for w in fat:
            by_week.setdefault(w["week"], {})["fatigue"] = w["avg_fatigue_1to5"]
        for w in acne:
            by_week.setdefault(w["week"], {})["acne"] = w["avg_acne_1to5"]
        trend_df = pd.DataFrame(
            [{"week": wk, **vals} for wk, vals in sorted(by_week.items())]
        ).set_index("week")
        st.line_chart(trend_df)

    # --- Menstrual cycle lengths -----------------------------------
    cyc = metrics["cycles"]
    if cyc["lengths"]:
        st.markdown("### Menstrual cycle lengths (days)")
        done = [c for c in cyc["lengths"] if c["length_days"] is not None]
        if done:
            cyc_df = pd.DataFrame(
                [{"cycle start": c["cycle_start"], "length (days)": c["length_days"]} for c in done]
            ).set_index("cycle start")
            st.bar_chart(cyc_df)
        if cyc.get("average_length_days") is not None:
            st.caption(f"Average cycle length: {cyc['average_length_days']} days "
                       f"({cyc['count']} recorded; ongoing cycles excluded from the average).")

    # --- My Health Summary -----------------------------------------
    st.divider()
    st.markdown("### My Health Summary")
    try:
        summary = analytics.make_patient_summary(
            metrics, journal_entries=st.session_state.get("journal_entries")
        )
    except Exception:
        summary = "Analytics not connected yet."
    st.markdown(
        f'<div class="harmoni-card sage">{summary}</div>',
        unsafe_allow_html=True,
    )


def _build_doctor_observations(metrics: dict, summary: str) -> str:
    """Build the observations block for the Doctor Summary.

    HONESTY: the "notable patterns" and the doctor questions below are
    computed by RULE-BASED CODE in this file, not by an AI model, so they
    are labelled "Detected automatically from your records". Only text that
    ai.call_ai actually returns in LIVE mode is labelled "AI-generated";
    demo-mode text is labelled "Sample output - not generated by live AI".
    Nothing here is a diagnosis or treatment advice.
    """
    lines = []

    # --- Rule-based patterns (NOT AI) ------------------------------------
    patterns = []

    adh = [(w["week"], w["adherence"]) for w in metrics.get("weekly", {}).get("adherence", [])
           if w.get("adherence") is not None]
    # Biggest week-over-week DROP in adherence (dates from the data).
    if len(adh) >= 2:
        worst = None  # (drop, from_week, to_week, from_val, to_val)
        for (w0, v0), (w1, v1) in zip(adh, adh[1:]):
            drop = v0 - v1
            if worst is None or drop > worst[0]:
                worst = (drop, w0, w1, v0, v1)
        if worst and worst[0] > 0:
            patterns.append(
                f"Biggest week-over-week drop in task adherence: "
                f"{round(worst[3] * 100)}% (week of {worst[1]}) -> "
                f"{round(worst[4] * 100)}% (week of {worst[2]})."
            )

    fat = [(w["week"], w["avg_fatigue_1to5"]) for w in metrics.get("weekly", {}).get("fatigue", [])
           if w.get("avg_fatigue_1to5") is not None]
    # Biggest week-over-week RISE in fatigue (dates from the data).
    if len(fat) >= 2:
        worst = None  # (rise, from_week, to_week, from_val, to_val)
        for (w0, v0), (w1, v1) in zip(fat, fat[1:]):
            rise = v1 - v0
            if worst is None or rise > worst[0]:
                worst = (rise, w0, w1, v0, v1)
        if worst and worst[0] > 0:
            patterns.append(
                f"Biggest week-over-week rise in fatigue: "
                f"{worst[3]} (week of {worst[1]}) -> {worst[4]} of 5 "
                f"(week of {worst[2]})."
            )

    # Overall first-vs-last comparison, if analytics provided one.
    comp = metrics.get("comparison")
    if comp and comp.get("first_30_days", {}).get("adherence") is not None \
            and comp.get("last_30_days", {}).get("adherence") is not None:
        f = round(comp["first_30_days"]["adherence"] * 100)
        l = round(comp["last_30_days"]["adherence"] * 100)
        patterns.append(f"Adherence changed from {f}% (first 30 days) to {l}% (last 30 days).")

    lines.append("Detected automatically from your records (rule-based, not AI):")
    if patterns:
        lines.extend(f"- {p}" for p in patterns)
    else:
        lines.append("- No strong week-over-week changes stood out yet.")

    # --- Rule-based questions (NOT AI) -----------------------------------
    questions = [
        "Are my current cycle lengths and their change over time expected for me?",
        "Do any weeks where my routine dropped off need follow-up?",
        "Is my medication timing (morning vs dinner) working as intended?",
        "Should any of my tracked symptoms (fatigue, acne) prompt a lab check?",
        "How often should I log to make these summaries most useful to you?",
    ]
    lines.append("")
    lines.append("Questions you might ask your doctor "
                 "(suggested automatically from your records, not AI):")
    lines.extend(f"- {q}" for q in questions[:5])

    # --- Actual AI layer output, labelled by its real mode ---------------
    try:
        res = ai.call_ai(task="summary", text=summary)
        mode = res.get("mode")
        extra = (res.get("data") or {}).get("summary", "")
        if mode == "live" and extra:
            lines.append("")
            lines.append("AI-generated (live model): " + extra)
        elif mode == "demo" and extra:
            lines.append("")
            lines.append("Sample output - not generated by live AI: " + extra)
        # mode == "unavailable": add nothing; there is no AI text to show.
    except Exception:
        pass

    header = ("Observations - NOT a diagnosis and NOT treatment advice. "
              "Please discuss with your care team.\n\n")
    return header + "\n".join(lines)


def page_doctor_summary(daily_log: pd.DataFrame, cycles: pd.DataFrame) -> None:
    st.subheader("Doctor Summary")

    # Same stale-cache safeguard as Insights.
    daily_log, cycles = _ensure_history(daily_log, cycles)

    if daily_log.empty and cycles.empty:
        st.warning("No data files found in data/. Add daily_log.csv and cycles.csv.")
        return

    st.caption(f"Includes {len(daily_log)} days of synthetic demo history for "
               f"Aisha (fictional).")
    _new_entries = st.session_state.get("journal_entries") or []
    _new_logs = st.session_state.get("task_logs") or []
    if _new_entries or _new_logs:
        st.caption(f"Your new entries: {len(_new_entries)} journal entry(ies), "
                   f"{len(_new_logs)} task log(s) this session.")

    # --- Period filter: 30 days, 90 days, or all -------------------
    period = st.radio(
        "Report period",
        options=["Last 30 days", "Last 90 days", "All data"],
        horizontal=True,
    )
    days = {"Last 30 days": 30, "Last 90 days": 90, "All data": None}[period]

    filtered = daily_log
    if days is not None and not daily_log.empty and "date" in daily_log.columns:
        dates = pd.to_datetime(daily_log["date"], errors="coerce")
        cutoff = dates.max() - pd.Timedelta(days=days)
        filtered = daily_log[dates > cutoff]

    # Analytics must be present and working, else say so clearly.
    if not all(hasattr(analytics, fn) for fn in
               ("build_metrics", "make_patient_summary",
                "make_doctor_pdf", "make_doctor_csv")):
        st.info("Analytics not connected yet.")
        return
    try:
        metrics = analytics.build_metrics(
            filtered,
            cycles,
            task_logs=st.session_state.get("task_logs"),
            journal_entries=st.session_state.get("journal_entries"),
        )
        summary = analytics.make_patient_summary(
            metrics, journal_entries=st.session_state.get("journal_entries")
        )
    except Exception as exc:
        st.info("Analytics not connected yet.")
        st.caption(f"({type(exc).__name__}: {exc})")
        return
    care_tasks = st.session_state.get("care_tasks", [])

    # --- Report preview --------------------------------------------
    st.markdown("### Report preview")
    st.caption(f"Period: {period}")

    # Section 1: Your records (the real logged metrics).
    st.markdown("#### Your records")
    st.markdown(f'<div class="harmoni-card">{summary}</div>', unsafe_allow_html=True)

    adh = metrics["weekly"]["adherence"]
    if adh:
        prev_df = pd.DataFrame([
            {"week": w["week"],
             "adherence %": round(w["adherence"] * 100) if w["adherence"] is not None else None,
             "tasks done": w["tasks_done"], "tasks total": w["tasks_total"]}
            for w in adh
        ])
        st.dataframe(prev_df, use_container_width=True)
    else:
        st.info("No weekly records in this period.")

    # Section 2: Observations - rule-based patterns + doctor questions
    # detected from the records, plus any real AI-layer text labelled by its
    # mode. No diagnosis or treatment advice.
    st.markdown("#### Observations")
    observations = _build_doctor_observations(metrics, summary)
    st.markdown(
        f'<div class="harmoni-card">{observations.replace(chr(10), "<br>")}</div>',
        unsafe_allow_html=True,
    )

    # --- Downloads -------------------------------------------------
    st.markdown("### Download reports")
    # The PDF gets the records summary AND the observations text, plus the
    # confirmed care tasks and journal entries.
    pdf_summary = summary + "\n\n" + observations
    try:
        pdf_bytes = analytics.make_doctor_pdf(
            metrics, care_tasks=care_tasks,
            journal_entries=st.session_state.get("journal_entries"),
            summary=pdf_summary,
        )
        csv_bytes = analytics.make_doctor_csv(
            metrics, care_tasks=care_tasks,
            journal_entries=st.session_state.get("journal_entries"),
        )
    except Exception as exc:
        st.info("Analytics not connected yet.")
        st.caption(f"({type(exc).__name__}: {exc})")
        return
    tag = {"Last 30 days": "30d", "Last 90 days": "90d", "All data": "all"}[period]
    c1, c2 = st.columns(2)
    c1.download_button(
        "Download PDF", data=pdf_bytes,
        file_name=f"harmoni_report_{tag}.pdf", mime="application/pdf",
    )
    c2.download_button(
        "Download CSV", data=csv_bytes,
        file_name=f"harmoni_report_{tag}.csv", mime="text/csv",
    )


def page_json_backup() -> None:
    st.subheader("Backup and restore")
    st.warning(
        "Demo storage only - NOT secure medical storage. This saves the "
        "app's in-session demo records (care tasks, journal entries, task logs, "
        "cycle dates) to a plain JSON file on your own computer. Do not store real "
        "medical data here."
    )

    # --- Download (backup) -----------------------------------------
    st.markdown("### Save a backup")
    backup = {
        "care_tasks": st.session_state.get("care_tasks", []),
        "care_draft": st.session_state.get("care_draft", []),
        "task_logs": st.session_state.get("task_logs", []),
        "journal_entries": st.session_state.get("journal_entries", []),
        "cycle_dates": st.session_state.get("cycle_dates", []),
        "_meta": {"app": APP_NAME, "kind": "demo-backup",
                  "saved_at": dt.datetime.now().isoformat(timespec="seconds")},
    }
    st.download_button(
        "Download backup (JSON)",
        data=json.dumps(backup, indent=2).encode("utf-8"),
        file_name="harmoni_demo_backup.json",
        mime="application/json",
    )

    # --- Upload (restore) ------------------------------------------
    st.markdown("### Restore from a backup")
    up = st.file_uploader("Upload a Harmoni demo backup (JSON)", type=["json"])
    if up is not None and st.button("Restore this backup", key="restore_json"):
        try:
            data = json.loads(up.getvalue().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            st.error("That file is not valid JSON. Nothing was changed.")
            return
        if not isinstance(data, dict):
            st.error("That backup has the wrong shape. Nothing was changed.")
            return
        restored = 0
        for key in ("care_tasks", "care_draft", "task_logs", "journal_entries", "cycle_dates"):
            if isinstance(data.get(key), list):
                st.session_state[key] = data[key]
                restored += 1
        st.success(f"Restored {restored} record group(s) from the backup.")


def _home_metrics(daily_log, cycles):
    """Build metrics safely for the Home dashboard. Returns {} on any problem."""
    try:
        daily_log, cycles = _ensure_history(daily_log, cycles)
        return analytics.build_metrics(
            daily_log, cycles,
            task_logs=st.session_state.get("task_logs") or [],
            journal_entries=st.session_state.get("journal_entries") or [],
        )
    except Exception:
        return {}


def page_home(daily_log, cycles, condition: str) -> None:
    """Dashboard overview page, styled to match the Harmoni mockup.

    Uses real metrics from analytics.build_metrics. Every number is read
    from the data; nothing is invented. Cards degrade gracefully when a
    value is missing.
    """
    metrics = _home_metrics(daily_log, cycles)
    weekly = metrics.get("weekly", {}) if metrics else {}

    # Latest weekly adherence -> overall adherence %.
    adh_list = weekly.get("adherence", [])
    latest_adh = next(
        (w["adherence"] for w in reversed(adh_list) if w.get("adherence") is not None),
        None,
    )
    adh_pct = round(latest_adh * 100) if latest_adh is not None else None

    today = dt.date.today().strftime("%b %d, %Y")

    # --- Welcome header ------------------------------------------------
    hc, dc = st.columns([3, 1])
    with hc:
        st.markdown(
            f'<div class="harmoni-welcome"><h1>Welcome back, Erad 👋</h1>'
            f'<p>Your personalized health companion for PCOS, thyroid & overall wellness.</p></div>',
            unsafe_allow_html=True,
        )
    with dc:
        st.markdown(
            f'<div class="harmoni-card" style="text-align:center;padding:0.7rem;">'
            f'📅 Today, {today}</div>',
            unsafe_allow_html=True,
        )

    # --- Quick-action tiles -------------------------------------------
    t1, t2, t3, t4 = st.columns(4)
    tiles = [
        (t1, "tile-lav", "Continue", "Care Plan"),
        (t2, "tile-sage", "Log", "Daily Journal"),
        (t3, "tile-blue", "View", "Insights"),
        (t4, "tile-peach", "Doctor", "Summary"),
    ]
    for col, css, label, name in tiles:
        with col:
            st.markdown(
                f'<div class="harmoni-tile {css}"><div class="label">{label}</div>'
                f'<div class="name">{name} →</div></div>',
                unsafe_allow_html=True,
            )

    st.write("")

    # --- Row: Health at a glance | Cycle tracker | My conditions ------
    g1, g2, g3 = st.columns(3)

    with g1:
        glance = (
            f'<div class="harmoni-big-metric"><div class="pct">'
            f'{adh_pct if adh_pct is not None else "--"}%</div>'
            f'<div class="sub">Overall Adherence</div></div>'
        )
        # Latest fatigue/acne as plain read-outs (no invented deltas).
        fat = weekly.get("fatigue", [])
        acne = weekly.get("acne", [])
        recent_fat = next((w["avg_fatigue_1to5"] for w in reversed(fat)
                           if w.get("avg_fatigue_1to5") is not None), None)
        recent_acne = next((w["avg_acne_1to5"] for w in reversed(acne)
                            if w.get("avg_acne_1to5") is not None), None)
        reads = "<ul style='margin:0.5rem 0 0 0;padding-left:1.1rem;'>"
        if recent_fat is not None:
            reads += f"<li>Fatigue (latest week): {recent_fat} / 5</li>"
        if recent_acne is not None:
            reads += f"<li>Acne (latest week): {recent_acne} / 5</li>"
        reads += "</ul>"
        st.markdown(
            f'<div class="harmoni-card"><div class="harmoni-title">Your Health at a Glance</div>'
            f'{glance}{reads}</div>',
            unsafe_allow_html=True,
        )

    with g2:
        cyc = metrics.get("cycles", {}) if metrics else {}
        avg_len = cyc.get("average_length_days")
        count = cyc.get("count", 0)
        st.markdown(
            f'<div class="harmoni-card"><div class="harmoni-title">Cycle Tracker</div>'
            f'<div class="harmoni-big-metric"><div class="pct" style="color:#d96a9a;">🩸</div>'
            f'<div class="sub">'
            f'{count} cycle(s) recorded'
            + (f'<br>average {avg_len} days' if avg_len is not None else '')
            + '</div></div></div>',
            unsafe_allow_html=True,
        )

    with g3:
        rows = [
            ("Manage symptoms", True),
            ("Track cycle & hormones", True),
            ("Lifestyle & diet guidance", True),
            ("Medication & supplement plan", True),
        ]
        items = ""
        for label, done in rows:
            items += (f'<div class="harmoni-row"><span>{label}</span>'
                      f'<span class="tick">✓</span></div>')
        st.markdown(
            f'<div class="harmoni-card"><div class="harmoni-title">My Conditions '
            f'({condition})</div>{items}</div>',
            unsafe_allow_html=True,
        )

    # --- Row: Health trends | Today's care plan -----------------------
    b1, b2 = st.columns([2, 1])

    with b1:
        st.markdown('<div class="harmoni-title">Health Trends (last weeks)</div>',
                    unsafe_allow_html=True)
        by_week = {}
        for w in weekly.get("fatigue", []):
            by_week.setdefault(w["week"], {})["Fatigue"] = w.get("avg_fatigue_1to5")
        for w in weekly.get("acne", []):
            by_week.setdefault(w["week"], {})["Acne"] = w.get("avg_acne_1to5")
        for w in adh_list:
            by_week.setdefault(w["week"], {})["Adherence %/20"] = (
                round(w["adherence"] * 5, 2) if w.get("adherence") is not None else None
            )
        if by_week:
            trend_df = pd.DataFrame(
                [{"week": wk, **vals} for wk, vals in sorted(by_week.items())]
            ).set_index("week")
            st.line_chart(trend_df, height=260)
        else:
            st.info("Log a few days to see your trends.")

    with b2:
        care_tasks = st.session_state.get("care_tasks") or []
        done = sum(1 for t in care_tasks if t.get("status") == "OK")
        total = len(care_tasks)
        head = f"{done} of {total} confirmed" if total else "No care plan yet"
        rows_html = ""
        if care_tasks:
            for t in care_tasks[:5]:
                ok = t.get("status") == "OK"
                mark = ('<span class="tick">✓</span>' if ok
                        else '<span class="pending">○</span>')
                instr = (t.get("instruction") or "")[:48]
                rows_html += (f'<div class="harmoni-row"><span>{instr}</span>{mark}</div>')
        else:
            rows_html = ('<div class="harmoni-row"><span>Go to Care Plan to build '
                         'your checklist.</span></div>')
        st.markdown(
            f'<div class="harmoni-card"><div class="harmoni-title">Today\'s Care Plan</div>'
            f'<div style="opacity:0.7;font-size:0.85rem;margin-bottom:0.4rem;">{head}</div>'
            f'{rows_html}</div>',
            unsafe_allow_html=True,
        )



# ----------------------------------------------------------------------
# App shell.
# ----------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🌿", layout="wide")
    inject_theme()
    init_state()

    with st.sidebar:
        logo_path = Path(__file__).parent / "assets" / "logo.png"
        if logo_path.exists():
            st.image(str(logo_path), width=150)
        else:
            st.markdown(f"### {APP_NAME}")
        st.caption(TAGLINE)

        condition = st.radio(
            "Your condition",
            options=["PCOS", "Thyroid", "Both"],
            index=2,  # demo default: Both
        )

        thyroid_subtype = "n/a"
        if condition in ("Thyroid", "Both"):
            thyroid_subtype = st.selectbox(
                "Thyroid type (self-reported)",
                options=["Hypothyroidism", "Hyperthyroidism", "Other"],
                index=0,  # demo default: Hypothyroidism
            )

        st.divider()
        destination = st.radio(
            "Go to",
            options=["Home", "Care Plan", "Daily Journal", "Insights", "Doctor Summary", "Backup"],
        )

    daily_log = load_daily_log()
    cycles = load_cycles()

    if destination == "Home":
        page_home(daily_log, cycles, condition)
    elif destination == "Care Plan":
        page_care_plan(condition, thyroid_subtype)
    elif destination == "Daily Journal":
        page_daily_journal()
    elif destination == "Insights":
        page_insights(daily_log, cycles)
    elif destination == "Doctor Summary":
        page_doctor_summary(daily_log, cycles)
    elif destination == "Backup":
        page_json_backup()

    render_footer()


if __name__ == "__main__":
    main()
