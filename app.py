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

import threading
import time

import pandas as pd
import streamlit as st

from linking_engine import build_pages, run_all_rules
from io_utils import read_url_list_file, read_internal_html_file
from export import build_review_workbook, build_contentful_matrix, SOURCE_TYPE_SHEET_NAMES
import ai_eval


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


def _configured_openai_key() -> str:
    """Domyslna wartosc pola z kluczem OpenAI - z Secrets (klucz `openai_api_key`),
    zeby klucz nie musial byc wpisywany recznie za kazdym razem ani zaszyty w kodzie."""
    try:
        return st.secrets.get("openai_api_key") or ""
    except Exception:
        return ""


def _configured_openai_model() -> str:
    """Domyslny model OpenAI - z Secrets (klucz `openai_model`), albo
    ai_eval.DEFAULT_MODEL jesli nie ustawiono (latwo podmienic bez zmiany kodu,
    gdyby model zostal wycofany/zmieniony)."""
    try:
        return st.secrets.get("openai_model") or ai_eval.DEFAULT_MODEL
    except Exception:
        return ai_eval.DEFAULT_MODEL


def _ai_thread_running() -> bool:
    """Czy w tle dziala watek oceny AI (patrz sekcja 3. Analiza nizej) - Streamlit
    nie przetwarza klikniec w trakcie jednego dlugiego, synchronicznego przebiegu
    skryptu, wiec ocena AI idzie w osobnym watku, a glowny skrypt co chwile sam
    sie odswieza (st.rerun) i przy okazji sprawdza stan tego watku / przycisk Przerwij."""
    t = st.session_state.get("ai_thread")
    return t is not None and t.is_alive()


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
3. Zaznaczasz wykluczenia (3xx / 4xx / noindex / soft-404, opcjonalnie tez linki juz istniejace
   na stronie - menu glowne/boczne/box kategorii/opis kategorii), ustawiasz suwaki i klikasz
   **Uruchom analize**.
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
   tylko dokdada. Kolumna `Podobienstwo` (0-1) jest wypelniona tylko dla tych wierszy. To
   JEDYNA warstwa bez potwierdzenia strukturalnego (breadcrumb) - stad opcjonalna ocena AI
   ponizej, TYLKO dla niej.

### Ocena AI trafnosci warstwy embedding_podobienstwo (opcjonalnie)

Poniewaz embedding_podobienstwo nie ma zadnego potwierdzenia strukturalnego (moze polaczyc
tematycznie odlegle strony, ktore sa embeddingowo podobne z przypadku, np. "Multicookery" z
"Zamrazarkami"), mozesz wlaczyc dodatkowa ocene modelu OpenAI: dla kazdej takiej pary model
dostaje Title + H1 obu stron i odpowiada **TAK** (dobrze pasuja) / **NIE** (nie pasuja, link
bylby mylacy) / **MOŻE** (niejednoznaczne, do recznej weryfikacji). Wynik trafia do kolumny
`Ocena_AI` zaraz obok `Rule`. Pozostale reguly NIE sa oceniane przez AI - maja juz potwierdzenie
po breadcrumbie, nie ma takiej potrzeby. Wymaga klucza API OpenAI (pole w sekcji 2 ponizej;
domyslna wartosc mozna ustawic w Secrets - klucz `openai_api_key`, model `openai_model`).

Ocena idzie paczkami (po 30 par) i pokazuje wlasny pasek postepu w sekcji 3 ponizej. W trakcie
mozna kliknac **⏹ Przerwij ocene AI** - przerwanie konczy biezaca paczke i od razu buduje pliki
wynikowe z tym, co juz zdazylo zostac ocenione (reszta wierszy zostaje po prostu bez `Ocena_AI`,
tak jak przy bledzie zapytania). Reszta analizy (wszystkie pozostale reguly) nie jest tym w ogole
dotknieta - to przerywa wylacznie ocene AI.

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
`Config -> Custom -> Extraction`. Dodaj **dwa** ekstraktory typu XPath. Przyklad z dzialajacej
konfiguracji (PODMIEN `twoja-domena.pl` i selektor `nav` na wlasne):
- Jeden zwracajacy WSZYSTKIE URL-e elementow breadcrumba naraz jako liste:
  `//nav[@data-test="breadcrumb-ul"]/ol/li[position()>1]/a[not(@href="https://twoja-domena.pl/marki")]/@href`
  - nazwij go **`Breadcrumb_URL`**. Screaming Frog sam ponumeruje wyniki w kolumnach
  wyjsciowych (`Breadcrumb_URL 1`, `Breadcrumb_URL 2`, ...). Warunek `not(@href=...)` wyklucza
  z breadcrumba ogolny link do "strefy marek" (jesli Twoja strona ma cos podobnego w
  breadcrumbie) - usun ten warunek, jesli nie jest potrzebny.
