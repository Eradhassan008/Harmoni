"""Harmoni analytics layer (stub).

Turns logged data into metrics, a patient-facing summary, and
doctor-facing exports (PDF and CSV). All functions are stubs for now
and return empty or minimal valid values so the app runs end to end.
"""

from __future__ import annotations

from typing import Optional


def build_metrics(
    daily_log_df,
    cycles_df,
    task_logs: Optional[list] = None,
    journal_entries: Optional[list] = None,
) -> dict:
    """Compute metrics from logged data.

    Returns a dict of metrics. Stub returns an empty dict.
    """
    return {}


def make_patient_summary(metrics: dict, journal_entries: Optional[list]) -> str:
    """Build a short, plain-language summary for the patient.

    Returns a string. Stub returns an empty string.
    """
    return ""


def make_doctor_pdf(
    metrics: dict,
    care_tasks,
    journal_entries: Optional[list],
    summary: str,
) -> bytes:
    """Build a doctor-facing PDF report.

    Returns PDF bytes. Stub returns empty bytes.
    """
    return b""


def make_doctor_csv(
    metrics: dict,
    care_tasks,
    journal_entries: Optional[list],
) -> bytes:
    """Build a doctor-facing CSV export.

    Returns CSV bytes. Stub returns empty bytes.
    """
    return b""
