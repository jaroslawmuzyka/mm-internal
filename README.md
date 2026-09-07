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
     (przodkowie + biezaca strona jako ostatni element). Opcjonalnie: kolumna
     `Extract embeddings from page content` zasila dodatkowa warstwe
     `embedding_podobienstwo` - patrz sekcja Reguly ponizej. Bez niej
     narzedzie dziala normalnie, po prostu bez tej warstwy. Pelna instrukcja
     konfiguracji crawla: zakladka "Konfiguracja Screaming Frog" w samej
     aplikacji (pod "Jak to dziala?").
2. **Typ kazdej strony** jest ustalany po przynaleznosci do listy
   Kategorie/Filtry/Marki - NIE po wzorcu URL-a. Dzieki temu narzedzie nie
   jest zaszyte pod jedna platforme e-commerce. Wyjatek: strona zagniezdzona
   w breadcrumbie pod URL-em z listy Marki (dowolny przodek) dziedziczy typ
   "brand", nawet jesli sama nie jest wprost na tej liscie - typowy przypadek
   to podstrona marka+kategoria (np. `brand/dafi/dzbanki-filtrujace`), ktorej
   lista Marki nie wymienia osobno, bo wymienia tylko glowne strony marek.
3. **Zaznaczasz wykluczenia** (3xx, 4xx, noindex), ustawiasz suwaki **"Maksymalna
   roznica poziomow"** (domyslnie 1), **"Liczba propozycji z warstwy
   embedding_podobienstwo"** (domyslnie 5, max **10**, `0` = wylacz - patrz
   sekcja Reguly ponizej), opcjonalnie **"Sufiks do usuniecia z konca Anchor"**
   (np. stala nazwa sklepu na koncu H1) i klikasz "Uruchom analize".
4. **Dostajesz dwa pliki:**
   - `chmura_linkow_do_oceny.xlsx` - kandydaci rozbici na **3 zeszyty wg
     Source_Type** (kto linkuje): `Kandydaci do link. (kategorie)`, `(marki)`,
     `(filtry)`, plus arkusze pomocnicze (`L1_do_uzupelnienia`,
     `L2_pod_L1_bez_siostr`, `Pominiete_zbyt_glebokie`,
     `Marka_wykluczona_generyczna`, `Wszystkie_adresy_wejsciowe`, `Diagnostyka`)
     do recznej weryfikacji
   - `chmura_linkow_matryca_contentful.xlsx` - gotowa macierz: pierwsza
     kolumna `Source_URL`, kolejne `Link_1, Link_2, ...` z URL-ami do
     zalinkowania (ze WSZYSTKICH 3 zeszytow naraz - kategorie, marki i filtry
     jako Source_URL)

   W **obu plikach** komorka `Target_URL` / `Link_N` jest kolorowana wg
   pewnosci dopasowania - patrz "Skala pewnosci" w sekcji Reguly ponizej.

Jest tez opcjonalny "Krok 2": jesli recznie poprawisz plik "do oceny"
(usunites/dodasz wiersze w ktorejkolwiek z zakladek `Kandydaci do link.
(...)`), mozesz go wgrac ponownie, zeby zbudowac macierz Contentful TYLKO
z zaakceptowanych linkow (ze wszystkich 3 zakladek naraz).

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

Breadcrumb pobiera sie w Screaming Frog przez **Custom Extraction (XPath)**.
Pelna instrukcja konfiguracji crawla (tryb List Mode vs Spider, XPath dla
breadcrumba, ekstraktor embeddingow, co dokladnie eksportowac) jest **w samej
aplikacji** - zakladka "🕷️ Konfiguracja Screaming Frog" pod "Jak to dziala?".

**Kolumna embeddingu (opcjonalna):** musi sie nazywac dokladnie
`Extract embeddings from page content` (celowo TYLKO ta jedna nazwa, nie
dowolna kolumna zawierajaca slowo "embedding") - wartosc to string typu
`0.123, -0.456, ...` (z lub bez nawiasow klamrowych/kwadratowych), tak jak
eksportuje wtyczka do embeddingow w Screaming Frog. Zasila warstwe
`embedding_podobienstwo` (patrz Reguly ponizej). Brak tej kolumny niczego nie
psuje - reszta narzedzia dziala normalnie, po prostu bez tej warstwy.

