# Chmura linkow - generator linkowania wewnetrznego

Aplikacja Streamlit generujaca propozycje linkowania wewnetrznego
(kategoria → kategoria + marka + filtr) na podstawie breadcrumba z crawla.
Zaprojektowana pod kategorie e-commerce, dziala na dowolnej stronie z podobna
struktura (kategorie / filtry-fasety / strony marek + breadcrumb).

## Jak to dziala

1. **Wgrywasz 4 pliki:**
   - **Kategorie** - lista URL-i stron kategorii (xlsx z kolumna adresu, albo
     sitemapa `.xml`)
   - **Filtry** - lista URL-i stron z parametrami/fasetami (np.
     `...?brand=PHILIPS`) - xlsx albo sitemapa
   - **Marki** - lista URL-i stron marek - xlsx albo sitemapa
   - **Internal HTML** - eksport crawlera (np. Screaming Frog) z kolumnami:
     adres, status code, indexability, H1 oraz breadcrumb wyciagniety Custom
     Extraction: `Breadcrumb_URL 1..N` (przodkowie) i `Breadcrumb_Name 1..N`
     (przodkowie + biezaca strona jako ostatni element)
2. **Typ kazdej strony** jest ustalany po przynaleznosci do listy
   Kategorie/Filtry/Marki - NIE po wzorcu URL-a. Dzieki temu narzedzie nie
   jest zaszyte pod jedna platforme e-commerce. Wyjatek: strona zagniezdzona
   w breadcrumbie pod URL-em z listy Marki (dowolny przodek) dziedziczy typ
   "brand", nawet jesli sama nie jest wprost na tej liscie - typowy przypadek
   to podstrona marka+kategoria (np. `brand/dafi/dzbanki-filtrujace`), ktorej
   lista Marki nie wymienia osobno, bo wymienia tylko glowne strony marek.
3. **Zaznaczasz wykluczenia** (3xx, 4xx, noindex), ustawiasz suwak **"Maksymalna
   roznica poziomow"** (domyslnie 1 - patrz sekcja Reguly ponizej) i klikasz
   "Uruchom analize".
4. **Dostajesz dwa pliki:**
   - `chmura_linkow_do_oceny.xlsx` - pelna lista kandydatow + arkusze
     pomocnicze (`L1_do_uzupelnienia`, `L2_pod_L1_bez_siostr`,
     `Pominiete_zbyt_glebokie`, `Marka_wykluczona_generyczna`, `Diagnostyka`)
     do recznej weryfikacji
   - `chmura_linkow_matryca_contentful.xlsx` - gotowa macierz: pierwsza
     kolumna `Source_URL`, kolejne `Link_1, Link_2, ...` z URL-ami do
     zalinkowania

Jest tez opcjonalny "Krok 2": jesli recznie poprawisz plik "do oceny"
(usunites/dodasz wiersze w zakladce `Kandydaci_linkowania`), mozesz go wgrac
ponownie, zeby zbudowac macierz Contentful TYLKO z zaakceptowanych linkow.

## Wymagany format Internal HTML

Minimalne kolumny (nazwy rozpoznawane w wersji nieczulej na wielkosc liter,
Screaming Frog exportuje dokladnie takie):

| Kolumna | Opis |
|---|---|
| `Address` | pelny URL strony |
| `Status Code` | np. 200, 301, 404 |
| `Indexability` | `Indexable` / `Non-Indexable` |
| `H1-1` (lub `H1`) | H1 strony - uzywane jako Anchor linku |
| `Breadcrumb_URL 1`, `Breadcrumb_URL 2`, ... | URL-e przodkow w breadcrumbie, w kolejnosci L1→Ln-1 |
| `Breadcrumb_Name 1`, `Breadcrumb_Name 2`, ... | nazwy przodkow + biezacej strony jako ostatni element, w kolejnosci L1→Ln |

**Original Url vs Address:** jesli plik ma obie kolumny (typowe dla eksportu
Screaming Frog w **List Mode**), URL bierzemy z `Original Url`, nie z `Address`.
`Address` bywa zdekodowana przez Screaming Frog (np. polski znak w wartosci
parametru filtra: `?colorNominated=Bia%C5%82y` -> `?colorNominated=Biały`),
podczas gdy `Original Url` zachowuje dokladnie taki zapis, w jakim URL zostal
podany na wejsciu. Jesli listy Kategorie/Filtry/Marki maja URL-e w formie
zakodowanej (typowe dla sitemap), dopasowanie po zdekodowanym `Address` po
prostu nie trafi - strona milczaco zniknie z analizy mimo ze jest w crawlu.
**Z tego samego powodu generuj listy Kategorie/Filtry/Marki tez z kolumny
`Original Url`**, jesli budujesz je z eksportu Screaming Frog - inaczej ten
sam problem wroci przez tylne drzwi (Internal HTML i listy będą zakodowane
niespojnie ze soba).