- Drugi analogicznie dla nazw (tekst zamiast `href`):
  `//nav[@data-test="breadcrumb-ul"]/ol/li[position()>1]/a[not(@href="https://twoja-domena.pl/marki")]/span[not(@aria-hidden)]/text() | //nav[@data-test="breadcrumb-ul"]/ol/li[position()>1]/div[@aria-current="true"]/span[not(@aria-hidden)]/text()`
  - nazwij go **`Breadcrumb_Name`**. Druga czesc warunku (za `|`, `div[@aria-current="true"]`)
  lapie BIEZACA strone w breadcrumbie - w wielu nowoczesnych komponentach UI ostatni segment
  (biezaca strona) nie jest linkiem `<a>` tylko zwyklym elementem z atrybutem
  `aria-current="true"`, wiec bez tego warunku ostatni element breadcrumba (czyli nazwa
  biezacej strony) by sie zgubil. `span[not(@aria-hidden)]` pomija zdublowany, ukryty tekst
  dla czytnikow ekranu (tez typowe w nowoczesnych komponentach).
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

**4. Linki wewnetrzne z konkretnych miejsc na stronie (Custom JavaScript, opcjonalnie):**
`Config -> Custom -> Extraction`, cztery osobne ekstraktory typu **Custom JavaScript** - kazdy
zbiera linki wewnetrzne (tylko `twoja-domena.pl` - PODMIEN na wlasna domene) z INNEGO miejsca
na stronie, jeden pod drugim w jednej komorce. Te dane NIE sa jeszcze wykorzystywane przez
regoly linkowania w tym narzedziu (to osobny etap, do przyszlego uzycia) - na razie sa tylko
zbierane do pliku Internal HTML.

`existing_links_menu_main` (poziomy pasek nawigacji pod naglowkiem):
```js
var container = document.querySelector('nav[aria-label="Main Navigation"]');
if (!container) { return seoSpider.data(''); }

var links = [];

Array.prototype.forEach.call(container.querySelectorAll('ul[role="menubar"] a[href]'), function (a) {
  var href = a.getAttribute('href');
  if (!href) { return; }
  var abs;
  try {
    abs = new URL(href, document.location.href).href;
  } catch (e) {
    return;
  }
  if (abs.indexOf('twoja-domena.pl') === -1) { return; }
  links.push(abs);
});

return seoSpider.data(links.join('\n'));
```

`existing_links_menu_left` (wysuwane menu "Kategorie"):
```js
var container = document.getElementById('app-header-category-nav-id');
if (!container) { return seoSpider.data(''); }

var links = [];

Array.prototype.forEach.call(container.querySelectorAll('a[href]'), function (a) {
  var href = a.getAttribute('href');
  if (!href) { return; }
  var abs;
  try {
    abs = new URL(href, document.location.href).href;
  } catch (e) {
    return;
  }
  if (abs.indexOf('twoja-domena.pl') === -1) { return; }
  links.push(abs);
});

return seoSpider.data(links.join('\n'));
```

`existing_links_category_box` (drzewko filtrow kategorii, tylko na stronach listingowych):
```js
var container = document.querySelector('details[data-test="facet-tree-category"]');
if (!container) { return seoSpider.data(''); }

var links = [];

Array.prototype.forEach.call(container.querySelectorAll('a[href]'), function (a) {
  var href = a.getAttribute('href');
  if (!href) { return; }
  var abs;
  try {
    abs = new URL(href, document.location.href).href;
  } catch (e) {
    return;
  }
  if (abs.indexOf('twoja-domena.pl') === -1) { return; }
  links.push(abs);
});

return seoSpider.data(links.join('\n'));
```

