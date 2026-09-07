"""
Chmura linkow - generator linkowania wewnetrznego (kategoria -> kategoria +
marka + filtr) na podstawie breadcrumba z crawla.

Wgrywasz 4 pliki (Kategorie / Filtry / Marki / Internal HTML), zaznaczasz
wykluczenia, klikasz "Uruchom analize" i dostajesz dwa pliki xlsx:
  1. do oceny (kandydaci rozbici na 3 zeszyty wg Source_Type - kategorie/
     marki/filtry - plus arkusze pomocnicze i audyt wgranych adresow)
  2. gotowa macierz do wgrania w Contentful (rowniez rozbita na 3 zeszyty wg
     Source_Type - Source_URL + kolumny Link_N)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from linking_engine import build_pages, run_all_rules
from io_utils import read_url_list_file, read_internal_html_file
from export import build_review_workbook, build_contentful_matrix, SOURCE_TYPE_SHEET_NAMES


st.set_page_config(page_title="Chmura linkow - linkowanie wewnetrzne", page_icon="🔗", layout="wide")


def _configured_password() -> str | None:
    """st.secrets rzuca wyjatek (nie zwraca None), jesli w ogole nie ma pliku
    secrets.toml - trzeba to zlapac, zeby pokazac czytelny blad zamiast stack trace."""
    try:
        return st.secrets.get("password")
    except Exception:
        return None


def _configured_anchor_suffix() -> str:
    """
    Domyslna wartosc pola 'Sufiks do usuniecia z Anchor' - opcjonalnie z Secrets
    (klucz `anchor_suffix_to_strip`), zeby nazwa sklepu/marki nie musiala byc
    zaszyta na sztywno w kodzie (ktory jest w publicznym repo). Mozna tez po
    prostu wpisac ja recznie w polu w UI za kazdym razem - to jest tylko
    wygodny domyslny prefill.
    """
    try:
        return st.secrets.get("anchor_suffix_to_strip") or ""
    except Exception:
        return ""


def _password_entered() -> None:
    if st.session_state.get("password_input") == _configured_password():
        st.session_state["password_correct"] = True
        del st.session_state["password_input"]  # nie trzymaj hasla w pamieci sesji
    else:
        st.session_state["password_correct"] = False


def check_password() -> bool:
    """Bramka dostepu - haslo ustawiane w Secrets (klucz `password`), patrz README."""
    if st.session_state.get("password_correct"):
        return True

    if not _configured_password():
        st.error(
            "Brak skonfigurowanego hasla dostepu. W Streamlit Community Cloud: "
            "**Settings -> Secrets** i dodaj:\n\n```\npassword = \"twoje-haslo\"\n```"
        )
        st.stop()

    st.text_input("🔒 Haslo dostepu", type="password", key="password_input", on_change=_password_entered)
    if st.session_state.get("password_correct") is False:
        st.error("Nieprawidlowe haslo.")
    return False


if not check_password():
    st.stop()

st.title("🔗 Chmura linkow - generator linkowania wewnetrznego")
st.caption(
    "Kategoria ↔ kategoria + marka + filtr, na podstawie breadcrumba z crawla - linkowanie dziala "
    "w obie strony (marka i filtr tez linkuja do innych stron, nie tylko sa targetem)."
)

with st.expander("Jak to dziala? (kliknij, zeby rozwinac)", expanded=False):
    st.markdown(
        """
1. **Kategorie / Filtry / Marki** - listy URL-i (xlsx z kolumna adresu, albo sitemapa XML).
   Typ kazdej strony jest ustalany po tym, na ktorej liscie sie znajduje - nie po wzorcu URL-a,
   wiec narzedzie dziala na dowolnej stronie e-commerce. Wyjatek: strona zagniezdzona pod URL-em
   z listy Marki (dowolny przodek w breadcrumbie) dziedziczy typ "brand", nawet jesli sama nie
   jest wprost na tej liscie (typowy przypadek: podstrona marka+kategoria).
