# Company HQ & Business Summary (Streamlit)

Search any company and get:
- **Business summary** (from Wikipedia)
- **Headquarters (today)** (best-effort from Wikidata HQ statements + time qualifiers when available)

## What “Headquarters (today)” means

Wikidata can store multiple headquarters locations over time, often with optional start/end dates.
This app tries to pick the HQ statement that matches your current date.
If Wikidata has no time qualifiers for that company, it falls back to “best available” HQ and tells you via the note at the bottom.

## Run locally

```bash
cd company-hq-search
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the shown local URL.

### Windows note (if `python` isn’t found)

Use the Windows `py` launcher instead:

```powershell
cd "C:\Users\Omar Hossam\company-hq-search"
py -m streamlit run app.py
```

## Hosted online (recommended: Streamlit Community Cloud)

1. Create a GitHub repo from this folder.
2. Go to [Streamlit Community Cloud](https://streamlit.io/cloud).
3. Connect your GitHub account.
4. Create a new app:
   - **App file**: `app.py`
   - **Requirements**: `requirements.txt`
5. Deploy.

## Sources used

- Wikidata SPARQL for company HQ (property `P159`) and time qualifiers.
- Wikipedia REST API for the business summary extract.

