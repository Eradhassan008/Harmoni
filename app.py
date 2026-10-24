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
          simple daily plan. Pick your condition in the sidebar, then
          use the tabs to journal, see insights, and share a summary
          with your doctor.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("Care Plan")
    st.caption(f"Condition: {condition} | Thyroid subtype: {thyroid_subtype}")

    plans = []
    if condition in ("PCOS", "Both"):
        plans.append(("PCOS", load_care_plan("PCOS")))
    if condition in ("Thyroid", "Both"):
        plans.append(("Thyroid", load_care_plan("Thyroid")))

    shown = False
    for label, text in plans:
        if text:
            shown = True
            with st.expander(f"{label} care plan", expanded=True):
                st.text(text)
    if not shown:
        st.info("No care plan file found in data/.")


def page_daily_journal() -> None:
    st.subheader("Daily Journal")
    st.info("Daily Journal content coming soon.")


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