2. **Internal HTML** - eksport crawlera (np. Screaming Frog) z kolumnami: adres, status code,
   indexability, H1 oraz breadcrumb wyciagniety Custom Extraction (`Breadcrumb_URL 1..N`,
   `Breadcrumb_Name 1..N`, gdzie ostatnia kolumna Breadcrumb_Name to nazwa biezacej strony).
   Szczegolowa instrukcja konfiguracji: zakladka **"Konfiguracja Screaming Frog"** ponizej.
   Kolumna adresu: jesli plik ma zarowno `Original Url` jak i `Address` (typowe dla eksportu
   List Mode), bierzemy `Original Url` - `Address` bywa zdekodowana (np. polskie znaki w
   parametrze filtra), co psuje dopasowanie do list Kategorie/Filtry/Marki. Opcjonalnie: kolumna
   `Extract embeddings from page content` zasila dodatkowa warstwe rekomendacji
   embedding_podobienstwo (patrz nizej) - jej brak niczego nie psuje, narzedzie dziala normalnie
   bez tej warstwy.
3. Zaznaczasz wykluczenia (3xx / 4xx / noindex), ustawiasz suwaki i klikasz **Uruchom analize**.
4. Dostajesz dwa pliki: pelna liste kandydatow do oceny (rozbita na **3 zeszyty wg tego, KTO
   linkuje**: `Kandydaci do link. (kategorie)`, `(marki)`, `(filtry)` - patrz sekcja Reguly
   ponizej) oraz gotowa macierz do wgrania w Contentful - **tak samo rozbita na 3 zeszyty**
   (`Matryca Contentful (kategorie)` / `(marki)` / `(filtry)`). Komorka `Target_URL` / `Link_N`
   jest **kolorowana wg pewnosci dopasowania** (patrz "Skala pewnosci" na koncu tej listy) - w
   obu plikach. Plik "do oceny" ma tez arkusz `Wszystkie_adresy_wejsciowe` - podglad wszystkich
   URL-i wgranych w listach Kategorie/Filtry/Marki wraz z ich Status Code / Indexability
   z Internal HTML (URL / Typ / Zrodlo-nazwa pliku / Status Code / Indexability), z czerwonym
   podswietleniem status <> 200 i indexability <> "Indexable" - do szybkiego sprawdzenia, co
   odpadlo z analizy i dlaczego.

### Reguly linkowania (dokladnie w tej kolejnosci trafiaja do wynikow)

1. **`kategoria_podrzedna`** - kazdy przodek kategorii z breadcrumba dostaje link do niej,
   na kazdym poziomie ponizej (nie tylko bezposrednie dzieci) - ale tylko do limitu
   **"Maksymalna roznica poziomow"** (suwak nizej, domyslnie 1 = tylko bezposrednie dzieci).
   Kandydaci ponizej limitu nie znikaja - trafiaja do arkusza `Pominiete_zbyt_glebokie`.
2. **`filtr_wlasny`** - kazda kategoria dostaje link do WLASNEJ strony z filtrem/fasetem
   (dopasowanej po identycznym breadcrumbie - parametr URL nie zmienia breadcrumba w DOM).
3. **`marka_orientacyjna_1seg`** (**`_UWAGA_KOLIZJA`** jesli nazwa koliduje w >1 dziale) -
   kategoria dostaje link do marki, jesli ostatni segment nazwy w breadcrumbie jest identyczny
   po obu stronach (np. kategoria ".../Golenie" -> marka ".../Philips/Golenie"). Kategorie/marki
   z w pelni generycznym ostatnim segmentem (np. "Akcesoria") sa z tego calkowicie wykluczone -
   trafiaja do arkusza `Marka_wykluczona_generyczna`.
4. **`kategoria_tego_samego_poziomu`** - kategorie-siostry pod tym samym rodzicem w breadcrumbie
   linkuja do siebie nawzajem. Pomijane, gdy rodzicem jest kategoria L1 (szeroki, "koszykowy"
   dzial) - jego dzieci trafiaja zamiast tego do `L2_pod_L1_bez_siostr` do recznej selekcji.