**Sufiks w Anchor (opcjonalny):** jesli H1 (a wiec i Anchor w wynikach)
konczy sie stalym dopiskiem (np. nazwa sklepu/marki), mozna go obciac polem
"Sufiks do usuniecia z konca Anchor" w UI. Wartosc domyslna tego pola mozna
ustawic w Secrets (klucz `anchor_suffix_to_strip`) - dzieki temu nazwa
sklepu/marki nie musi byc zaszyta na sztywno w kodzie w tym (publicznym)
repozytorium.

**Wazna wlasciwosc, na ktorej opiera sie reguła kategoria→filtr:** strona z
parametrem/fasetem (np. `?brand=PHILIPS`) musi miec w crawlu **identyczny
breadcrumb** jak jej kategoria bazowa (czyli parametr URL nie zmienia
breadcrumba w DOM). Jesli Twoja strona zmienia breadcrumb dla stron
filtrowanych, ta reguła nie zadziala i trzeba ja dostosowac (np. wracajac do
mapowania przez osobny arkusz Main Category → Final URL).

## Reguly linkowania (dokladnie w tej kolejnosci trafiaja do wynikow)

1. **kategoria_podrzedna** - kazdy przodek z breadcrumba -> ta kategoria, na
   kazdym poziomie ponizej (nie tylko bezposrednie dzieci) - **ale tylko do
   limitu "Maksymalna roznica poziomow"** ustawionego suwakiem w UI (domyslnie
   `1` = tylko bezposrednie dzieci). Bez tego limitu szeroki dzial typu "AGD
   male" potrafil dostac 260 propozycji linkow naraz (2-4 poziomy w dol
   drzewa), co jest nie do wdrozenia bez recznego przycinania. Kandydaci
   odcieci limitem NIE znikaja - trafiaja do arkusza `Pominiete_zbyt_glebokie`
   do recznej decyzji, czy warto je jednak dodac.
2. **filtr_wlasny** - kazda kategoria -> jej WLASNA strona z filtrem/fasetem
   (dopasowana do kategorii bazowej po identycznej krotce breadcrumba -
   parametr URL nie zmienia breadcrumba w DOM).
3. **marka_orientacyjna_1seg(_UWAGA_KOLIZJA)** - dopasowanie kategorii do
   marki po 1 ostatnim segmencie nazwy w breadcrumbie (np. kategoria
   ".../Golenie" -> marka ".../Philips/Golenie"). Nazwy powtarzajace sie w
   >1 dziale sa oznaczone `_UWAGA_KOLIZJA` do recznej weryfikacji (wykrywane
   automatycznie z danych, nie na sztywno). Kategorie/marki, ktorych
   **ostatni segment breadcrumba jest slowem w pelni generycznym** (np.
   "Akcesoria" - samo w sobie nie niesie zadnej informacji o produkcie, wiec
   np. kategoria "Akcesoria" w dziale AGD bledasnie parowala z marka
   "DJI Akcesoria") sa **calkowicie wykluczone** z tego dopasowania - nie
   tylko oznaczone jako kolizja, tylko w ogole nie generuja tej propozycji.
   Lista takich slow: `GENERIC_LEAF_EXCLUSIONS_DEFAULT` w `linking_engine.py`
   (na start: `"Akcesoria"`). Takie kategorie trafiaja do arkusza
   `Marka_wykluczona_generyczna` - dalej dostaja normalnie linki ze
   wszystkich pozostalych regul, traca TYLKO to jedno dopasowanie.
4. **kategoria_tego_samego_poziomu** - siostry po wspolnym rodzicu w
   breadcrumbie. Pomijane, gdy rodzicem jest kategoria L1 (szeroki,
   "koszykowy" dzial typu "Dom i Ogrod") - jego dzieci nie sa tematycznie
   spokrewnione mimo wspolnego rodzica. Takie kategorie trafiaja do
   `L2_pod_L1_bez_siostr` do recznej selekcji.
5. **filtr_tego_samego_poziomu** - jak wyzej, ale target to strona z filtrem
   siostrzanej kategorii.