`existing_links_bottom` (blok tresci/linkow na dole strony kategorii):
```js
var container = document.querySelector('aside#category-bottom');
if (!container) { return seoSpider.data(''); }

var links = [];

Array.prototype.forEach.call(container.querySelectorAll('a[href]'), function (a) {
  var href = a.getAttribute('href');
  if (!href) { return; }
  var abs;
  try {
    abs = new URL(href, document.location.href).href;
  } catch (e) {
    return;
  }
  if (abs.indexOf('twoja-domena.pl') === -1) { return; }
  links.push(abs);
});

return seoSpider.data(links.join('\n'));
```

Dopasuj selektor kontenera (`document.querySelector(...)` / `getElementById(...)`) w kazdym
skrypcie do wlasnej struktury HTML - powyzsze sa przykladem z konkretnej strony i pewnie nie
zadzialaja "z automatu" na innym sklepie. **Uwaga: eksport do .xlsx ma limit 32 767 znakow na
komorke** - jesli ktoras z tych list linkow jest bardzo dluga (np. rozbudowane mega-menu),
Screaming Frog obetnie zawartosc komorki z komunikatem "Data too large for file format".
Rozwiazanie: eksportuj Internal HTML jako **.csv** zamiast .xlsx (narzedzie obsluguje oba
formaty, a .csv nie ma tego limitu).

**5. Custom Search: wykrywanie stron soft-404 (opcjonalnie):**
`Config -> Custom -> Search`, dodaj wpis: **Search** = `404_Media.svg`, **Filter** = `Contains`
(dopasuj do wlasnej strony bledu - to przyklad konkretnego elementu graficznego, ktory
Twoja strona bledu osadza w HTML).

Chodzi o strony typu **soft-404**: zwracaja HTTP 200 (wygladaja jak normalna, dzialajaca
strona - Status Code i Indexability nie zlapia tego problemu), ale w rzeczywistosci pokazuja
uzytkownikowi komunikat "nie znaleziono strony" (typowy przypadek: usunieta/przeniesiona
kategoria albo filtr, ktora serwer i tak odpowiada kodem 200 zamiast prawdziwego 404). Jesli
szablon takiej strony w Twoim sklepie osadza konkretny, unikalny element (np. plik graficzny
`404_Media.svg`, ktory nie wystepuje na normalnych stronach), Custom Search go wylapie - w
eksporcie pojawi sie dodatkowa kolumna z wynikiem wyszukiwania. Odfiltruj/usun takie adresy
PRZED wgraniem Internal HTML do tego narzedzia (obecny mechanizm wykluczen w sekcji 2 ponizej,
oparty o Status Code/Indexability, ich nie zlapie - to znany, jeszcze nie zautomatyzowany
przypadek).

**6. Eksport:**
Po zakonczeniu crawla: zakladka **Internal** (filtr HTML) -> **Export** (albo
`Bulk Export -> Web -> All`), format `.xlsx` lub `.csv`. Upewnij sie, ze w eksporcie sa
kolumny: `Address`/`Original Url`, `Status Code`, `Indexability`, `H1-1`, `Title 1`
(opcjonalna, ale wymagana do oceny AI - patrz nizej), `Breadcrumb_URL 1..N`,
`Breadcrumb_Name 1..N` i (opcjonalnie) `Extract embeddings from page content`.

**7. Noindex a filtry:**
Fasety/filtry czesto maja `noindex, follow` (celowo, zeby nie rozdmuchiwac indeksu) - to NIE
znaczy, ze nie warto do nich linkowac wewnetrznie. Jesli chcesz uwzglednic takie strony w
analizie, odznacz checkbox "Nie uwzgledniaj noindex" w sekcji 2 ponizej.
        """
    )

with st.expander("Proces przygotowania danych - kolejnosc krokow (kliknij, zeby rozwinac)", expanded=False):
    st.markdown(
        """
1) Pobierz sitemapy
2) Wrzuc w narzedzie do ekstrakcji linkow z sitemap
3) Usun z sitemap content adresy dotyczace tylko kategorii na blogu
4) Ustaw Screaming Frog i przecrawluj wszystkie pozostale adresy
5) Wrzuc wszystkie pliki do tego narzedzia
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
c1, c2, c3, c4 = st.columns(4)
with c1:
    exclude_3xx = st.checkbox("Nie uwzgledniaj 3xx (przekierowania)", value=True)
with c2:
    exclude_4xx = st.checkbox("Nie uwzgledniaj 4xx (nie znaleziono)", value=True)
with c3:
    exclude_noindex = st.checkbox("Nie uwzgledniaj noindex (Non-Indexable)", value=True)