5. **`filtr_tego_samego_poziomu`** - jak wyzej, ale target to strona z filtrem siostrzanej
   kategorii.
6. **Pozostale reguly** (bez ustalonego priorytetu miedzy soba):
   - `marka_precyzyjna_2seg` - dopasowanie kategorii do marki po **2** ostatnich segmentach
     nazwy w breadcrumbie (bardzo male ryzyko falszywych trafien).
   - `filtr_podrzedny` - jak `kategoria_podrzedna`, ale target to strona z filtrem (ten sam
     limit "Maksymalna roznica poziomow").
   - `kategoria_nadrzedna` / `filtr_nadrzedny` - kazda kategoria na poziomie **L5 lub glebiej**
     ZAWSZE linkuje w gore do bezposredniego rodzica ORAZ do wszystkich jego filtrow
     (niezalezne od limitu "Maksymalna roznica poziomow" - to jest link "w gore" o 1 poziom).
7. **`embedding_podobienstwo`** (zawsze na samym koncu) - DODATKOWA warstwa, niezalezna od
   breadcrumba: cosine similarity miedzy embeddingami tresci stron (jesli kolumna jest
   dostepna w Internal HTML), max **10** najbardziej podobnych stron per strona (suwak nizej),
   ale TYLKO pary, ktorych ZADNA z powyzszych regul jeszcze nie zaproponowala - nie duplikuje,
   tylko dokdada. Kolumna `Podobienstwo` (0-1) jest wypelniona tylko dla tych wierszy.

### Linkowanie odwrocone - marka i filtr tez SA zrodlem, nie tylko targetem

Kazda z powyzszych regul kategoria→filtr i kategoria→marka ma OD RAZU wygenerowana
odwrotnosc (dopisek `_odwrotnie` w nazwie reguly), zeby strona marki/filtra tez mogla
linkowac do innych stron:
- `filtr_wlasny_odwrotnie` / `filtr_tego_samego_poziomu_odwrotnie` / `filtr_podrzedny_odwrotnie`
  / `filtr_nadrzedny_odwrotnie` - filtr linkuje z powrotem do kategorii (wlasnej bazowej,
  siostrzanej, przodka, rodzica).
- `marka_precyzyjna_2seg_odwrotnie` / `marka_orientacyjna_1seg_odwrotnie(_UWAGA_KOLIZJA_odwrotnie)`
  - marka linkuje z powrotem do dopasowanej kategorii.
- **`marka_do_filtru`** / **`filtr_do_marki`** - marka i filtr NIE maja bezposredniego
  dopasowania po nazwie/URL (parametry filtra nie sa parsowane), wiec to dopasowanie
  **tranzytywne**: jesli marka pasuje do kategorii C, a C ma WLASNY filtr F, to marka i F
  sa tez ze soba powiazane w obie strony (typowy przypadek: strona marki "Philips" ↔ filtr
  "Czajniki elektryczne marki Philips").

Kazdy z tych kandydatow trafia do zeszytu zgodnego z Source_Type (patrz punkt 4 powyzej) -
np. `filtr_wlasny_odwrotnie` (Source=filtr) laduje w `Kandydaci do link. (filtry)`.

**Skala pewnosci (kolor komorki Target_URL / Link_N):**
🟢 ciemna zielen = najpewniejsze (`kategoria_podrzedna`, `filtr_wlasny`, `kategoria_nadrzedna`,
`filtr_nadrzedny`, `marka_precyzyjna_2seg`, `marka_orientacyjna_1seg` - dokladne dopasowanie
strukturalne, w tym najsilniejsze dopasowania marki) →
🟩 jasna zielen = pewne (`kategoria_tego_samego_poziomu` - siostry po wspolnym rodzicu) →
🟨 zolty = pewne, mniej bezposrednie (`filtr_podrzedny`, `marka_do_filtru`, `filtr_do_marki`) →
🟧 pomaranczowy = wymaga uwagi (`marka_orientacyjna_1seg_UWAGA_KOLIZJA` - jawnie oznaczona kolizja
nazw, `filtr_tego_samego_poziomu` - siostry-filtry) →
🟥 czerwony = najmniej pewne (`embedding_podobienstwo` - czysto statystyczne podobienstwo tresci,
warto zweryfikowac recznie).

