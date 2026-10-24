# Harmoni

**Two conditions. One plan.**

Harmoni helps women managing PCOS and hypothyroidism turn their care
plans into one daily routine, track progress, and share a clear
summary with their doctors.

This repo is a minimal Streamlit project foundation. Feature logic is
not built yet; `ai.py` and `analytics.py` hold stubs.

## Run it

```
pip install -r requirements.txt
streamlit run app.py
```

## Files

- `app.py` - Streamlit app shell: sidebar with Care Plan, Daily
  Journal, Insights, Doctor Summary; condition picker; theme; footer.
- `ai.py` - AI layer stub (`call_ai`).
- `analytics.py` - metrics and export stubs.
- `requirements.txt` - Python dependencies.
- `data/` - sample CSV data (e.g. `daily_log.csv`, `cycles.csv`).

## Note

Not medical advice. Always follow your care team's guidance.
