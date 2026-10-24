# Harmoni MVP - Design

## Architecture: three Python modules

Harmoni is a Streamlit app split into three runtime modules with a strict
separation of concerns. Teammates own different modules and edit **only their
own files**.

| Module | Role | Owns |
|---|---|---|
| `app.py` | Streamlit UI and all pages/flows. Calls the shared contracts. | the UI, navigation, session state, draft/confirm flow, rule-based doctor observations |
| `ai.py` | AI adapter. One entry point `call_ai`. Returns a fixed shape, never fabricates clinical content. | provider detection, deterministic demo extraction, honest `mode` |
| `analytics.py` | Metrics + patient/doctor outputs from logged data only. | metric computation, summaries, PDF/CSV exports |

The UI never computes clinical content itself except explicitly rule-based,
clearly-labelled observations; metrics and AI extraction live behind the
contracts below.

## Shared function contracts

### ai.py

```
call_ai(task, text="", image_bytes=None, mime_type=None) -> dict
    task in {"care_plan", "journal", "summary"}
    returns {"mode": "live"|"demo"|"unavailable", "data": {}, "warning": None}
```
- `live`   - a configured provider produced the result.
- `demo`   - deterministic output for a recognised sample fixture; no model used.
- `unavailable` - nothing configured / cannot handle the input (e.g. an image
  with no vision model). The UI must keep the manual path working.
- Never invents medication names, doses, schedules, endpoints, or diagnoses.

### analytics.py

```
build_metrics(daily_log_df, cycles_df, task_logs=None, journal_entries=None) -> dict
    chart-ready metrics; has_data flag; weekly adherence/fatigue/acne series;
    cycles; first-30-vs-last-30 comparison; notes. Numbers only from the data.

make_patient_summary(metrics, journal_entries=None) -> str
    plain-language summary; insufficient-data safeguards; no advice.

make_doctor_pdf(metrics, care_tasks, journal_entries, summary) -> bytes
    PDF report; "Your records" + observations text passed via summary;
    includes care_tasks and journal_entries. Always valid %PDF bytes.

make_doctor_csv(metrics, care_tasks, journal_entries) -> bytes
    one row per week of the real logged metrics.
```

## Data files (`data/`)

- `daily_log.csv` - 150 days of synthetic daily entries. Columns:
  `date, day_of_plan, pcos_med_taken, thyroid_med_taken, thyroid_med_on_time,
  walk_done, sleep_hours, tasks_done, tasks_total, adherence_pct, cycle_started,
  cycle_day, fatigue_1to5, acne_1to5, mood, symptoms, note`.
- `cycles.csv` - 4 cycle starts. Columns: `cycle_start, length_days`
  (the ongoing cycle has no length yet).
- `care_plan_pcos.txt`, `care_plan_thyroid.txt` - synthetic sample care plans
  (fictional patient "Aisha K.").

## UI notes

- Light lavender/sage theme; white rounded cards; purple primary `#674B86`,
  configured in `.streamlit/config.toml`.
- Pages: Home, Care Plan, Daily Journal, Insights, Doctor Summary.
- In-session records (confirmed care tasks, task logs, journal entries, cycle
  dates) live in `st.session_state`; the JSON backup is demo storage only.