Kazda regula `_odwrotnie` ma ten sam kolor co jej oryginal (to ten sam fakt, tylko widziany z
drugiej strony). Jesli wiersz laczy >1 regule (np. `marka_precyzyjna_2seg + marka_orientacyjna_1seg`),
liczy sie NAJLEPSZY (najpewniejszy) kolor wsrod nich.

**Co trafia do osobnych arkuszy zamiast do zeszytow Kandydaci do link. (...):**
- `L1_do_uzupelnienia` - kategorie L1 (najwyzszy poziom) NIGDY nie wystepuja jako Source_URL w
  zadnym z 3 zeszytow kandydatow - wszystkie ich automatyczne propozycje (z kazdej reguly,
  lacznie z embedding_podobienstwo) trafiaja tutaj, do jednego miejsca recznej weryfikacji.
- `Pominiete_zbyt_glebokie` - kandydaci kategoria_podrzedna/filtr_podrzedny odcieci limitem
  roznicy poziomow (nic nie ginie, tylko wymaga recznej decyzji, jesli chcesz je jednak dodac).
- `Marka_wykluczona_generyczna` - kategorie z generycznym ostatnim segmentem breadcrumba (np.
  "Akcesoria"), ktore nie dostaly automatycznej propozycji marki 1-segmentowej.
- `Wszystkie_adresy_wejsciowe` - audyt: kazdy URL wgrany w listach Kategorie/Filtry/Marki
  (kolumny URL / Typ / Zrodlo - nazwa wgranego pliku / Status Code / Indexability z
  Internal HTML), niezaleznie czy ostatecznie zostal dopasowany do crawla - czerwone
  podswietlenie na status <> 200 i indexability <> "Indexable". Do szybkiego sprawdzenia,
  co dokladnie zostalo przeanalizowane i co odpadlo (i dlaczego).
        """
    )

with st.expander("🕷️ Konfiguracja Screaming Frog (kliknij, zeby rozwinac)", expanded=False):
    st.markdown(
        """
Jak skonfigurowac crawl w Screaming Frog, zeby eksport "Internal HTML" mial wszystkie
kolumny, ktorych to narzedzie potrzebuje.

**1. Tryb crawla:**
- **Spider (zwykly crawl)** - jesli chcesz, zeby Screaming Frog sam odkryl wszystkie strony
  przechodzac po linkach. Prostsze, ale fasety/filtry czesto sa zablokowane w robots.txt albo
  nie majace zwyklych linkow `<a href>` (JS), wiec spider moze ich nie znalezc.
- **List Mode** (Mode -> List) - wgrywasz gotowa liste URL-i (np. z sitemapy albo eksportu ze
  sklepu) i Screaming Frog odwiedza TYLKO te adresy. Polecane, jesli masz pelna liste kategorii/
  filtrow/marek z innego zrodla - gwarantuje, ze nic nie zostanie pominiete przez crawl budget
  albo brak wewnetrznych linkow. W tym trybie w eksporcie pojawia sie kolumna **`Original Url`**
  (dokladny URL, ktory wgrales) obok `Address` (URL po ewentualnych przekierowaniach/dekodowaniu)
  - to narzedzie samo wybiera `Original Url`, jesli jest dostepna (patrz sekcja 2 powyzej).

**2. Breadcrumb (Custom Extraction):**
`Config -> Custom -> Extraction`. Dodaj **dwa** ekstraktory typu XPath:
- Jeden zwracajacy WSZYSTKIE URL-e elementow breadcrumba naraz jako liste, np.
  `//nav[contains(@class,"breadcrumb")]//a/@href` (dopasuj XPath do struktury DOM swojej
  strony) - nazwij go np. `Breadcrumb_URL`. Screaming Frog sam ponumeruje wyniki w kolumnach
  wyjsciowych (`Breadcrumb_URL 1`, `Breadcrumb_URL 2`, ...).