with c4:
    exclude_soft_404 = st.checkbox(
        "Nie uwzgledniaj soft-404",
        value=True,
        help=(
            "Strony, ktore zwracaja HTTP 200 (wygladaja jak normalna strona - Status Code i "
            "Indexability tego NIE zlapia), ale w rzeczywistosci pokazuja komunikat 'nie "
            "znaleziono'. Patrz kolumna 'Soft 404' (Custom Search w Screaming Frog, zakladka "
            "'Konfiguracja Screaming Frog' ponizej) - 0 = nie jest soft-404, 1 lub wiecej = "
            "jest. Brak tej kolumny w pliku Internal HTML nie przeszkadza - checkbox po prostu "
            "nie ma wtedy nic do wykluczenia."
        ),
    )
st.caption("Strony z kodem 5xx sa wykluczane zawsze - to nigdy nie jest dobry kandydat na link.")

st.subheader("Linki wewnetrzne juz istniejace na stronie (opcjonalnie)")
st.caption(
    "Jesli proponowany link (Target_URL) jest JUZ podlinkowany na stronie zrodlowej w jednym "
    "z ponizszych miejsc (patrz Custom JavaScript w zakladce 'Konfiguracja Screaming Frog' "
    "ponizej), taka propozycja jest zbedna - trafia do arkusza 'Pominiete_juz_na_stronie' "
    "zamiast do glownej listy kandydatow. Brak ktorejkolwiek z tych kolumn w pliku Internal "
    "HTML nie przeszkadza - odpowiadajacy checkbox po prostu nie ma wtedy nic do wykluczenia."
)
m1, m2, m3, m4 = st.columns(4)
with m1:
    exclude_existing_menu_main = st.checkbox(
        "Nie uwzgledniaj stron podlinkowanych w menu glownym", value=True
    )
with m2:
    exclude_existing_menu_left = st.checkbox(
        "Nie uwzgledniaj adresow z menu bocznego", value=True
    )
with m3:
    exclude_existing_category_box = st.checkbox(
        "Nie uwzgledniaj adresow z menu kategorii (nawigacja fasetowa)", value=True
    )
with m4:
    exclude_existing_bottom = st.checkbox(
        "Nie uwzgledniaj adresow dodanych juz w opisie kategorii (bottom)", value=True
    )

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

st.subheader("Ocena AI trafnosci warstwy embedding_podobienstwo (opcjonalnie)")
st.caption(
    "Dotyczy WYLACZNIE propozycji z warstwy embedding_podobienstwo (jedynej bez potwierdzenia "
    "strukturalnego po breadcrumbie) - reszta regul nie jest wysylana do OpenAI, bo nie ma takiej "
    "potrzeby. Wynik (TAK / NIE / MOŻE) trafia do kolumny `Ocena_AI` zaraz obok `Rule`."
)
use_ai_eval = st.checkbox(
    "Wlacz ocene AI (wymaga klucza OpenAI)",
    value=bool(_configured_openai_key()),
    disabled=_ai_thread_running(),
)
ai_col1, ai_col2 = st.columns(2)
with ai_col1:
    openai_api_key = st.text_input(
        "Klucz API OpenAI",
        value=_configured_openai_key(),
        type="password",
        help=(
            "Domyslna wartosc mozna ustawic w Secrets (klucz `openai_api_key`), zeby nie wpisywac "
            "recznie za kazdym razem - patrz README."
        ),
        disabled=not use_ai_eval,
    )
with ai_col2:
    openai_model = st.text_input(
        "Model OpenAI",
        value=_configured_openai_model(),
        help="Domyslna wartosc mozna ustawic w Secrets (klucz `openai_model`).",
        disabled=not use_ai_eval,
    )
st.caption(
    "Koszt/czas: kazde zapytanie ocenia do 30 par naraz (Title + H1 obu stron - nie cala tresc "
    "strony), wiec liczba zapytan to z grubsza liczba propozycji embeddingowych podzielona przez 30. "
    "Przy duzej liczbie propozycji (wysoki suwak embedding_top_n powyzej) moze to zajac dluzsza chwile."
)