6. **Pozostale reguly** (bez ustalonego priorytetu miedzy soba):
   - **marka_precyzyjna_2seg** - dopasowanie kategorii do marki po **2**
     ostatnich segmentach nazwy w breadcrumbie (bardzo male ryzyko
     falszywych trafien - precyzyjniejsze niz `marka_orientacyjna_1seg`).
   - **filtr_podrzedny** - jak `kategoria_podrzedna`, ale target to strona z
     filtrem (ten sam limit "Maksymalna roznica poziomow" - odcieci
     kandydaci tez trafiaja do `Pominiete_zbyt_glebokie`).
   - **kategoria_nadrzedna / filtr_nadrzedny** - kazda kategoria na poziomie
     **L5 lub glebiej** ZAWSZE linkuje w gore do swojego bezposredniego
     rodzica (dokladnie 1 poziom wyzej, `Poziom_roznica = -1`,
     `kategoria_nadrzedna`) ORAZ do wszystkich filtrow nalezacych do tego
     rodzica (`filtr_nadrzedny`). Niezalezne od limitu "Maksymalna roznica
     poziomow" - to nie sa reguly "w dol", nie podlegaja odcinaniu. Cel:
     glebokie, waskie galezie drzewa (bez rodzenstwa) maja przynajmniej
     jeden pewny automatyczny link. Prog `PARENT_LINK_MIN_LEVEL` (domyslnie
     5) w `linking_engine.py`.
7. **embedding_podobienstwo** (zawsze na samym koncu) - DODATKOWA warstwa,
   niezalezna od breadcrumba, dziala na dowolnym typie strony (kategoria/
   filtr/marka). Wymaga kolumny `Extract embeddings from page content` w
   Internal HTML (patrz sekcja "Wymagany format" powyzej) - bez niej ta
   warstwa po prostu jest pusta, reszta narzedzia dziala normalnie. Dla
   kazdej strony z poprawnym embeddingiem liczy **cosine similarity** do
   wszystkich innych stron z embeddingiem i wybiera `embedding_top_n`
   (suwak w UI, domyslnie 5, **max 10**) najbardziej podobnych - ale
   **pomijajac pary, ktore juz maja rekomendacje z ktorejkolwiek innej
   reguly** (niezaleznie czy zostaly odciete limitem glebokosci). To
   gwarantuje, ze ta warstwa tylko DOKLADA nowe propozycje, nigdy nie
   duplikuje tego, co juz jest gdzie indziej. Wiersze tej reguly maja
   dodatkowo wypelniona kolumne `Podobienstwo` (0-1) - dla reszty regul jest
   pusta. Strony bez uzytecznego embeddingu (brak kolumny, blad parsowania,
   dlugosc wektora inna niz najczestsza w danych) trafiaja do metryki w
   Diagnostyce, reszta rekomendacji dla nich dziala normalnie.

### Linkowanie odwrocone - marka i filtr tez SA zrodlem

Kazda regula kategoria→filtr i kategoria→marka powyzej ma OD RAZU wygenerowana
odwrotnosc (dopisek `_odwrotnie` w nazwie), zeby strona marki/filtra tez mogla
byc `Source_URL`, nie tylko targetem:

- `filtr_wlasny_odwrotnie` / `filtr_tego_samego_poziomu_odwrotnie` /
  `filtr_podrzedny_odwrotnie` / `filtr_nadrzedny_odwrotnie` - filtr linkuje
  z powrotem do kategorii (odpowiednio: wlasnej bazowej, siostrzanej,
  przodka, bezposredniego rodzica).
- `marka_precyzyjna_2seg_odwrotnie` / `marka_orientacyjna_1seg_odwrotnie`
  (`_UWAGA_KOLIZJA_odwrotnie`) - marka linkuje z powrotem do dopasowanej
  kategorii.
- **`marka_do_filtru`** / **`filtr_do_marki`** - marka i filtr nie maja
  bezposredniego dopasowania po nazwie/URL (parametry filtra nie sa
  parsowane) - to dopasowanie **tranzytywne** przez wspolna kategorie
  bazowa: jesli marka pasuje do kategorii C (marka_precyzyjna_2seg /
  marka_orientacyjna_1seg), a C ma WLASNY filtr F (filtr_wlasny), to marka
  i F sa tez powiazane w obie strony. Patrz
  `linking_engine.build_brand_filter_candidates`.

`kategoria_nadrzedna` NIE ma odwrotnosci (bylby to zwykly duplikat
`kategoria_podrzedna` z limitem glebokosci 1) - kategoria jako Source_URL
jest juz w pelni pokryta pozostalymi regulami powyzej.

Kazdy kandydat laduje w zeszycie zgodnym ze swoim `Source_Type` - patrz
punkt 4 w sekcji "Jak to dziala" powyzej (`Kandydaci do link. (kategorie /
marki / filtry)`).

### Skala pewnosci (kolor komorki `Target_URL` / `Link_N`, w obu plikach xlsx)

