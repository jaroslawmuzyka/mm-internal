"""
Cache ocen AI (warstwa embedding_podobienstwo - patrz ai_eval.py) w Supabase.

Ten sam Source_URL/Target_URL moze przewijac sie przez wiele biegow (nowy
crawl, poprawka w danych wejsciowych) - bez cache kazdy taki bieg oceniałby te
same pary od nowa przez OpenAI (koszt + czas). Zamiast tego: PRZED wyslaniem
czegokolwiek do OpenAI, app.py pyta ta tabele, czy dana para byla juz kiedys
oceniona - jesli tak, bierze wynik STAMTAD. Tylko pary, ktorych NIE MA jeszcze
w cache, trafiaja do OpenAI - ich wynik jest od razu tez zapisywany z powrotem
(na biezaco, paczka po paczce - patrz ai_eval.on_batch_evaluated), zeby kolejny
bieg mial jeszcze mniej do zrobienia.

Uzywa surowego REST API Supabase (PostgREST) przez `requests` - BEZ dodatkowej
zaleznosci `supabase-py` (ktora w tym prostym przypadku - odczyt/zapis jednej
tabeli po kluczu - jest zbednym ciezarem). Wymaga w Secrets (patrz README,
sekcja Supabase, tam tez SQL do stworzenia tabeli):
    supabase_url = "https://xxxx.supabase.co"
    supabase_key = "..." (service_role key)

NIGDY nie rzuca wyjatku na zewnatrz - brak konfiguracji, zly klucz/URL, brak
sieci, blad Supabase: odczyt daje po prostu pusty cache (wszystko idzie do
OpenAI, jak bez cache), zapis daje komunikat bledu (do pokazania w UI jako
ostrzezenie) - reszta analizy w OBU przypadkach dziala dalej normalnie.

Symetria A<->B: ocena tematycznej spojnosci nie zalezy od kierunku, wiec
zarowno odczyt jak i zapis uzywaja `ai_eval.canonical_pair` (para posortowana
leksykograficznie) zamiast (Source_URL, Target_URL) wprost - dzieki temu
ocena zapisana dla A->B jest znajdowana tez przy pytaniu o B->A w kolejnym
biegu, zamiast oceniac ja ponownie. Wiersze zapisane PRZED wprowadzeniem tej
zmiany (jesli para wtedy trafila do bazy w "nieskanonizowanej" kolejnosci)
nie beda dopasowane przy pierwszym kolejnym biegu - to jednorazowy koszt,
ocena po prostu zostanie zrobiona (i zapisana kanonicznie) jeszcze raz.
"""

from __future__ import annotations

import requests

from ai_eval import canonical_pair

TABLE_NAME = "ai_link_evaluations"
FETCH_BATCH_SIZE = 40   # ile unikalnych Source_URL na 1 zapytanie GET (limit dlugosci URL-a zapytania)
WRITE_BATCH_SIZE = 500  # ile wierszy na 1 zapytanie POST (upsert)


def _headers(api_key: str) -> dict:
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _in_filter(values: list[str]) -> str:
    """PostgREST: kolumna=in.("a","b","c") - cudzyslowy wymagane, bo nasze
    wartosci (URL-e) maja znaki specjalne (?, =, &, /), ktore bez cudzyslowow
    kolidowalyby ze skladnia filtra."""
    escaped = ",".join('"' + v.replace('"', '\\"') + '"' for v in values)
    return f"in.({escaped})"


