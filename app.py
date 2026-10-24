"""Harmoni - Two conditions. One plan.

Streamlit entry point. This file wires up the app shell only:
a sidebar with four destinations, a condition picker, a simple
theme, and a footer. Feature logic lives in ai.py and analytics.py
and is intentionally left as stubs for now.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import ai  # noqa: F401  (stub module, wired for later use)
import analytics  # noqa: F401  (stub module, wired for later use)

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
CONDITION_TAGS = ["PCOS", "Thyroid"]


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


def _guess_category(text: str, kind: str) -> str:
    """Best-effort grouping of an instruction into a checklist bucket.

    Keeps the original wording; only picks which group it shows under.
    """
    low = text.lower()
    if kind == "follow-up" or any(
        w in low for w in ["follow-up", "return visit", "blood test", "blood work",
                            "appointment", "referral", "repeat"]
    ):
        return "Upcoming appointments"
    if any(w in low for w in ["week", "weekly", "5 days", "per week"]):
        return "Weekly"
    if any(w in low for w in ["morning", "empty stomach", "before breakfast",
                              "before coffee", "every morning"]):
        return "Morning"
    if any(w in low for w in ["evening", "night", "bedtime", "dinner", "with dinner"]):
        return "Evening"
    return "Morning"


def _make_item(instruction: str, kind: str, timing: str, condition: str,
               category: str = "") -> dict:
    instruction = instruction.strip()
    return {
        "instruction": instruction,          # original wording, preserved
        "kind": kind,
        "timing": timing.strip(),
        "condition": condition,
        "category": category or _guess_category(instruction, kind),
    }


def parse_care_plan_text(text: str, condition: str) -> list:
    """Turn pasted/loaded care-plan text into draft items.

    Splits on numbered plan lines and keeps each line's ORIGINAL wording
    as the instruction. This is the manual fallback used when the AI
    layer is unavailable; it never guesses clinical content.
    """
    import re

    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\s*\d+[.)]\s*(.+)$", line)
        if not m:
            continue
        body = m.group(1).strip()
        low = body.lower()
        if low.startswith("medication") or "medication" in low[:20]:
            kind = "medication"
        elif any(w in low for w in ["follow-up", "return", "blood", "referral", "repeat visit"]):
            kind = "follow-up"
        else:
            kind = "habit"
        items.append(_make_item(body, kind, "", condition))
    return items


def group_tasks(tasks: list) -> dict:
    grouped = {cat: [] for cat in CATEGORIES}
    for t in tasks:
        grouped.get(t.get("category", "Morning"), grouped["Morning"]).append(t)
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
            --harmoni-sage: #a8c3a0;
            --harmoni-sage-soft: #e4efe0;
            --harmoni-ink: #2f2a3a;
          }
          .harmoni-card {
            background: var(--harmoni-lavender-soft);
            border: 1px solid var(--harmoni-lavender);
            border-radius: 16px;
            padding: 1.1rem 1.25rem;
            margin-bottom: 1rem;
            color: var(--harmoni-ink);
          }
          .harmoni-card.sage {
            background: var(--harmoni-sage-soft);
            border-color: var(--harmoni-sage);
          }
          .harmoni-title {
            color: var(--harmoni-ink);
            font-weight: 700;
            margin-bottom: 0.1rem;
          }
          .harmoni-tagline {
            color: var(--harmoni-ink);
            opacity: 0.75;
            margin-top: 0;
          }
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

    default_tag = "Thyroid" if condition == "Thyroid" else "PCOS"

    tab_photo, tab_paste, tab_manual = st.tabs(
        ["Photo upload", "Paste text", "Manual entry"]
    )

    # --- (1) Photo upload --------------------------------------------
    with tab_photo:
        photo = st.file_uploader(
            "Upload a photo of your care plan (JPG or PNG)",
            type=["jpg", "jpeg", "png"],
        )
        if photo is not None:
            st.image(photo, caption=photo.name, width=320)
            if st.button("Read this photo", key="read_photo"):
                img_bytes = photo.getvalue()
                result = ai.call_ai(
                    task="extract_care_plan",
                    image_bytes=img_bytes,
                    mime_type=photo.type,
                )
                _handle_ai_result(result, default_tag)

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
                result = ai.call_ai(task="extract_care_plan", text=pasted)
                if result.get("mode") == "unavailable":
                    st.info("AI not connected - reading the numbered lines manually.")
                    items = parse_care_plan_text(pasted, default_tag)
                    if items:
                        st.session_state["care_draft"] = items
                        st.success(f"Found {len(items)} item(s). Review below.")
                    else:
                        st.warning(
                            "No numbered plan lines found. Use Manual entry to add items."
                        )
                else:
                    _handle_ai_result(result, default_tag)

    # --- (3) Manual entry --------------------------------------------
    with tab_manual:
        with st.form("manual_item", clear_on_submit=True):
            instruction = st.text_input("Instruction (exact wording)")
            kind = st.selectbox("Type", ITEM_KINDS)
            timing = st.text_input("Timing (e.g. 'every morning, empty stomach')")
            tag = st.selectbox("Condition", CONDITION_TAGS,
                               index=CONDITION_TAGS.index(default_tag)
                               if default_tag in CONDITION_TAGS else 0)
            category = st.selectbox("Show under", CATEGORIES)
            if st.form_submit_button("Add item") and instruction.strip():
                st.session_state["care_draft"].append(
                    _make_item(instruction, kind, timing, tag, category)
                )
                st.success("Added to review below.")

    # --- Editable review table ---------------------------------------
    st.divider()
    st.markdown("### Review before confirming")
    draft = st.session_state["care_draft"]
    if not draft:
        st.caption("No draft items yet. Add some above.")
    else:
        draft_df = pd.DataFrame(draft)
        edited = st.data_editor(
            draft_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "instruction": st.column_config.TextColumn("Instruction (original wording)"),
                "kind": st.column_config.SelectboxColumn("Type", options=ITEM_KINDS),
                "timing": st.column_config.TextColumn("Timing"),
                "condition": st.column_config.SelectboxColumn("Condition", options=CONDITION_TAGS),
                "category": st.column_config.SelectboxColumn("Group", options=CATEGORIES),
            },
            key="care_editor",
        )
        st.caption("Nothing is saved until you click Confirm.")
        if st.button("Confirm care plan", type="primary"):
            st.session_state["care_tasks"] = edited.to_dict("records")
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


def _handle_ai_result(result: dict, default_tag: str) -> None:
    """Route a call_ai result. On 'unavailable', keep the manual path."""
    mode = result.get("mode")
    if mode == "unavailable":
        st.info("AI not connected - enter manually using the Manual entry tab "
                "or paste text and read the numbered lines.")
        return
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
            i.get("category", ""),
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
    note = st.text_area("Free-text note", key="journal_note")
    symptoms = st.text_input(
        "Symptoms (comma separated, e.g. fatigue, acne, cramps)",
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

    if daily_log.empty and cycles.empty:
        st.warning("No data files found in data/. Add daily_log.csv and cycles.csv.")
        return

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            f'<div class="harmoni-card">Daily log rows<br><b>{len(daily_log)}</b></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="harmoni-card sage">Cycles recorded<br><b>{len(cycles)}</b></div>',
            unsafe_allow_html=True,
        )

    st.markdown("**Daily log** (first rows)")
    st.dataframe(daily_log.head(10), use_container_width=True)
    st.caption("Columns: " + ", ".join(daily_log.columns))

    st.markdown("**Cycles**")
    st.dataframe(cycles, use_container_width=True)

    st.info("Charts and metrics come next; data is now wired in.")


def page_doctor_summary() -> None:
    st.subheader("Doctor Summary")
    st.info("Doctor Summary content coming soon.")


# ----------------------------------------------------------------------
# App shell.
# ----------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🌿", layout="wide")
    inject_theme()
    init_state()

    with st.sidebar:
        st.markdown(f"### {APP_NAME}")
        st.caption(TAGLINE)

        condition = st.radio(
            "Your condition",
            options=["PCOS", "Thyroid", "Both"],
            index=2,
        )

        thyroid_subtype = "n/a"
        if condition in ("Thyroid", "Both"):
            thyroid_subtype = st.selectbox(
                "Thyroid type (self-reported)",
                options=["hypothyroidism", "hyperthyroidism", "other"],
            )

        st.divider()
        destination = st.radio(
            "Go to",
            options=["Care Plan", "Daily Journal", "Insights", "Doctor Summary"],
        )

    daily_log = load_daily_log()
    cycles = load_cycles()

    if destination == "Care Plan":
        page_care_plan(condition, thyroid_subtype)
    elif destination == "Daily Journal":
        page_daily_journal()
    elif destination == "Insights":
        page_insights(daily_log, cycles)
    elif destination == "Doctor Summary":
        page_doctor_summary()

    render_footer()


if __name__ == "__main__":
    main()