Breadcrumb pobiera sie w Screaming Frog przez **Custom Extraction (XPath)** -
patrz `docs/screaming_frog_setup.md` (jesli dolaczony) po przykladowa
konfiguracje; dla kazdej strony trzeba dopasowac XPath do jej struktury DOM.

**Wazna wlasciwosc, na ktorej opiera sie reguła kategoria→filtr:** strona z
parametrem/fasetem (np. `?brand=PHILIPS`) musi miec w crawlu **identyczny
breadcrumb** jak jej kategoria bazowa (czyli parametr URL nie zmienia
breadcrumba w DOM). Jesli Twoja strona zmienia breadcrumb dla stron
filtrowanych, ta reguła nie zadziala i trzeba ja dostosowac (np. wracajac do
mapowania przez osobny arkusz Main Category → Final URL).

## Reguly linkowania (Faza 1: kategoria → *)

- **kategoria_tego_samego_poziomu** - siostry po wspolnym rodzicu w
  breadcrumbie. Pomijane, gdy rodzicem jest kategoria L1 (szeroki,
  "koszykowy" dzial typu "Dom i Ogrod") - jego dzieci nie sa tematycznie
  spokrewnione mimo wspolnego rodzica. Takie kategorie trafiaja do
  `L2_pod_L1_bez_siostr` do recznej selekcji.
- **kategoria_nadrzedna** - kazda kategoria na poziomie **L5 lub glebiej**
  ZAWSZE linkuje w gore do swojego bezposredniego rodzica (dokladnie 1
  poziom wyzej, `Poziom_roznica = -1`). Niezalezne od limitu "Maksymalna
  roznica poziomow" - to nie jest reguła "w dol", nie podlega odcinaniu.
  Cel: glebokie, waskie galezie drzewa (bez rodzenstwa) maja przynajmniej
  jeden pewny automatyczny link. Prog `PARENT_LINK_MIN_LEVEL` (domyslnie 5)
  w `linking_engine.py`.
- **kategoria_podrzedna** - kazdy przodek z breadcrumba -> ta kategoria, na
  kazdym poziomie ponizej (nie tylko bezposrednie dzieci) - **ale tylko do
  limitu "Maksymalna roznica poziomow"** ustawionego suwakiem w UI (domyslnie
  `1` = tylko bezposrednie dzieci). Bez tego limitu szeroki dzial typu "AGD
  male" potrafil dostac 260 propozycji linkow naraz (2-4 poziomy w dol drzewa),
  co jest nie do wdrozenia bez recznego przycinania. Kandydaci odcieci limitem
  NIE znikaja - trafiaja do arkusza `Pominiete_zbyt_glebokie` do recznej
  decyzji, czy warto je jednak dodac.
- **filtr_wlasny / filtr_tego_samego_poziomu / filtr_podrzedny** - jak wyzej,
  ale dla stron z filtrem (dopasowanych do kategorii bazowej po identycznej
  krotce breadcrumba). `filtr_podrzedny` podlega temu samemu limitowi
  "Maksymalna roznica poziomow" co `kategoria_podrzedna` (ten sam mechanizm,
  ten sam problem "eksplozji" linkow pod szerokimi dzialami) - odcieci
  kandydaci tez trafiaja do `Pominiete_zbyt_glebokie`.
- **marka_precyzyjna_2seg** - dopasowanie kategorii do marki po 2 ostatnich
  segmentach nazwy w breadcrumbie (male ryzyko falszywych trafien).
- **marka_orientacyjna_1seg(_UWAGA_KOLIZJA)** - jak wyzej, po 1 segmencie
  (wiecej propozycji, ale nazwy powtarzajace sie w >1 dziale sa oznaczone
  `_UWAGA_KOLIZJA` do recznej weryfikacji - wykrywane automatycznie z danych,
  nie na sztywno). Kategorie/marki, ktorych **ostatni segment breadcrumba jest
  slowem w pelni generycznym** (np. "Akcesoria" - samo w sobie nie niesie
  zadnej informacji o produkcie, wiec np. kategoria "Akcesoria" w dziale AGD
  bledasnie parowala z marka "DJI Akcesoria") sa **calkowicie wykluczone** z
  dopasowania 1-segmentowego - nie tylko oznaczone jako kolizja, tylko w ogole
  nie generuja tej propozycji. Lista takich slow: `GENERIC_LEAF_EXCLUSIONS_DEFAULT`
  w `linking_engine.py` (na start: `"Akcesoria"`). Takie kategorie trafiaja do
  arkusza `Marka_wykluczona_generyczna` - dalej dostaja normalnie linki ze
  wszystkich pozostalych regul (kategoria_podrzedna, kategoria_tego_samego_poziomu,
  filtr_*, marka_precyzyjna_2seg), traca TYLKO orientacyjne dopasowanie marki
  po samej nazwie.