def fetch_cached_verdicts(
    pairs: list[tuple[str, str]],
    supabase_url: str,
    supabase_key: str,
) -> tuple[dict[tuple[str, str], str], str | None]:
    """
    Zwraca (cache, error). `cache`: dict {canonical_pair(Source_URL, Target_URL):
    Ocena_AI} dla par, ktore juz kiedys zostaly ocenione - klucz jest
    KANONICZNY (patrz ai_eval.canonical_pair), wiec wywolujacy MUSI tez
    kanonizowac przed sprawdzeniem `key in cache` (patrz app.py). `error`:
    komunikat bledu (None = bez problemow). Pusta lista `pairs` albo brak
    konfiguracji (pusty supabase_url/supabase_key) -> pusty cache, brak bledu
    (to normalny, oczekiwany stan gdy Supabase nie jest skonfigurowany).

    Pyta batchami po unikalnym (kanonicznym) source_url (nie po parach -
    PostgREST nie ma wygodnej skladni na "lista dokladnych par", a liczba
    unikalnych source_url jest zwykle duzo mniejsza niz liczba par dzieki
    embedding_top_n), po czym filtruje wynik do dokladnie tych par, o ktore
    pytalismy.
    """
    if not pairs or not supabase_url or not supabase_key:
        return {}, None

    canonical_pairs = {canonical_pair(s, t) for s, t in pairs}
    source_urls = sorted({p[0] for p in canonical_pairs})
    cache: dict[tuple[str, str], str] = {}

    try:
        for i in range(0, len(source_urls), FETCH_BATCH_SIZE):
            chunk = source_urls[i:i + FETCH_BATCH_SIZE]
            resp = requests.get(
                f"{supabase_url.rstrip('/')}/rest/v1/{TABLE_NAME}",
                headers=_headers(supabase_key),
                params={
                    "select": "source_url,target_url,ocena",
                    "source_url": _in_filter(chunk),
                },
                timeout=30,
            )
            resp.raise_for_status()
            for row in resp.json():
                key = (row.get("source_url"), row.get("target_url"))
                if key in canonical_pairs and row.get("ocena"):
                    cache[key] = row["ocena"]
        return cache, None
    except Exception as e:
        return {}, f"Nie udalo sie odczytac cache ocen z Supabase (analiza dziala dalej, po prostu bez cache): {e}"


def upsert_verdicts(
    rows: list[dict],
    supabase_url: str,
    supabase_key: str,
    model: str,
) -> str | None:
    """
    Zapisuje (upsert po kluczu source_url+target_url) nowo ocenione pary do
    Supabase - kazdy wiersz zapisywany jest pod KANONICZNYM kluczem (patrz
    ai_eval.canonical_pair), niezaleznie od tego, w ktorym kierunku byl
    faktycznie kandydatem (Source_URL/Target_URL moga wiec wyladowac w bazie
    zamienione wzgledem oryginalnego wiersza - to celowe, dzieki temu
    fetch_cached_verdicts znajdzie ta sama ocene niezaleznie od kierunku
    zapytania). `rows`: wiersze kandydatow (dicty z kluczami Source_URL,
    Target_URL, Ocena_AI) - TYLKO niepuste Ocena_AI sa zapisywane (blad
    zapytania/pominiete przy przerwaniu nie zasmiecaja cache pustymi
    wartosciami, ktore potem falszywie wygladalyby jak "juz ocenione: brak
    dopasowania"). Zwraca komunikat bledu (None = bez problemow, w tym gdy
    Supabase nie jest skonfigurowany - wtedy po prostu nic sie nie zapisuje).
    """
    if not supabase_url or not supabase_key:
        return None

    payload = []
    seen = set()
    for r in rows:
        if not r.get("Ocena_AI"):
            continue
        key = canonical_pair(r["Source_URL"], r["Target_URL"])
        if key in seen:
            # Oba kierunki (A->B i B->A) tej samej pary w tej samej paczce -
            # patrz ai_eval.evaluate_embedding_candidates (dedup A<->B) -
            # zapisz kanoniczny wiersz tylko raz.
            continue
        seen.add(key)
        payload.append({"source_url": key[0], "target_url": key[1], "ocena": r["Ocena_AI"], "model": model})
    if not payload:
        return None

    try:
        for i in range(0, len(payload), WRITE_BATCH_SIZE):
            chunk = payload[i:i + WRITE_BATCH_SIZE]
            resp = requests.post(
                f"{supabase_url.rstrip('/')}/rest/v1/{TABLE_NAME}",
                headers={**_headers(supabase_key), "Prefer": "resolution=merge-duplicates"},
                params={"on_conflict": "source_url,target_url"},
                json=chunk,
                timeout=30,
            )
            resp.raise_for_status()
        return None
    except Exception as e:
        return f"Nie udalo sie zapisac {len(payload)} ocen do cache Supabase (oceny sa nadal w pliku wynikowym, po prostu nie trafily do cache na przyszlosc): {e}"
