"""
Integracja z OpenAI: ocena trafnosci propozycji z warstwy embedding_podobienstwo
(patrz linking_engine.py) - jedynej warstwy BEZ potwierdzenia strukturalnego
(breadcrumb), stad najbardziej podatnej na przypadkowe, nietrafione dopasowania
(np. "Multicookery" <-> "Zamrazarki" - podobne embeddingowo, ale bez sensu jako
link wewnetrzny).

Dla kazdej pary Source/Target z Rule == embedding_podobienstwo pyta model o
TAK / NIE / MOZE na podstawie Title + H1 obu stron (bez tresci calej strony -
tylko te dwa pola, tanio i szybko). Wynik trafia do kolumny Ocena_AI (patrz
export.py), zaraz obok kolumny Rule.

NIGDY nie rzuca wyjatku na zewnatrz - brak klucza, zly klucz, zly model, limit
zapytan, brak sieci: wszystko konczy sie pustym Ocena_AI dla dotknietych
wierszy + wpisem w liscie bledow zwracanej z evaluate_embedding_candidates,
reszta narzedzia dziala dalej normalnie. Pozostale reguly (nie embedding_podobienstwo)
nigdy nie sa wysylane do OpenAI - nie ma takiej potrzeby, maja juz potwierdzenie
strukturalne (breadcrumb).

Przerywalne w trakcie dzialania: `evaluate_embedding_candidates` przyjmuje
opcjonalny `stop_event` (threading.Event), sprawdzany przed kazda paczka
zapytan - patrz app.py, gdzie ta funkcja jest wywolywana w osobnym watku
(threading.Thread), zeby przycisk "Przerwij" w UI mial szanse zadzialac
(Streamlit nie przetwarza klikniec w trakcie jednego dlugiego, synchronicznego
przebiegu skryptu).

Cache w Supabase (opcjonalny, patrz supabase_cache.py): PRZED wyslaniem
czegokolwiek do OpenAI, app.py sprawdza w Supabase, czy dana para
Source_URL/Target_URL nie byla juz kiedys oceniona - jesli tak, bierze
wynik STAMTAD zamiast pytac model ponownie (oszczednosc czasu/kosztu przy
powtarzanych biegach na tych samych danych). Nowe oceny sa zapisywane do
Supabase NA BIEZACO (patrz `on_batch_evaluated` nizej), nie dopiero po
calosci - przerwanie w trakcie nie traci juz uzyskanych wynikow. ai_eval.py
NIC nie wie o Supabase bezposrednio (tylko o callbacku `on_batch_evaluated`)
- to app.py laczy oba moduly.
"""

from __future__ import annotations

import json
import threading
from typing import Callable

from linking_engine import EMBEDDING_RULE

AI_EVAL_COLUMN = "Ocena_AI"
DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"
DEFAULT_BATCH_SIZE = 30  # ile par per 1 zapytanie do OpenAI - mniej zapytan = szybciej i taniej
VALID_VERDICTS = {"TAK", "NIE", "MOZE"}
# W kolumnie wynikowej piszemy z polskim "Ż" (tak jak zazyczyl sobie user),
# model dostaje instrukcje zwracac ASCII "MOZE" (prosciej/bezpieczniej w JSON).
DISPLAY_VERDICT = {"TAK": "TAK", "NIE": "NIE", "MOZE": "MOŻE"}

SYSTEM_PROMPT = (
    "Jestes ekspertem SEO oceniajacym trafnosc linkowania wewnetrznego w sklepie "
    "internetowym z elektronika i AGD. Dla kazdej pary stron oceniasz, czy strona "
    "Source (skad wychodzi link) i strona Target (dokad prowadzi) sa na tyle "
    "tematycznie spojne, zeby link wewnetrzny mial sens dla uzytkownika sklepu.\n\n"
    "Odpowiadaj:\n"
    "TAK - kategorie/produkty zdecydowanie do siebie pasuja (np. ten sam typ "
    "produktu, ta sama marka tego samego typu produktu, wyrazne pokrewienstwo)\n"
    "NIE - brak sensownego zwiazku tematycznego, link bylby mylacy dla uzytkownika\n"
    "MOZE - niejednoznaczne, wymaga recznej weryfikacji przez czlowieka\n\n"
    "Oceniaj WYLACZNIE na podstawie podanych tytulow i naglowkow H1 - nie zgaduj "
    "tresci, ktorej nie widzisz."
)

USER_PROMPT_PREFIX = (
    "Ocen ponizsze pary stron (source = skad prowadzi link, target = dokad). "
    "Zwroc WYLACZNIE JSON w formacie:\n"
    '{"results": [{"id": <int>, "ocena": "TAK"|"NIE"|"MOZE"}, ...]}\n'
    "Jeden wpis w results na kazda pare z listy ponizej, w dowolnej kolejnosci, "
    "ale kazde \"id\" musi wystapic dokladnie raz.\n\n"
)


def _build_client(api_key: str):
    import openai
    return openai.OpenAI(api_key=api_key)


def _pair_payload(idx: int, source, target) -> dict:
    return {
        "id": idx,
        "source_title": source.title or "",
        "source_h1": source.h1 or "",
        "target_title": target.title or "",
        "target_h1": target.h1 or "",
    }


def _normalize_verdict(raw) -> str:
    v = str(raw or "").strip().upper()
    v = v.replace("MOŻE", "MOZE")  # tolerancja na polskie znaki w odpowiedzi modelu
    return DISPLAY_VERDICT.get(v, "") if v in VALID_VERDICTS else ""


