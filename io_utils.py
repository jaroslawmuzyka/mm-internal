"""
Wczytywanie plikow wejsciowych: listy URL-i (xlsx albo sitemapa XML) oraz
crawl "Internal HTML" (eksport Screaming Frog, xlsx albo csv).

Wszystko oparte o pandas/openpyxl - zaden z parserow nie zaklada konkretnej
nazwy kolumny "na sztywno", tylko probuje rozpoznac najbardziej prawdopodobna
kolumne (Original Url/Address/URL, Status Code, Indexability, H1, Breadcrumb_*).

Kolumna URL: "Original Url" ma PIERWSZENSTWO przed "Address" (patrz
URL_COLUMN_CANDIDATES). W eksportach Screaming Frog z List Mode "Address" bywa
zdekodowana (np. polskie znaki w wartosci parametru filtra: "Bia%C5%82y" ->
"Biały"), podczas gdy "Original Url" zachowuje dokladnie taki zapis (URL-encoded),
w jakim URL zostal podany na wejsciu. Jesli listy Kategorie/Filtry/Marki
zawieraja URL-e w formie zakodowanej (typowe dla sitemap), dopasowanie po
Address (zdekodowanym) po prostu nie trafi - strona zniknie z analizy mimo ze
jest w crawlu. Z tego samego powodu listy Kategorie/Filtry/Marki, jesli
generowane z eksportu Screaming Frog, tez powinny brac kolumne "Original Url".

Kolumna embeddingu (OPCJONALNA, patrz EMBEDDING_COLUMN_CANDIDATES): musi sie
nazywac (dopasowanie nieczule na wielkosc liter/spacje) "Extract embeddings
from page content" - CELOWO tylko ta jedna, konkretna kolumna, zeby nie
wciagac przypadkiem innej kolumny zawierajacej slowo "embedding" o innym
znaczeniu. Wartosc to string typu "0.12, -0.45, ..." (z lub bez nawiasow
klamrowych), parsowany na tuple floatow. Zasila
linking_engine.build_embedding_candidates (dodatkowa warstwa rekomendacji
oparta o podobienstwo tresci). Brak tej kolumny nie przeszkadza w niczym
innym - reszta narzedzia dziala normalnie, po prostu bez tej warstwy.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


URL_COLUMN_CANDIDATES = ["original url", "address", "url", "final url", "page url", "loc"]
STATUS_COLUMN_CANDIDATES = ["status code", "status_code", "statuscode", "http status"]
INDEXABILITY_COLUMN_CANDIDATES = ["indexability"]
H1_COLUMN_PREFIXES = ["h1"]
BREADCRUMB_URL_PREFIX = "breadcrumb_url"
BREADCRUMB_NAME_PREFIX = "breadcrumb_name"
# Wylacznie ta jedna, konkretna kolumna (patrz docstring modulu) - dopasowanie
# czesciowe w _find_column daje tolerancje na wielkosc liter / dodatkowe
# spacje, ale NIE na inna nazwe. Kolumna OPCJONALNA.
EMBEDDING_COLUMN_CANDIDATES = ["extract embeddings from page content"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _trailing_number(col_name: str) -> int:
    m = re.search(r"(\d+)\s*$", col_name)
    return int(m.group(1)) if m else 0


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    norm_map = {_norm(c): c for c in columns}
    for cand in candidates:
        if cand in norm_map:
            return norm_map[cand]
    # dopasowanie czesciowe (np. "Status Code" wewnatrz dluzszej nazwy)
    for c in columns:
        nc = _norm(c)
        for cand in candidates:
            if cand in nc:
                return c
    return None


def _parse_embedding(raw) -> tuple:
    """
    Konwertuje wartosc kolumny embeddingu (np. "[0.123, -0.456, ...]" albo
    "0.123,-0.456,...") na tuple floatow. Zwraca () jesli puste/niepoprawne
    (brak wartosci, blad parsowania, NaN/Inf w ktorejkolwiek liczbie) - taka
    strona po prostu nie bierze udzialu w warstwie embedding_podobienstwo
    (patrz linking_engine.build_embedding_candidates), reszta narzedzia
    dziala normalnie dalej. Musi NIGDY nie rzucic wyjatku - crawl ze Screaming
    Frog moze miec puste/dziwne wartosci w tej kolumnie dla dowolnego wiersza,
    to nie moze wywalic calej analizy.
    """
    try:
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            return ()
        s = str(raw).strip()
        if not s or s.lower() == "nan":
            return ()
        s = s.strip("[]() '\"")
        if not s:
            return ()
        parts = re.split(r"[,;\s]+", s)
        values = tuple(float(p.strip(" '\"")) for p in parts if p.strip(" '\""))
        if not values or not all(math.isfinite(v) for v in values):
            return ()
        return values
    except (ValueError, TypeError, OverflowError):
        return ()


def _read_dataframe(uploaded_file) -> pd.DataFrame:
    name = getattr(uploaded_file, "name", "") or ""
    raw = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw))

    # CSV - sprobuj przecinka, potem srednika
    for sep in [",", ";", "\t"]:
        try:
            df = pd.read_csv(io.BytesIO(raw), sep=sep)
            if df.shape[1] > 1:
                return df
        except Exception:
            continue
    # ostatnia proba - domyslny separator
    return pd.read_csv(io.BytesIO(raw))


# --------------------------------------------------------------------------
# Listy URL-i (Kategorie / Filtry / Marki): xlsx albo sitemapa XML
# --------------------------------------------------------------------------

def _parse_sitemap_bytes(raw: bytes, _depth: int = 0, _max_depth: int = 3) -> set:
    urls = set()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return urls

    # usuniecie namespace'ow dla uproszczenia
    def strip_ns(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    tag = strip_ns(root.tag)

    if tag == "sitemapindex":
        sub_locs = []
        for sitemap_el in root:
            for child in sitemap_el:
                if strip_ns(child.tag) == "loc" and child.text:
                    sub_locs.append(child.text.strip())
        if requests is not None and _depth < _max_depth:
            for loc in sub_locs[:50]:
                try:
                    resp = requests.get(loc, timeout=15)
                    if resp.ok:
                        urls |= _parse_sitemap_bytes(resp.content, _depth + 1, _max_depth)
                except Exception:
                    continue
        return urls

    # zwykla sitemapa <urlset><url><loc>...
    for url_el in root:
        for child in url_el:
            if strip_ns(child.tag) == "loc" and child.text:
                urls.add(child.text.strip())
    return urls


def read_url_list_file(uploaded_file) -> set:
    """Zwraca zbior URL-i z xlsx (dowolna kolumna z adresami) albo sitemapy XML."""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    raw = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    if name.endswith(".xml"):
        return _parse_sitemap_bytes(raw)

    # xlsx/csv - znajdz kolumne z URL-ami
    df = _read_dataframe(uploaded_file)
    columns = [str(c) for c in df.columns]
    url_col = _find_column(columns, URL_COLUMN_CANDIDATES)
    if url_col is None:
        # fallback: pierwsza kolumna, ktorej wiekszosc wartosci wyglada jak URL
        for c in columns:
            sample = df[c].dropna().astype(str).head(20)
            if len(sample) and (sample.str.startswith("http").mean() > 0.5):
                url_col = c
                break
    if url_col is None:
        url_col = columns[0]

    values = df[url_col].dropna().astype(str).str.strip()
    return set(v for v in values if v.startswith("http"))


# --------------------------------------------------------------------------
# Internal HTML (crawl Screaming Frog): Address / Status Code / Indexability
# / H1 / Breadcrumb_URL N / Breadcrumb_Name N
# --------------------------------------------------------------------------

def read_internal_html_file(uploaded_file) -> list[dict]:
    df = _read_dataframe(uploaded_file)
    columns = [str(c) for c in df.columns]

    url_col = _find_column(columns, URL_COLUMN_CANDIDATES)
    status_col = _find_column(columns, STATUS_COLUMN_CANDIDATES)
    indexability_col = _find_column(columns, INDEXABILITY_COLUMN_CANDIDATES)
    h1_col = _find_column(columns, H1_COLUMN_PREFIXES)
    embedding_col = _find_column(columns, EMBEDDING_COLUMN_CANDIDATES)

    bc_url_cols = sorted(
        [c for c in columns if _norm(c).replace(" ", "_").startswith(BREADCRUMB_URL_PREFIX)],
        key=_trailing_number,
    )
    bc_name_cols = sorted(
        [c for c in columns if _norm(c).replace(" ", "_").startswith(BREADCRUMB_NAME_PREFIX)],
        key=_trailing_number,
    )

    if url_col is None:
        raise ValueError(
            "Nie znaleziono kolumny z adresem URL w pliku Internal HTML "
            "(oczekiwano kolumny typu 'Address' albo 'URL')."
        )

    rows = []
    for _, r in df.iterrows():
        url = r.get(url_col)
        if pd.isna(url) or not str(url).strip():
            continue

        status_val = r.get(status_col) if status_col else None
        try:
            status_code = int(status_val) if pd.notna(status_val) else None
        except (ValueError, TypeError):
            status_code = None

        indexability = r.get(indexability_col) if indexability_col else None
        if pd.isna(indexability):
            indexability = None

        h1 = r.get(h1_col) if h1_col else None
        if pd.isna(h1):
            h1 = None

        bc_urls = tuple(
            str(r[c]).strip() for c in bc_url_cols if pd.notna(r.get(c)) and str(r[c]).strip()
        )
        bc_names = tuple(
            str(r[c]).strip() for c in bc_name_cols if pd.notna(r.get(c)) and str(r[c]).strip()
        )

        embedding = _parse_embedding(r.get(embedding_col)) if embedding_col else ()

        rows.append(
            {
                "url": str(url).strip(),
                "status_code": status_code,
                "indexability": indexability,
                "h1": h1,
                "breadcrumb_urls": bc_urls,
                "breadcrumb_names": bc_names,
                "embedding": embedding,
            }
        )
    return rows