def _show_summary_and_build_outputs(pages, result, all_candidates, all_input_urls, ai_errors, ai_results=None):
    """
    Wspolny "finisz" po analizie - Podsumowanie + budowa 2 plikow xlsx +
    zapis do session_state (skad je pobiera sekcja '4. Pobierz pliki').
    Wywolywane w dwoch miejscach: od razu po run_all_rules (gdy ocena AI jest
    wylaczona/pominieta) albo dopiero po zakonczeniu watku oceny AI w tle
    (patrz sekcja 3. Analiza nizej).

    `ai_results`: opcjonalny dict {(Source_URL, Target_URL): ocena} z watku
    oceny AI (patrz ai_eval.evaluate_embedding_candidates - results_holder).
    Watek mutuje kandydatow IN PLACE (powinno wystarczyc samo w sobie), ale
    TUTAJ dodatkowo jawnie "doklejamy" oceny po kluczu jako zabezpieczenie -
    zgloszony przypadek na Streamlit Community Cloud, gdzie podsumowanie w UI
    mialo poprawne oceny, ale plik xlsx wychodzil z pusta kolumna Ocena_AI
    (mutacja in-place najwyrazniej nie zawsze przezywa watek + kolejne
    st.rerun/session_state na tej platformie).
    """
    if ai_results:
        for c in all_candidates + result["l1_outbound_candidates"]:
            key = (c.get("Source_URL"), c.get("Target_URL"))
            if key in ai_results:
                c["Ocena_AI"] = ai_results[key]

    if ai_errors:
        st.warning(
            "Niektore zapytania do OpenAI nie powiodly sie - dotkniete wiersze zostaly "
            "bez oceny (Ocena_AI puste), reszta analizy dziala normalnie:\n\n"
            + "\n".join(f"- {e}" for e in ai_errors[:10])
        )

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
    m9, _ = st.columns(2)
    m9.metric(
        "Odcieci - link juz na stronie (arkusz Pominiete_juz_na_stronie)",
        len(result["cut_by_existing_link_candidates"]),
    )

    embedding_rows_all = [
        c for c in all_candidates + result["l1_outbound_candidates"]
        if c.get("Rule") == "embedding_podobienstwo"
    ]
    if any(c.get("Ocena_AI") for c in embedding_rows_all):
        ai_verdicts = pd.Series([c.get("Ocena_AI") for c in embedding_rows_all])
        ai_col_a, ai_col_b, ai_col_c = st.columns(3)
        ai_col_a.metric("Ocena AI: TAK", int((ai_verdicts == "TAK").sum()))
        ai_col_b.metric("Ocena AI: MOŻE", int((ai_verdicts == "MOŻE").sum()))
        ai_col_c.metric("Ocena AI: NIE", int((ai_verdicts == "NIE").sum()))

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
        cut_by_existing_link_candidates=result["cut_by_existing_link_candidates"],
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


st.header("3. Analiza")
run_clicked = st.button("▶️ Uruchom analize", type="primary", disabled=_ai_thread_running())