Kolor odzwierciedla, na ile automatyczne dopasowanie jest strukturalnie pewne
(patrz `linking_engine.RULE_CONFIDENCE_TIERS` / `export.TIER_FILL_COLORS`) -
to inna os niz kolejnosc wyswietlania powyzej (tam liczy sie priorytet
wyswietlania, tu jakosc dopasowania):

| Kolor | Tier | Reguly |
|---|---|---|
| 🟩 Zielony | najpewniejsze - dokladne dopasowanie 1:1 po breadcrumbie | `kategoria_podrzedna`, `filtr_wlasny(_odwrotnie)`, `kategoria_nadrzedna`, `filtr_nadrzedny(_odwrotnie)`, `marka_precyzyjna_2seg(_odwrotnie)` |
| 🟨 Zolty | pewne, mniej bezposrednie (w tym dopasowanie tranzytywne) | `kategoria_tego_samego_poziomu`, `filtr_tego_samego_poziomu(_odwrotnie)`, `filtr_podrzedny(_odwrotnie)`, `marka_orientacyjna_1seg(_odwrotnie)`, `marka_do_filtru`, `filtr_do_marki` |
| 🟧 Pomaranczowy | wymaga uwagi - jawnie oznaczona kolizja nazw | `marka_orientacyjna_1seg_UWAGA_KOLIZJA(_odwrotnie)` |
| 🟥 Czerwony | najmniej pewne - czysto statystyczne podobienstwo tresci | `embedding_podobienstwo` |

Reguly `_odwrotnie` maja ten sam tier co ich oryginal - to dokladnie ten sam
fakt, tylko widziany z drugiej strony (marka/filtr jako Source zamiast
kategoria).

Jesli wiersz ma >1 regule naraz (scalone przez `merge_candidates`), liczy sie
NAJLEPSZY (najpewniejszy) tier wsrod nich.

Kazdy wiersz w wyniku ma kolumne `Poziom_roznica` (Target level − Source
level: 0 = ten sam poziom, dodatnia = ile poziomow nizej jest target) - w
Excelu mozna to od razu przefiltrowac/posortowac.

**Sortowanie wynikow:** najpierw `Source_URL` rosnaco (A -> Z), a w obrebie
tego samego `Source_URL` wg priorytetu reguly: `kategoria_podrzedna` ->
`filtr_wlasny` -> `marka_orientacyjna_1seg(_UWAGA_KOLIZJA)` ->
`kategoria_tego_samego_poziomu` -> `filtr_tego_samego_poziomu` -> pozostale
reguly (`marka_precyzyjna_2seg`, `filtr_podrzedny`, `kategoria_nadrzedna`,
`filtr_nadrzedny`) -> **`embedding_podobienstwo` zawsze na samym koncu**
(patrz sekcja Reguly powyzej - to najmniej pewna, "ostatnia deska ratunku"
warstwa). Dotyczy to zarowno kazdego z 3 zeszytow `Kandydaci do link. (...)`
/ `Pominiete_zbyt_glebokie`, jak i kolejnosci `Link_1, Link_2, ...` w
macierzy Contentful (patrz `linking_engine.RULE_SORT_ORDER`).

**Kategorie L1 (departament najwyzszego poziomu) nigdy nie wystepuja jako
`Source_URL` w zadnym z 3 zeszytow `Kandydaci do link. (...)` ani w
`Pominiete_zbyt_glebokie`** - wszystkie ich automatyczne propozycje, z kazdej
reguly (nie tylko kategoria_podrzedna), sa przenoszone do arkusza
`L1_do_uzupelnienia`. Chodzi o to, zeby L1 bylo w calosci recznie
przegladane w jednym miejscu, zamiast mieszac sie z reszta kandydatow. L1
bez zadnych automatycznych propozycji (np. brak dzieci w breadcrumbie) tez
sie tam pojawia, jako placeholder do recznego uzupelnienia.

## Kolejne kierunki linkowania

Silnik (`linking_engine.py`) dziala juz w obie strony: oprocz kategoria→*
generuje tez marka→(kategoria, filtr) i filtr→(kategoria, marka) - patrz
"Linkowanie odwrocone" powyzej. Zasada dodawania KOLEJNEGO kierunku (gdyby
kiedys byl potrzebny, np. marka<->marka) jest ta sama: skopiuj wlasciwa
`build_*` funkcje i zamien, ktory typ jest `Source`, a ktory `Target` - albo,
jak w `build_brand_filter_candidates`, dopisz odwrotnosc od razu obok
oryginalu w tej samej funkcji.

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