- Drugi analogicznie dla nazw (tekst zamiast `href`), np.
  `//nav[contains(@class,"breadcrumb")]//a/text() | //nav[contains(@class,"breadcrumb")]//span[last()]/text()`
  - nazwij go `Breadcrumb_Name` (musi zawierac TEZ biezaca strone jako ostatni element, nie
  tylko przodkow - stad dodatkowy warunek na ostatni element bez linku).
- **Wazne zalozenie, na ktorym opiera sie regula filtr_wlasny/filtr_tego_samego_poziomu/
  filtr_podrzedny:** strona z parametrem/fasetem (np. `?brand=PHILIPS`) musi miec **identyczny**
  breadcrumb jak jej kategoria bazowa. Jesli Twoja strona zmienia breadcrumb dla stron
  filtrowanych, te reguly nie zadzialaja poprawnie.

**3. Embeddingi (opcjonalnie, dla warstwy embedding_podobienstwo):**
`Config -> Custom -> Extraction`, dodaj ekstraktor typu **Custom JavaScript**, ktory:
- pobiera tresc strony (np. glowny tekst/opis kategorii),
- wywoluje API embeddingow (np. OpenAI `text-embedding-3-small` albo inny model),
- zwraca wynik jako pojedynczy string z liczbami rozdzielonymi przecinkami, np.
  `"0.0123,-0.0456,0.0789,..."` (nawiasy klamrowe/kwadratowe tez sa tolerowane).

Nazwij ten ekstraktor **dokladnie** `Extract embeddings from page content` - narzedzie
rozpoznaje TYLKO te jedna, konkretna nazwe kolumny (celowo, zeby nie wciagnac przypadkiem
innej kolumny). Bez tego kroku narzedzie dziala normalnie, po prostu bez tej warstwy.

**4. Eksport:**
Po zakonczeniu crawla: zakladka **Internal** (filtr HTML) -> **Export** (albo
`Bulk Export -> Web -> All`), format `.xlsx` lub `.csv`. Upewnij sie, ze w eksporcie sa
kolumny: `Address`/`Original Url`, `Status Code`, `Indexability`, `H1-1`,
`Breadcrumb_URL 1..N`, `Breadcrumb_Name 1..N` i (opcjonalnie) `Extract embeddings from page content`.

