"""Harmoni analytics layer.

Turns logged data into metrics, a patient-facing summary, and
doctor-facing exports (PDF and CSV).

Design rules:
- Only use what is actually in the logs. Never invent trends, lab
  results, or thyroid diagnostics, and make no treatment claims.
- Handle missing, empty, and zero data safely. When there is not
  enough data, say so rather than guessing.
- Comparisons (first-30-days vs last-30-days) appear only when there
  are enough observations in each window.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

import pandas as pd

# How many days a comparison window needs before we trust it.
_MIN_WINDOW_DAYS = 7
# Width of each comparison window, in days.
_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# small, safe helpers
# ---------------------------------------------------------------------------


def _to_bool(value) -> Optional[bool]:
    """Read a loose truthy/falsey cell into a bool, or None if blank."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("", "nan", "none"):
        return None
    if text in ("true", "1", "yes", "y", "t"):
        return True
    if text in ("false", "0", "no", "n", "f"):
        return False
    return None


def _to_number(value) -> Optional[float]:
    """Read a cell into a float, or None if it is blank/not a number."""
    if value is None:
        return None
    try:
        text = str(value).strip()
        if text == "" or text.lower() in ("nan", "none"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    """Divide safely. Return None when the denominator is zero or missing."""
    if denominator is None or denominator == 0:
        return None
    if numerator is None:
        return None
    return numerator / denominator


def _mean(values: list) -> Optional[float]:
    """Average a list of numbers, ignoring blanks. None if nothing is left."""
    clean = [v for v in (_to_number(x) for x in values) if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, places)


def _parse_dates(df: "pd.DataFrame") -> "pd.DataFrame":
    """Return a copy with a parsed, sorted ``_date`` column (bad rows dropped)."""
    out = df.copy()
    if "date" not in out.columns:
        out["_date"] = pd.NaT
        return out
    out["_date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# build_metrics
# ---------------------------------------------------------------------------


def build_metrics(
    daily_log_df,
    cycles_df,
    task_logs: Optional[list] = None,
    journal_entries: Optional[list] = None,
) -> dict:
    """Compute chart-ready metrics from logged data.

    Parameters
    ----------
    daily_log_df, cycles_df:
        DataFrames read from ``data/daily_log.csv`` and ``data/cycles.csv``.
    task_logs:
        Optional. Reserved for future per-task logs; unused today but kept
        in the signature so callers do not break.
    journal_entries:
        Optional list of confirmed journal entries. Each may be a dict with
        ``date`` and ``symptoms`` (string or list) keys. Only confirmed
        entries are summarised; nothing is inferred.

    Returns
    -------
    dict
        A JSON-friendly dict of metrics and chart-ready series. Every value
        is either a real number from the data or ``None`` when the data is
        missing. No trend is invented.
    """
    metrics: dict = {
        "has_data": False,
        "day_count": 0,
        "date_range": {"start": None, "end": None},
        "weekly": {
            "adherence": [],      # [{week, tasks_done, tasks_total, adherence}]
            "fatigue": [],        # [{week, avg_fatigue_1to5}]
            "acne": [],           # [{week, avg_acne_1to5}]
        },
        "cycles": {
            "lengths": [],        # [{cycle_start, length_days}]
            "count": 0,
            "average_length_days": None,
        },
        "comparison": None,       # first-30 vs last-30, or None if not enough data
        "symptom_history": [],    # from confirmed journal entries only
        "notes": [],              # plain-language caveats about the data
    }

    # --- daily log -------------------------------------------------------
    if daily_log_df is None or len(daily_log_df) == 0:
        metrics["notes"].append("No daily log entries yet.")
    else:
        df = _parse_dates(daily_log_df)
        if len(df) == 0:
            metrics["notes"].append("Daily log has no readable dates yet.")
        else:
            metrics["has_data"] = True
            metrics["day_count"] = int(len(df))
            metrics["date_range"] = {
                "start": df["_date"].min().strftime("%Y-%m-%d"),
                "end": df["_date"].max().strftime("%Y-%m-%d"),
            }

            # Group into ISO weeks (Monday-anchored) for chart-ready series.
            df = df.copy()
            df["_week"] = df["_date"].dt.to_period("W").apply(
                lambda p: p.start_time.strftime("%Y-%m-%d")
            )

            for week, chunk in df.groupby("_week", sort=True):
                # weekly task adherence = sum(tasks_done) / sum(tasks_total)
                if "tasks_done" in chunk and "tasks_total" in chunk:
                    done = sum(
                        v for v in (_to_number(x) for x in chunk["tasks_done"])
                        if v is not None
                    )
                    total = sum(
                        v for v in (_to_number(x) for x in chunk["tasks_total"])
                        if v is not None
                    )
                    adherence = _safe_ratio(done, total)
                    metrics["weekly"]["adherence"].append({
                        "week": week,
                        "tasks_done": _round(done, 0),
                        "tasks_total": _round(total, 0),
                        "adherence": _round(adherence, 3),
                    })

                if "fatigue_1to5" in chunk:
                    metrics["weekly"]["fatigue"].append({
                        "week": week,
                        "avg_fatigue_1to5": _round(_mean(list(chunk["fatigue_1to5"]))),
                    })

                if "acne_1to5" in chunk:
                    metrics["weekly"]["acne"].append({
                        "week": week,
                        "avg_acne_1to5": _round(_mean(list(chunk["acne_1to5"]))),
                    })

            # --- first-30 vs last-30 comparison --------------------------
            metrics["comparison"] = _build_comparison(df)

    # --- cycles ----------------------------------------------------------
    if cycles_df is not None and len(cycles_df) > 0:
        lengths = []
        for _, row in cycles_df.iterrows():
            start = row.get("cycle_start") if hasattr(row, "get") else row["cycle_start"]
            length = row.get("length_days") if hasattr(row, "get") else row["length_days"]
            length_val = _to_number(length)
            # An ongoing cycle has no recorded length yet; keep the start,
            # leave length as None, and do not count it in the average.
            lengths.append({
                "cycle_start": str(start).strip() if start is not None else None,
                "length_days": _round(length_val, 1),
            })
        metrics["cycles"]["lengths"] = lengths
        metrics["cycles"]["count"] = len(lengths)
        completed = [x["length_days"] for x in lengths if x["length_days"] is not None]
        if completed:
            metrics["cycles"]["average_length_days"] = _round(
                sum(completed) / len(completed), 1
            )
        else:
            metrics["notes"].append("No completed cycle lengths recorded yet.")
    else:
        metrics["notes"].append("No cycle entries yet.")

    # --- symptom history from confirmed journal entries only -------------
    metrics["symptom_history"] = _symptom_history(journal_entries)

    return metrics


def _build_comparison(df: "pd.DataFrame") -> Optional[dict]:
    """Compare the first 30 days vs the last 30 days of logged data.

    Returns None unless BOTH windows hold at least ``_MIN_WINDOW_DAYS`` rows
    and the two windows do not overlap. Never fabricates a verdict.
    """
    if len(df) < (2 * _MIN_WINDOW_DAYS):
        return None

    start = df["_date"].min()
    end = df["_date"].max()
    first_cut = start + pd.Timedelta(days=_WINDOW_DAYS)
    last_cut = end - pd.Timedelta(days=_WINDOW_DAYS)

    first = df[df["_date"] < first_cut]
    last = df[df["_date"] > last_cut]

    # Windows must not overlap and each must have enough observations.
    if first_cut > last_cut:
        return None
    if len(first) < _MIN_WINDOW_DAYS or len(last) < _MIN_WINDOW_DAYS:
        return None

    def window_stats(chunk):
        done = sum(
            v for v in (_to_number(x) for x in chunk.get("tasks_done", []))
            if v is not None
        )
        total = sum(
            v for v in (_to_number(x) for x in chunk.get("tasks_total", []))
            if v is not None
        )
        return {
            "days": int(len(chunk)),
            "adherence": _round(_safe_ratio(done, total), 3),
            "avg_fatigue_1to5": _round(_mean(list(chunk.get("fatigue_1to5", [])))),
            "avg_acne_1to5": _round(_mean(list(chunk.get("acne_1to5", [])))),
        }

    return {
        "window_days": _WINDOW_DAYS,
        "first_30_days": window_stats(first),
        "last_30_days": window_stats(last),
    }


def _symptom_history(journal_entries: Optional[list]) -> list:
    """Summarise symptoms from confirmed journal entries only.

    Returns a list of ``{symptom, count}`` sorted most-common first. If no
    confirmed entries are provided, returns an empty list (no guessing).
    """
    if not journal_entries:
        return []

    counts: dict = {}
    for entry in journal_entries:
        if not isinstance(entry, dict):
            continue
        if not entry.get("confirmed", True):
            # Only count confirmed entries.
            continue
        raw = entry.get("symptoms")
        if raw is None:
            continue
        if isinstance(raw, str):
            items = [s.strip() for s in raw.replace(",", ";").split(";")]
        elif isinstance(raw, (list, tuple)):
            items = [str(s).strip() for s in raw]
        else:
            continue
        for sym in items:
            if sym:
                key = sym.lower()
                counts[key] = counts.get(key, 0) + 1

    return [
        {"symptom": sym, "count": count}
        for sym, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ---------------------------------------------------------------------------
# make_patient_summary
# ---------------------------------------------------------------------------


def make_patient_summary(metrics: dict, journal_entries: Optional[list] = None) -> str:
    """Build a short, deterministic, plain-language summary of the metrics.

    Describes only what the metrics contain. Makes no treatment claims and
    invents no trends. Returns "not enough data yet" style text when needed.
    """
    if not metrics or not metrics.get("has_data"):
        return "Not enough data yet. Log a few days to see your summary."

    lines: list = []

    days = metrics.get("day_count", 0)
    rng = metrics.get("date_range", {})
    if rng.get("start") and rng.get("end"):
        lines.append(
            f"You have logged {days} day(s), from {rng['start']} to {rng['end']}."
        )
    else:
        lines.append(f"You have logged {days} day(s).")

    # Most recent weekly adherence.
    adherence = metrics.get("weekly", {}).get("adherence", [])
    recent_adh = next(
        (w["adherence"] for w in reversed(adherence) if w.get("adherence") is not None),
        None,
    )
    if recent_adh is not None:
        lines.append(f"Your latest weekly task adherence was {round(recent_adh * 100)}%.")

    # Most recent weekly fatigue / acne averages.
    fatigue = metrics.get("weekly", {}).get("fatigue", [])
    recent_fat = next(
        (w["avg_fatigue_1to5"] for w in reversed(fatigue) if w.get("avg_fatigue_1to5") is not None),
        None,
    )
    if recent_fat is not None:
        lines.append(f"Your latest weekly average fatigue was {recent_fat} out of 5.")

    acne = metrics.get("weekly", {}).get("acne", [])
    recent_acne = next(
        (w["avg_acne_1to5"] for w in reversed(acne) if w.get("avg_acne_1to5") is not None),
        None,
    )
    if recent_acne is not None:
        lines.append(f"Your latest weekly average acne was {recent_acne} out of 5.")

    # Cycles.
    cycles = metrics.get("cycles", {})
    avg_len = cycles.get("average_length_days")
    if cycles.get("count"):
        if avg_len is not None:
            lines.append(
                f"You have {cycles['count']} recorded cycle(s); "
                f"average length {avg_len} days."
            )
        else:
            lines.append(f"You have {cycles['count']} recorded cycle(s).")

    # First-30 vs last-30 comparison, only if present.
    comp = metrics.get("comparison")
    if comp:
        first = comp.get("first_30_days", {})
        last = comp.get("last_30_days", {})
        if first.get("adherence") is not None and last.get("adherence") is not None:
            lines.append(
                "Comparing your first 30 days with your last 30 days, "
                f"task adherence went from {round(first['adherence'] * 100)}% "
                f"to {round(last['adherence'] * 100)}%."
            )

    if len(lines) <= 1:
        return "Not enough data yet. Log a few more days to see your summary."

    return " ".join(lines)


# ---------------------------------------------------------------------------
# doctor exports
# ---------------------------------------------------------------------------


def _records_for_period(metrics: dict) -> list:
    """Flatten the weekly metrics into tabular records for export."""
    weekly = metrics.get("weekly", {}) if metrics else {}
    adherence = {w["week"]: w for w in weekly.get("adherence", [])}
    fatigue = {w["week"]: w for w in weekly.get("fatigue", [])}
    acne = {w["week"]: w for w in weekly.get("acne", [])}

    weeks = sorted(set(adherence) | set(fatigue) | set(acne))
    rows = []
    for week in weeks:
        a = adherence.get(week, {})
        rows.append({
            "week": week,
            "tasks_done": a.get("tasks_done"),
            "tasks_total": a.get("tasks_total"),
            "adherence_pct": (
                round(a["adherence"] * 100) if a.get("adherence") is not None else None
            ),
            "avg_fatigue_1to5": fatigue.get(week, {}).get("avg_fatigue_1to5"),
            "avg_acne_1to5": acne.get(week, {}).get("avg_acne_1to5"),
        })
    return rows


def make_doctor_pdf(
    metrics: dict,
    care_tasks=None,
    journal_entries: Optional[list] = None,
    summary: str = "",
) -> bytes:
    """Build a doctor-facing PDF report as bytes.

    The PDF has a "Your records" section (the real logged metrics) and an
    "AI observations" placeholder section. No treatment claims are made.
    Always returns valid PDF bytes (starting with ``%PDF``), even with no data.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib import colors

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title="Harmoni report",
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Harmoni report", styles["Title"]))
    rng = (metrics or {}).get("date_range", {})
    if rng.get("start") and rng.get("end"):
        story.append(Paragraph(f"Period: {rng['start']} to {rng['end']}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    # --- Your records ----------------------------------------------------
    story.append(Paragraph("Your records", styles["Heading2"]))
    if summary:
        story.append(Paragraph(summary, styles["Normal"]))
        story.append(Spacer(1, 0.15 * inch))

    rows = _records_for_period(metrics or {})
    if rows:
        header = ["Week", "Tasks done", "Tasks total", "Adherence %", "Avg fatigue", "Avg acne"]
        table_data = [header]
        for r in rows:
            table_data.append([
                r["week"],
                "" if r["tasks_done"] is None else str(int(r["tasks_done"])),
                "" if r["tasks_total"] is None else str(int(r["tasks_total"])),
                "" if r["adherence_pct"] is None else str(r["adherence_pct"]),
                "" if r["avg_fatigue_1to5"] is None else str(r["avg_fatigue_1to5"]),
                "" if r["avg_acne_1to5"] is None else str(r["avg_acne_1to5"]),
            ])
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4B2E83")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2EFF7")]),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Not enough data yet.", styles["Normal"]))

    # Cycle lengths.
    cycles = (metrics or {}).get("cycles", {})
    if cycles.get("lengths"):
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Recorded cycles", styles["Heading3"]))
        cyc_data = [["Cycle start", "Length (days)"]]
        for c in cycles["lengths"]:
            cyc_data.append([
                c.get("cycle_start") or "",
                "" if c.get("length_days") is None else str(c["length_days"]),
            ])
        cyc_table = Table(cyc_data, repeatRows=1)
        cyc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4B2E83")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        story.append(cyc_table)

    # --- AI observations placeholder ------------------------------------
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("AI observations", styles["Heading2"]))
    story.append(Paragraph(
        "This section is a placeholder. AI observations are not yet generated. "
        "This report contains only the records you logged and makes no diagnosis "
        "or treatment recommendation.",
        styles["Normal"],
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def make_doctor_csv(
    metrics: dict,
    care_tasks=None,
    journal_entries: Optional[list] = None,
) -> bytes:
    """Build a doctor-facing CSV export of the records as bytes.

    One row per week with the real logged metrics. Returns a header-only CSV
    when there is no data (never raises).
    """
    buffer = io.StringIO()
    fieldnames = [
        "week",
        "tasks_done",
        "tasks_total",
        "adherence_pct",
        "avg_fatigue_1to5",
        "avg_acne_1to5",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in _records_for_period(metrics or {}):
        writer.writerow({
            "week": row["week"],
            "tasks_done": "" if row["tasks_done"] is None else int(row["tasks_done"]),
            "tasks_total": "" if row["tasks_total"] is None else int(row["tasks_total"]),
            "adherence_pct": "" if row["adherence_pct"] is None else row["adherence_pct"],
            "avg_fatigue_1to5": "" if row["avg_fatigue_1to5"] is None else row["avg_fatigue_1to5"],
            "avg_acne_1to5": "" if row["avg_acne_1to5"] is None else row["avg_acne_1to5"],
        })
    return buffer.getvalue().encode("utf-8")
