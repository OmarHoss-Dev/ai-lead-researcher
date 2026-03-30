import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
import streamlit as st


WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# A descriptive UA helps Wikidata/other APIs tolerate automated traffic.
HEADERS = {"User-Agent": "company-hq-search/1.0 (contact: admin@example.com)"}


def _parse_wikidata_date(value: Optional[str]) -> Optional[_dt.date]:
    if not value:
        return None
    # Typical formats:
    # - "2020-01-15T00:00:00Z"
    # - "2020-01-15T00:00:00"
    # - "2020-01-15"
    # - sometimes just a year
    s = str(value).strip()
    if not s:
        return None

    # If it's a bare year, treat it as Jan 1st.
    if re.fullmatch(r"\d{4}", s):
        return _dt.date(int(s), 1, 1)

    # Handle trailing "Z" and normalize fractional seconds.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # fromisoformat can't parse fractional seconds when the timezone is present in some edge cases,
    # so strip fractional seconds if we detect "....+00:00" or similar.
    s = re.sub(r"(\.\d+)([+-]\d\d:\d\d)$", r"\2", s)

    # Try datetime parsing first.
    try:
        if "T" in s:
            dt = _dt.datetime.fromisoformat(s)
            return dt.date()
    except ValueError:
        pass

    # Fallback: first 10 chars look like YYYY-MM-DD
    if len(s) >= 10:
        try:
            return _dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _cut_summary(text: str, max_chars: int = 420, max_sentences: int = 3) -> str:
    t = " ".join(text.split()).strip()
    if len(t) <= max_chars:
        # Still cap by sentences.
        parts = re.split(r"(?<=[.!?])\s+", t)
        return " ".join(parts[:max_sentences]).strip()

    # Cap by sentences then by characters.
    parts = re.split(r"(?<=[.!?])\s+", t)
    out = " ".join(parts[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip()
    return out


def search_wikidata_entities(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": limit,
    }
    r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=25)
    r.raise_for_status()
    data = r.json()
    items = data.get("search", []) or []
    results: List[Dict[str, Any]] = []
    for it in items:
        results.append(
            {
                "id": it.get("id"),
                "label": it.get("label"),
                "description": it.get("description"),
                "match_type": it.get("match", None),
            }
        )
    return results


def fetch_company_details(company_id: str) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """
    Returns:
      - company english label
      - HQ candidate entries (may include time qualifiers)
      - english wikipedia title (or None)
    """
    # We fetch HQ statements (P159) including optional time qualifiers (start/end).
    # For "current headquarters", we later pick the statement that matches today's date.
    sparql = f"""
    SELECT
      ?companyLabel
      ?hqCity
      ?hqCityLabel
      ?countryLabel
      ?start
      ?end
      ?articleTitle
    WHERE {{
      BIND(wd:{company_id} AS ?company)
      ?company rdfs:label ?companyLabel FILTER(LANG(?companyLabel) = "en").

      OPTIONAL {{
        ?company p:P159 ?hqStatement .
        ?hqStatement ps:P159 ?hqCity .
        OPTIONAL {{ ?hqStatement pq:P580 ?start . }}
        OPTIONAL {{ ?hqStatement pq:P582 ?end . }}

        OPTIONAL {{ ?hqCity wdt:P17 ?country . }}
      }}

      OPTIONAL {{
        ?article schema:about ?company ;
                 schema:inLanguage "en" ;
                 schema:isPartOf <https://en.wikipedia.org/> ;
                 schema:name ?articleTitle .
      }}

      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT 50
    """

    r = requests.get(
        WIKIDATA_SPARQL,
        params={"query": sparql, "format": "json"},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    bindings = data.get("results", {}).get("bindings", []) or []

    company_label = bindings[0].get("companyLabel", {}).get("value", "").strip() if bindings else ""

    hq_candidates: List[Dict[str, Any]] = []
    wikipedia_title: Optional[str] = None

    for b in bindings:
        if not wikipedia_title and b.get("articleTitle", {}).get("value"):
            wikipedia_title = b["articleTitle"]["value"]

        # When there is no P159 at all, HQ fields can be missing.
        if not b.get("hqCity", {}).get("value"):
            continue

        hq_candidates.append(
            {
                "hqCity": b.get("hqCity", {}).get("value"),
                "hqCityLabel": b.get("hqCityLabel", {}).get("value"),
                "countryLabel": b.get("countryLabel", {}).get("value"),
                "start": b.get("start", {}).get("value"),
                "end": b.get("end", {}).get("value"),
            }
        )

    return company_label, hq_candidates, wikipedia_title


def choose_current_hq(
    hq_candidates: List[Dict[str, Any]], today: _dt.date
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Pick the best HQ statement for 'today' when start/end time qualifiers exist.
    If no statement matches, fall back to the best-available (most recently started).
    """
    if not hq_candidates:
        return None, "No HQ information found in Wikidata."

    current: List[Tuple[_dt.date, Dict[str, Any]]] = []
    unknown_time: List[Dict[str, Any]] = []

    for c in hq_candidates:
        start = _parse_wikidata_date(c.get("start"))
        end = _parse_wikidata_date(c.get("end"))

        if start is None and end is None:
            unknown_time.append(c)
            continue

        # If start is missing, treat it as -infinity.
        start_ok = True if start is None else start <= today
        # If end is missing, treat it as +infinity.
        end_ok = True if end is None else end >= today

        if start_ok and end_ok:
            # Score by start (None -> very old).
            score = start or _dt.date(1, 1, 1)
            current.append((score, c))

    if current:
        # Latest start wins.
        current.sort(key=lambda x: x[0], reverse=True)
        return current[0][1], "Selected HQ based on time qualifiers (when available)."

    # Fallback: most recent start among timed candidates.
    with_start: List[Tuple[_dt.date, Dict[str, Any]]] = []
    for c in hq_candidates:
        start = _parse_wikidata_date(c.get("start"))
        if start is not None:
            with_start.append((start, c))

    if with_start:
        with_start.sort(key=lambda x: x[0], reverse=True)
        return with_start[0][1], "No HQ matched 'today'; using most recently started HQ."

    # Final fallback: take the first unknown-time entry.
    return unknown_time[0], "No time qualifiers found; using a best-available HQ."


def wikipedia_summary_for_title(title: str) -> Optional[str]:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}"
    r = requests.get(url, headers=HEADERS, timeout=25)
    if r.status_code != 200:
        return None
    data = r.json()
    extract = data.get("extract")
    if not extract:
        return None
    return _cut_summary(extract)


def build_links(company_id: str) -> Dict[str, str]:
    wikidata_url = f"https://www.wikidata.org/wiki/{company_id}"
    # Wikipedia title is discovered later (depends on sitelink).
    return {"wikidata": wikidata_url}


def main() -> None:
    st.set_page_config(page_title="Company HQ & Summary", layout="centered")
    st.title("Company HQ & Business Summary")
    st.caption("Search a company name and get a best-effort business summary + headquarters for today.")

    query = st.text_input("Search company", placeholder="e.g. Microsoft, Samsung, Toyota", key="company_query")
    search_clicked = st.button("Search", type="primary")

    if search_clicked and query.strip():
        with st.spinner("Searching trusted sources..."):
            try:
                candidates = search_wikidata_entities(query.strip())
            except Exception as e:
                st.error(f"Search failed: {e}")
                st.session_state["candidates"] = []
            else:
                st.session_state["candidates"] = candidates or []

    candidates = st.session_state.get("candidates", None)

    if not candidates:
        st.info("Type a company name, then click `Search`.")
        return

    # Let the user pick the best match.
    option_labels = []
    id_by_label: Dict[str, str] = {}
    for c in candidates:
        label = c.get("label") or ""
        desc = c.get("description") or ""
        # Include Wikidata id to avoid collisions when labels/descriptions repeat.
        display = f"{label} [{c.get('id', '').strip()}] — {desc}".strip(" —")
        option_labels.append(display)
        id_by_label[display] = c["id"]

    chosen_label = st.selectbox("Pick the correct company", option_labels, key="chosen_company")
    chosen_id = id_by_label[chosen_label]

    if st.button("Get summary & HQ", type="secondary"):
        with st.spinner("Fetching headquarters and business summary..."):
            today = _dt.date.today()
            try:
                company_label, hq_candidates, wikipedia_title = fetch_company_details(chosen_id)
            except Exception as e:
                st.error(f"Failed to fetch company details: {e}")
                return

        wikidata_url = build_links(chosen_id)["wikidata"]
        hq, hq_note = choose_current_hq(hq_candidates, today=today)

        summary_text: Optional[str] = None
        wikipedia_url: Optional[str] = None
        if wikipedia_title:
            try:
                summary_text = wikipedia_summary_for_title(wikipedia_title)
                wikipedia_url = f"https://en.wikipedia.org/wiki/{quote(wikipedia_title.replace(' ', '_'))}"
            except Exception:
                summary_text = None

        st.subheader(company_label or "Company")

        st.markdown("### Summary")
        bullets: List[str] = []
        if summary_text:
            bullets.append(f"**Business:** {summary_text}")
        else:
            bullets.append("**Business:** (No Wikipedia summary found)")

        st.markdown("### Headquarters")
        if hq:
            country = hq.get("countryLabel") or ""
            city = hq.get("hqCityLabel") or ""
            pretty = f"{city}, {country}".strip(", ")
            bullets.append(
                f"**Headquarters (today):** {pretty}"
                if pretty
                else "**Headquarters (today):** (Not available)"
            )
        else:
            bullets.append("**Headquarters (today):** (Not available)")

        st.write("- " + "\n- ".join(bullets))

        st.markdown("### Sources")
        sources: List[str] = [f"[Wikidata]({wikidata_url})"]
        if wikipedia_url:
            sources.append(f"[Wikipedia]({wikipedia_url})")
        st.write("- " + "\n- ".join(sources))

        st.caption(f"Today’s date used for HQ selection: {today.isoformat()}. {hq_note}")


if __name__ == "__main__":
    main()