Kazdy wiersz w wyniku ma kolumne `Poziom_roznica` (Target level − Source
level: 0 = ten sam poziom, dodatnia = ile poziomow nizej jest target) - w
Excelu mozna to od razu przefiltrowac/posortowac.

**Sortowanie wynikow:** najpierw `Source_URL` rosnaco (A -> Z), a w obrebie
tego samego `Source_URL` wg priorytetu reguly: `kategoria_podrzedna` ->
`filtr_wlasny` -> `marka_orientacyjna_1seg(_UWAGA_KOLIZJA)` ->
`kategoria_tego_samego_poziomu` -> `filtr_tego_samego_poziomu` -> pozostale
reguly (`marka_precyzyjna_2seg`, `filtr_podrzedny`, `kategoria_nadrzedna`).
Dotyczy to zarowno arkusza `Kandydaci_linkowania` / `Pominiete_zbyt_glebokie`,
jak i kolejnosci `Link_1, Link_2, ...` w macierzy Contentful (patrz
`linking_engine.RULE_SORT_ORDER`).

**Kategorie L1 (departament najwyzszego poziomu) nigdy nie wystepuja jako
`Source_URL` w `Kandydaci_linkowania` ani w `Pominiete_zbyt_glebokie`** -
wszystkie ich automatyczne propozycje, z kazdej reguly (nie tylko
kategoria_podrzedna), sa przenoszone do arkusza `L1_do_uzupelnienia`. Chodzi
o to, zeby L1 bylo w calosci recznie przegladane w jednym miejscu, zamiast
mieszac sie z reszta kandydatow. L1 bez zadnych automatycznych propozycji
(np. brak dzieci w breadcrumbie) tez sie tam pojawia, jako placeholder do
recznego uzupelnienia.

## Kolejne fazy (do dopisania)

Silnik (`linking_engine.py`) jest tak zbudowany, zeby dalo sie dopisac kolejne
kierunki tym samym schematem:

- **filtr → kategoria + marka**
- **marka → kategoria + filtr**

Najprosciej: skopiuj `build_category_hierarchy_candidates` /
`build_category_brand_candidates` / `build_category_filter_candidates` i
zamien, ktory typ jest `Source`, a ktory `Target`.

## Haslo dostepu

Aplikacja jest zabezpieczona hasłem (ekran logowania przed wgraniem plikow).
Haslo ustawia sie przez **Secrets**, nie w kodzie:

- **Streamlit Community Cloud**: wejdz w aplikacje -> **Settings -> Secrets** i
  wklej:
  ```
  password = "twoje-haslo"
  ```
  Zapisz - aplikacja sama sie zrestartuje z nowym haslem. Zmiana hasla pozniej
  = ta sama sciezka, bez zadnego deployu.
- **Lokalnie**: stworz plik `.streamlit/secrets.toml` (w `.gitignore`, nigdy
  nie trafia do repo) z ta sama zawartoscia.

Jesli haslo nie jest ustawione w Secrets, aplikacja pokazuje blad zamiast
ekranu logowania (nie da sie jej przypadkiem zostawic bez zabezpieczenia).

## Uruchomienie lokalne

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Wdrozenie na Streamlit Community Cloud

1. Wrzuc ta paczke jako repozytorium na GitHub (caly folder, z
   `requirements.txt` w korzeniu repo).
2. Wejdz na [share.streamlit.io](https://share.streamlit.io), "New app".
3. Wskaz repo, branch, i plik glowny: `app.py`.
4. Deploy - Streamlit sam zainstaluje zaleznosci z `requirements.txt`.

Limit wielkosci wgrywanego pliku jest ustawiony na 500 MB w
`.streamlit/config.toml` (mozna zmniejszyc/zwiekszyc w zaleznosci od
wielkosci crawla).

## Struktura repo

```
app.py               - interfejs Streamlit (UI, wgrywanie plikow, przyciski)
linking_engine.py     - czysta logika regul (bez zaleznosci od Streamlit)
io_utils.py           - wczytywanie xlsx/csv/sitemap -> znormalizowane dane
export.py             - budowa dwoch plikow xlsx wyjsciowych
requirements.txt
.streamlit/config.toml
```

`linking_engine.py` i `export.py` nie zaleza od Streamlit, wiec mozna je
testowac/uzywac niezaleznie (np. w skrypcie CLI albo w notebooku).