def _call_batch(
    client, model: str, payload: list[dict], system_prompt: str, user_prompt_prefix: str
) -> dict[int, str]:
    user_content = user_prompt_prefix + json.dumps(payload, ensure_ascii=False)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    out = {}
    for item in data.get("results", []):
        try:
            out[int(item["id"])] = _normalize_verdict(item.get("ocena"))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def evaluate_embedding_candidates(
    candidates: list[dict],
    page_by_url: dict,
    api_key: str,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Callable[[int, int], None] | None = None,
    stop_event: threading.Event | None = None,
    results_holder: dict[tuple, str] | None = None,
    on_batch_evaluated: Callable[[list[dict]], None] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt_prefix: str = USER_PROMPT_PREFIX,
) -> list[str]:
    """
    Mutuje kazdy dict w `candidates` IN PLACE: dopisuje `Ocena_AI` (TAK/NIE/MOŻE)
    do wierszy z Rule == embedding_podobienstwo. Wiersze innych regul - bez
    zmian (brak klucza Ocena_AI - w eksporcie wychodzi jako puste pole, tak
    jak np. Podobienstwo dla regul spoza embedding_podobienstwo).

    `page_by_url`: {url: PageRow} - do pobrania Title/H1 obu stron pary.
    `progress(done, total)`: opcjonalny callback do paska postepu w UI.
    `stop_event`: opcjonalny threading.Event - sprawdzany PRZED kazda paczka
    zapytan (nie w trakcie pojedynczego zapytania - przerwanie jest wiec
    granularne co ok. `batch_size` wierszy, nie natychmiastowe). Gdy ustawiony,
    petla konczy sie od razu - wiersze, ktore nie zdazyly byc ocenione, po
    prostu zostaja bez Ocena_AI, tak jak przy bledzie zapytania.

    `results_holder`: opcjonalny dict {(Source_URL, Target_URL): ocena},
    dopisywany OBOK mutacji in-place - niezalezny "plan B" na wypadek, gdyby
    watek w tle i glowny skrypt (po st.rerun/session_state) z jakiegos powodu
    nie dzielily juz tych samych obiektow dict (np. na Streamlit Community
    Cloud - zgloszony przypadek, gdzie podsumowanie w UI mialo poprawne
    oceny, ale plik xlsx wychodzil z pusta kolumna Ocena_AI). app.py uzywa
    tego do jawnego "doklejenia" ocen po kluczu tuz przed budowa plikow,
    zamiast polegac WYLACZNIE na mutacji in-place.

    `on_batch_evaluated(rows)`: opcjonalny callback wywolywany PO kazdej
    ocenionej paczce (rows = wiersze z TEJ paczki, juz z ustawionym Ocena_AI) -
    patrz app.py/supabase_cache.py, gdzie sluzy do zapisu wynikow do cache
    Supabase NA BIEZACO (nie dopiero na koncu), zeby przerwanie w trakcie
    (stop_event) nie tracilo juz opłaconych/wykonanych ocen.

    `system_prompt` / `user_prompt_prefix`: domyslnie stale SYSTEM_PROMPT /
    USER_PROMPT_PREFIX z tego modulu, ale app.py pozwala je podejrzec i
    recznie edytowac w UI (pole tekstowe) - przydatne, gdy w trakcie pracy z
    narzedziem okaze sie, ze trzeba dopisac jakis wyjatek/szczegol do
    instrukcji dla modelu. Usuniecie instrukcji formatu JSON z
    `user_prompt_prefix` NIE powoduje crasha - `_call_batch` przestanie sie
    dac poprawnie sparsowac, co skonczy sie po prostu bledem zapytania dla tej
    paczki (jak przy kazdym innym bledzie API) i pustymi ocenami, nie awaria
    calej analizy.

    Zwraca liste komunikatow bledow (pusta lista = bez problemow). Blad
    pojedynczego zapytania NIE przerywa reszty - dotkniete wiersze zostaja
    po prostu bez oceny (Ocena_AI = "").
    """
    targets = [c for c in candidates if c.get("Rule") == EMBEDDING_RULE]
    if not targets or not api_key:
        return []

    try:
        client = _build_client(api_key)
    except Exception as e:
        return [f"Nie udalo sie zainicjowac klienta OpenAI: {e}"]

    errors: list[str] = []
    done = 0
    total = len(targets)

    for i in range(0, total, batch_size):
        if stop_event is not None and stop_event.is_set():
            break
        chunk = targets[i:i + batch_size]
        payload = []
        rows_by_id = {}
        for j, c in enumerate(chunk):
            source = page_by_url.get(c.get("Source_URL"))
            target = page_by_url.get(c.get("Target_URL"))
            if source is None or target is None:
                continue
            payload.append(_pair_payload(j, source, target))
            rows_by_id[j] = c

        if payload:
            try:
                verdicts = _call_batch(client, model, payload, system_prompt, user_prompt_prefix)
            except Exception as e:
                errors.append(f"Blad zapytania do OpenAI (wiersze {i + 1}-{i + len(chunk)}): {e}")
                verdicts = {}
            for j, c in rows_by_id.items():
                verdict = verdicts.get(j, "")
                c[AI_EVAL_COLUMN] = verdict
                if results_holder is not None:
                    results_holder[(c.get("Source_URL"), c.get("Target_URL"))] = verdict
            if on_batch_evaluated and rows_by_id:
                on_batch_evaluated(list(rows_by_id.values()))

        done += len(chunk)
        if progress:
            progress(done, total)

    return errors