**5. Noindex a filtry:**
Fasety/filtry czesto maja `noindex, follow` (celowo, zeby nie rozdmuchiwac indeksu) - to NIE
znaczy, ze nie warto do nich linkowac wewnetrznie. Jesli chcesz uwzglednic takie strony w
analizie, odznacz checkbox "Nie uwzgledniaj noindex" w sekcji 2 ponizej.
        """
    )

st.header("1. Wgraj pliki")
col1, col2 = st.columns(2)
with col1:
    kategorie_file = st.file_uploader("Kategorie (xlsx lub sitemapa .xml)", type=["xlsx", "xls", "xml"], key="kategorie")
    marki_file = st.file_uploader("Marki (xlsx lub sitemapa .xml)", type=["xlsx", "xls", "xml"], key="marki")
with col2:
    filtry_file = st.file_uploader("Filtry (xlsx lub sitemapa .xml)", type=["xlsx", "xls", "xml"], key="filtry")
    internal_html_file = st.file_uploader(
        "Internal HTML (eksport crawlera, xlsx lub csv)", type=["xlsx", "xls", "csv"], key="internal_html"
    )

st.header("2. Wykluczenia")
c1, c2, c3 = st.columns(3)
with c1:
    exclude_3xx = st.checkbox("Nie uwzgledniaj 3xx (przekierowania)", value=True)
with c2:
    exclude_4xx = st.checkbox("Nie uwzgledniaj 4xx (nie znaleziono)", value=True)
with c3:
    exclude_noindex = st.checkbox("Nie uwzgledniaj noindex (Non-Indexable)", value=True)
st.caption("Strony z kodem 5xx sa wykluczane zawsze - to nigdy nie jest dobry kandydat na link.")

max_level_diff = st.slider(
    "Maksymalna roznica poziomow dla kategoria_podrzedna / filtr_podrzedny",
    min_value=1,
    max_value=5,
    value=1,
    help=(
        "Ogranicza, jak daleko 'w dol' drzewa kategorii moze isc automatyczny link "
        "z szerokiego dzialu (np. 'AGD male' -> kategoria 3 poziomy nizej). Domyslnie 1 "
        "= tylko bezposrednie dzieci. Kandydaci ponizej tego limitu nie znikaja - trafiaja "
        "do arkusza 'Pominiete_zbyt_glebokie' w pliku do oceny, do recznej decyzji."
    ),
)

embedding_top_n = st.slider(
    "Liczba propozycji z warstwy embedding_podobienstwo na strone (0 = wylacz, max 10)",
    min_value=0,
    max_value=10,
    value=10,
    help=(
        "Dodatkowa warstwa rekomendacji oparta o podobienstwo tresci (cosine similarity "
        "na embeddingach z kolumny 'Extract embeddings from page content' w Internal HTML). "
        "Dziala TYLKO jesli taka kolumna jest w pliku. Dla kazdej strony dobiera N "
        "najbardziej podobnych innych stron, ale POMIJA pary, ktore juz maja rekomendacje "
        "z innej reguly - to czysto dodatkowa warstwa, zawsze na samym koncu wynikow."
    ),
)

anchor_suffix_to_strip = st.text_input(
    "Sufiks do usuniecia z konca Anchor (opcjonalnie)",
    value=_configured_anchor_suffix(),
    help=(
        "Jesli H1 (a wiec i Anchor) na Twojej stronie konczy sie stalym dopiskiem "
        "(np. nazwa sklepu/marki), wpisz go tutaj - zostanie obciety z konca kazdego "
        "Anchora w wynikach. Domyslna wartosc mozna ustawic w Secrets (klucz "
        "`anchor_suffix_to_strip`), zeby nie wpisywac za kazdym razem recznie."
    ),
)

st.header("3. Analiza")
run_clicked = st.button("▶️ Uruchom analize", type="primary")

if run_clicked:
    missing = [
        name
        for name, f in [
            ("Kategorie", kategorie_file),
            ("Filtry", filtry_file),
            ("Marki", marki_file),
            ("Internal HTML", internal_html_file),
        ]
        if f is None
    ]
    if missing:
        st.error(f"Brakuje plikow: {', '.join(missing)}. Wgraj wszystkie 4 pliki przed analiza.")
    else:
        with st.spinner("Wczytywanie plikow..."):
            try:
                category_urls = read_url_list_file(kategorie_file)
                filtry_urls = read_url_list_file(filtry_file)
                marka_urls = read_url_list_file(marki_file)
                internal_html_rows = read_internal_html_file(internal_html_file)
            except Exception as e:
                st.error(f"Blad wczytywania plikow: {e}")
                st.stop()

        st.success(
            f"Wczytano: {len(category_urls)} kategorii, {len(filtry_urls)} filtrow, "
            f"{len(marka_urls)} marek, {len(internal_html_rows)} wierszy z Internal HTML."
        )

        # Audyt: wszystkie URL-e wgrane do narzedzia (listy Kategorie/Filtry/Marki),
        # niezaleznie czy ostatecznie zostaly dopasowane do crawla - patrz arkusz
        # Wszystkie_adresy_wejsciowe w pliku "do oceny". Status Code / Indexability
        # brane z SUROWEGO Internal HTML (przed wykluczeniami 3xx/4xx/noindex) -
        # to wlasnie pozwala zobaczyc, ktore adresy odpadly z analizy i dlaczego.
        crawl_by_url = {r["url"]: r for r in internal_html_rows if r.get("url")}

        def _crawl_status_indexability(url: str) -> dict:
            row = crawl_by_url.get(url)
            if row is None:
                return {"Status Code": "", "Indexability": "(brak w Internal HTML)"}
            return {"Status Code": row.get("status_code"), "Indexability": row.get("indexability")}

        all_input_urls = (
            [{"URL": u, "Typ": "kategoria", "Źródło": kategorie_file.name, **_crawl_status_indexability(u)} for u in category_urls]
            + [{"URL": u, "Typ": "filtr", "Źródło": filtry_file.name, **_crawl_status_indexability(u)} for u in filtry_urls]
            + [{"URL": u, "Typ": "marka", "Źródło": marki_file.name, **_crawl_status_indexability(u)} for u in marka_urls]
        )

        with st.spinner("Budowanie kandydatow do linkowania..."):
            pages = build_pages(
                internal_html_rows,
                category_urls,
                filtry_urls,
                marka_urls,
                exclude_3xx=exclude_3xx,
                exclude_4xx=exclude_4xx,
                exclude_noindex=exclude_noindex,
            )
            if not pages:
                st.error(
                    "Po zastosowaniu filtrow i dopasowaniu do list Kategorie/Filtry/Marki nie zostala "
                    "zadna strona. Sprawdz, czy URL-e w Internal HTML pokrywaja sie z URL-ami na "
                    "listach (np. http vs https, koncowe slashe)."
                )
                st.stop()

            result = run_all_rules(
                pages,
                max_level_diff=max_level_diff,
                embedding_top_n=embedding_top_n,
                anchor_suffix_to_strip=anchor_suffix_to_strip.strip(),
            )
            all_candidates = result["all_candidates"]

        st.subheader("Podsumowanie")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Kandydaci (unikalne pary)", len(all_candidates))
        m2.metric("Strony wziete pod uwage", len(pages))
        m3.metric("Kategorie L1 (do recznego uzup.)", len(result["l1_categories"]))
        m4.metric("Kategorie L2 pod L1 (bez siostr)", len(result["l2_under_l1_no_siblings"]))

        m5, m6 = st.columns(2)
        m5.metric(
            "Odcieci limitem glebokosci (arkusz Pominiete_zbyt_glebokie)",
            len(result["cut_by_depth_candidates"]),
        )
        m6.metric(
            "Kategorie wykluczone z marki (nazwa generyczna)",
            len(result["brand_generic_excluded"]),
        )
        m7, m8 = st.columns(2)
        m7.metric(
            "Propozycje z L1 (przeniesione do arkusza L1_do_uzupelnienia)",
            len(result["l1_outbound_candidates"]),
        )
        m8.metric(
            "Nowe propozycje z warstwy embedding_podobienstwo",
            len(result["embedding_candidates"]),
        )

        st.caption("Kandydaci do linkowania - rozbicie na 3 zeszyty wg Source_Type:")
        mk1, mk2, mk3 = st.columns(3)
        by_source_type = pd.Series([c["Source_Type"] for c in all_candidates]).value_counts()
        mk1.metric("Kandydaci do link. (kategorie)", int(by_source_type.get("category", 0)))
        mk2.metric("Kandydaci do link. (marki)", int(by_source_type.get("brand", 0)))
        mk3.metric("Kandydaci do link. (filtry)", int(by_source_type.get("filtered_category", 0)))

        rule_counts = pd.Series(
            [r for c in all_candidates for r in c["Rule"].split(" + ")]
        ).value_counts()
        st.bar_chart(rule_counts)

        st.subheader("Podglad kandydatow (pierwsze 200 wierszy)")
        st.dataframe(pd.DataFrame(all_candidates).head(200), width="stretch")

        # Liczenie regul (i warstwy embedding_podobienstwo) trwa milisekundy nawet
        # dla tysiecy stron - realny czas czekania to zapis xlsx: stylowanie
        # komorka-po-komorce w openpyxl dla kilkunastu tysiecy wierszy potrafi
        # zajac dziesiatki sekund, stad pasek postepu wlasnie tutaj.
        progress_bar = st.progress(0, text="Zapisywanie pliku 'do oceny'...")

        def _make_progress_cb(prefix):
            def _cb(stage, done, total):
                frac = min(max(done / total, 0.0), 1.0) if total else 1.0
                progress_bar.progress(frac, text=f"{prefix}: {stage} - {done}/{total} wierszy ({frac * 100:.0f}%)")
            return _cb

        review_bytes = build_review_workbook(
            all_candidates,
            result["l1_categories"],
            result["l2_under_l1_no_siblings"],
            result["no_base_found"],
            pages,
            cut_by_depth_candidates=result["cut_by_depth_candidates"],
            max_level_diff=result["max_level_diff"],
            brand_generic_excluded=result["brand_generic_excluded"],
            l1_outbound_candidates=result["l1_outbound_candidates"],
            embedding_top_n=result["embedding_top_n"],
            embedding_skipped=result["embedding_skipped"],
            all_input_urls=all_input_urls,
            progress=_make_progress_cb("Plik 'do oceny'"),
        )

        progress_bar.progress(0, text="Zapisywanie macierzy Contentful...")
        contentful_bytes = build_contentful_matrix(
            all_candidates,
            progress=_make_progress_cb("Macierz Contentful"),
        )
        progress_bar.progress(1.0, text="Gotowe!")

        st.session_state["review_bytes"] = review_bytes
        st.session_state["contentful_bytes"] = contentful_bytes
        st.session_state["all_candidates"] = all_candidates

if "review_bytes" in st.session_state:
    st.header("4. Pobierz pliki")
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "⬇️ Pobierz XLSX do oceny",
            data=st.session_state["review_bytes"],
            file_name="chmura_linkow_do_oceny.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with d2:
        st.download_button(
            "⬇️ Pobierz macierz do Contentful",
            data=st.session_state["contentful_bytes"],
            file_name="chmura_linkow_matryca_contentful.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
with st.expander("Krok 2 (opcjonalnie): macierz Contentful z RECZNIE POPRAWIONEGO pliku"):
    st.markdown(
        "Jesli po pobraniu pliku 'do oceny' recznie usunales/dodales wiersze w ktorejkolwiek "
        "z zakladek **Kandydaci do link. (kategorie / marki / filtry)**, wgraj tu poprawiony "
        "plik - macierz do Contentful zostanie zbudowana TYLKO z tych wierszy, ktore w nim "
        "zostaly (ze wszystkich 3 zakladek naraz)."
    )
    edited_file = st.file_uploader(
        "Poprawiony plik (zakladki Kandydaci do link. (...))", type=["xlsx"], key="edited"
    )
    if edited_file is not None:
        edited_rows = []
        sheets_found = []
        for _, sheet_name in SOURCE_TYPE_SHEET_NAMES:
            try:
                df = pd.read_excel(edited_file, sheet_name=sheet_name)
            except Exception:
                continue
            sheets_found.append(sheet_name)
            edited_rows.extend(df.to_dict(orient="records"))

        if not sheets_found:
            st.error(
                "Nie znaleziono zadnej z zakladek 'Kandydaci do link. (...)' w tym pliku. "
                "Upewnij sie, ze wgrywasz plik pobrany z tego narzedzia (ewentualnie recznie "
                "poprawiony, ale z zachowanymi nazwami zakladek)."
            )
        else:
            required_cols = {"Source_URL", "Target_URL"}
            missing_cols = [
                r for r in edited_rows if not required_cols.issubset(r.keys())
            ]
            if missing_cols:
                st.error(f"Kazda zakladka musi zawierac kolumny: {', '.join(required_cols)}.")
            else:
                edited_contentful_bytes = build_contentful_matrix(edited_rows)
                st.success(
                    f"Wczytano {len(edited_rows)} wierszy z {len(sheets_found)} zakladek "
                    f"({', '.join(sheets_found)})."
                )
                st.download_button(
                    "⬇️ Pobierz macierz Contentful (z poprawionego pliku)",
                    data=edited_contentful_bytes,
                    file_name="chmura_linkow_matryca_contentful_poprawiona.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