if run_clicked and not _ai_thread_running():
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
                exclude_soft_404=exclude_soft_404,
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
                exclude_existing_menu_main=exclude_existing_menu_main,
                exclude_existing_menu_left=exclude_existing_menu_left,
                exclude_existing_category_box=exclude_existing_category_box,
                exclude_existing_bottom=exclude_existing_bottom,
            )
            all_candidates = result["all_candidates"]

        # posprzataj ewentualne resztki po poprzednim biegu
        st.session_state.pop("ai_thread", None)
        st.session_state.pop("ai_stop_event", None)
        st.session_state.pop("ai_progress_holder", None)
        st.session_state.pop("ai_errors_holder", None)
        st.session_state.pop("ai_results_holder", None)

        ai_thread_started = False
        if use_ai_eval and openai_api_key:
            embedding_rows_to_eval = [
                c for c in all_candidates + result["l1_outbound_candidates"]
                if c.get("Rule") == "embedding_podobienstwo"
            ]
            if not embedding_rows_to_eval:
                pass  # brak propozycji embeddingowych - nic do oceny, przechodzimy prosto do budowy plikow
            else:
                # Ocena AI idzie w OSOBNYM WATKU (nie synchronicznie tutaj) - to jedyny
                # sposob, zeby przycisk "Przerwij" mial szanse zadzialac. W trakcie
                # jednego dlugiego, blokujacego wywolania Streamlit nie przetwarza
                # klikniec - dopiero po tym, jak skrypt sam sie odswiezy (st.rerun).
                page_by_url = {p.url: p for p in pages}
                stop_event = threading.Event()
                progress_holder = {"done": 0, "total": len(embedding_rows_to_eval)}
                errors_holder = []
                results_holder: dict[tuple, str] = {}
                model_to_use = openai_model.strip() or ai_eval.DEFAULT_MODEL

                def _ai_worker(
                    rows=embedding_rows_to_eval, pbu=page_by_url, key=openai_api_key,
                    model=model_to_use, ev=stop_event, ph=progress_holder, eh=errors_holder,
                    rh=results_holder,
                ):
                    errs = ai_eval.evaluate_embedding_candidates(
                        rows, pbu, api_key=key, model=model, stop_event=ev,
                        progress=lambda d, t: ph.update(done=d, total=t),
                        results_holder=rh,
                    )
                    eh.extend(errs)

                thread = threading.Thread(target=_ai_worker, daemon=True)
                st.session_state["ai_thread"] = thread
                st.session_state["ai_stop_event"] = stop_event
                st.session_state["ai_progress_holder"] = progress_holder
                st.session_state["ai_errors_holder"] = errors_holder
                st.session_state["ai_results_holder"] = results_holder
                thread.start()
                ai_thread_started = True

        if ai_thread_started:
            # Watek juz dziala w tle - zapisz co potrzebne po odswiezeniu (kazdy
            # rerun odtwarza caly skrypt od zera, lokalne zmienne znikaja) i
            # odswiez sie, zeby wejsc w galaz z paskiem postepu + przyciskiem
            # Przerwij (patrz elif _ai_thread_running() nizej).
            st.session_state["pending_pages"] = pages
            st.session_state["pending_result"] = result
            st.session_state["pending_all_candidates"] = all_candidates
            st.session_state["pending_all_input_urls"] = all_input_urls
            st.rerun()
        else:
            # Ocena AI wylaczona/pominieta - buduj wyniki od razu, bez dodatkowego
            # (niepotrzebnego w tym przypadku) przebiegu skryptu.
            _show_summary_and_build_outputs(pages, result, all_candidates, all_input_urls, [])

elif _ai_thread_running():
    # W trakcie oceny AI (odpalonej w gornej galezi, po czym skrypt sam sie
    # odswiezyl) - pokaz pasek postepu + przycisk Przerwij, po czym znowu sam
    # sie odswiez za chwile, zeby miec szanse zlapac ewentualne klikniecie.
    stop_event = st.session_state["ai_stop_event"]
    progress_holder = st.session_state["ai_progress_holder"]

    done, total = progress_holder.get("done", 0), progress_holder.get("total", 0)
    frac = min(max(done / total, 0.0), 1.0) if total else 0.0
    st.progress(frac, text=f"Ocena AI: {done}/{total} propozycji embeddingowych ({frac * 100:.0f}%)")

    if stop_event.is_set():
        st.info("Przerywanie... (konczy sie biezaca paczka zapytan do OpenAI)")
    elif st.button("⏹ Przerwij ocene AI", key="ai_stop_button"):
        stop_event.set()
        st.info("Przerywanie... (konczy sie biezaca paczka zapytan do OpenAI)")

    time.sleep(0.4)
    st.rerun()

elif st.session_state.get("pending_result") is not None:
    # Watek oceny AI (jesli byl) juz sie skonczyl (albo od razu nie byl
    # potrzebny) - budujemy pliki wynikowe. .pop() celowo - ten fragment ma
    # sie wykonac RAZ, tak jak dawniej (przy nastepnych, niepowiazanych
    # odswiezeniach np. po kliknieciu "Pobierz" ta sekcja ma juz nie wracac -
    # dokladnie jak wczesniej, kiedy to wszystko bylo w jednym bloku run_clicked).
    ai_errors = st.session_state.pop("ai_errors_holder", [])
    ai_results = st.session_state.pop("ai_results_holder", None)
    st.session_state.pop("ai_thread", None)
    st.session_state.pop("ai_stop_event", None)
    st.session_state.pop("ai_progress_holder", None)
    pages = st.session_state.pop("pending_pages")
    result = st.session_state.pop("pending_result")
    all_candidates = st.session_state.pop("pending_all_candidates")
    all_input_urls = st.session_state.pop("pending_all_input_urls")

    _show_summary_and_build_outputs(pages, result, all_candidates, all_input_urls, ai_errors, ai_results)

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
