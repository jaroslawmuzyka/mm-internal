"""
Chmura linkow - generator linkowania wewnetrznego (kategoria -> kategoria +
marka + filtr) na podstawie breadcrumba z crawla.

Wgrywasz 4 pliki (Kategorie / Filtry / Marki / Internal HTML), zaznaczasz
wykluczenia, klikasz "Uruchom analize" i dostajesz dwa pliki xlsx:
  1. do oceny (pelna lista kandydatow + arkusze pomocnicze)
  2. gotowa macierz do wgrania w Contentful (Source_URL + kolumny Link_N)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from linking_engine import build_pages, run_all_rules
from io_utils import read_url_list_file, read_internal_html_file
from export import build_review_workbook, build_contentful_matrix


st.set_page_config(page_title="Chmura linkow - linkowanie wewnetrzne", page_icon="🔗", layout="wide")


def _configured_password() -> str | None:
    """st.secrets rzuca wyjatek (nie zwraca None), jesli w ogole nie ma pliku
    secrets.toml - trzeba to zlapac, zeby pokazac czytelny blad zamiast stack trace."""
    try:
        return st.secrets.get("password")
    except Exception:
        return None


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
    "Kategoria → kategoria + marka + filtr, na podstawie breadcrumba z crawla. "
    "Kolejne kierunki (filtr→kategoria/marka, marka→kategoria/filtr) mozna dodac tym samym schematem."
)

with st.expander("Jak to dziala? (kliknij, zeby rozwinac)", expanded=False):
    st.markdown(
        """
1. **Kategorie / Filtry / Marki** - listy URL-i (xlsx z kolumna adresu, albo sitemapa XML).
   Typ kazdej strony jest ustalany po tym, na ktorej liscie sie znajduje - nie po wzorcu URL-a,
   wiec narzedzie dziala na dowolnej stronie e-commerce.
2. **Internal HTML** - eksport crawlera (np. Screaming Frog) z kolumnami: adres, status code,
   indexability, H1 oraz breadcrumb wyciagniety Custom Extraction (`Breadcrumb_URL 1..N`,
   `Breadcrumb_Name 1..N`, gdzie ostatnia kolumna Breadcrumb_Name to nazwa biezacej strony).
3. Zaznaczasz wykluczenia (3xx / 4xx / noindex) i klikasz **Uruchom analize**.
4. Dostajesz dwa pliki: pelna liste kandydatow do oceny oraz gotowa macierz do wgrania w Contentful.

**Reguly:** kategoria → kategoria (ten sam poziom + wszystko ponizej w drzewie, ale tylko do
limitu "Maksymalna roznica poziomow" ponizej - patrz suwak w sekcji 2), kategoria → filtr
(dopasowanie po identycznym breadcrumbie strony z parametrem, ten sam limit glebokosci dla
filtr_podrzedny), kategoria → marka (dopasowanie po 1 lub 2 ostatnich segmentach nazwy w
breadcrumbie; kategorie/marki z w pelni generycznym ostatnim segmentem, np. "Akcesoria", sa
calkowicie wykluczone z dopasowania 1-segmentowego - trafiaja do arkusza
Marka_wykluczona_generyczna zamiast do kandydatow).

**Co trafia do osobnych arkuszy zamiast do glownej listy kandydatow:**
- `Pominiete_zbyt_glebokie` - kandydaci kategoria_podrzedna/filtr_podrzedny odcieci limitem
  roznicy poziomow (nic nie ginie, tylko wymaga recznej decyzji, jesli chcesz je jednak dodac).
- `Marka_wykluczona_generyczna` - kategorie z generycznym ostatnim segmentem breadcrumba (np.
  "Akcesoria"), ktore nie dostaly automatycznej propozycji marki 1-segmentowej.
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

            result = run_all_rules(pages, max_level_diff=max_level_diff)
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

        rule_counts = pd.Series(
            [r for c in all_candidates for r in c["Rule"].split(" + ")]
        ).value_counts()
        st.bar_chart(rule_counts)

        st.subheader("Podglad kandydatow (pierwsze 200 wierszy)")
        st.dataframe(pd.DataFrame(all_candidates).head(200), use_container_width=True)

        review_bytes = build_review_workbook(
            all_candidates,
            result["l1_categories"],
            result["l2_under_l1_no_siblings"],
            result["no_base_found"],
            pages,
            cut_by_depth_candidates=result["cut_by_depth_candidates"],
            max_level_diff=result["max_level_diff"],
            brand_generic_excluded=result["brand_generic_excluded"],
        )
        contentful_bytes = build_contentful_matrix(all_candidates)

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
        "Jesli po pobraniu pliku 'do oceny' recznie usunales/dodales wiersze w zakladce "
        "**Kandydaci_linkowania**, wgraj tu poprawiony plik - macierz do Contentful zostanie "
        "zbudowana TYLKO z tych wierszy, ktore w nim zostaly."
    )
    edited_file = st.file_uploader(
        "Poprawiony plik (zakladka Kandydaci_linkowania)", type=["xlsx"], key="edited"
    )
    if edited_file is not None:
        try:
            df = pd.read_excel(edited_file, sheet_name="Kandydaci_linkowania")
        except Exception:
            df = pd.read_excel(edited_file)
        required_cols = {"Source_URL", "Target_URL"}
        if not required_cols.issubset(set(df.columns)):
            st.error(f"Plik musi zawierac kolumny: {', '.join(required_cols)}.")
        else:
            edited_rows = df.to_dict(orient="records")
            edited_contentful_bytes = build_contentful_matrix(edited_rows)
            st.success(f"Wczytano {len(edited_rows)} wierszy z poprawionego pliku.")
            st.download_button(
                "⬇️ Pobierz macierz Contentful (z poprawionego pliku)",
                data=edited_contentful_bytes,
                file_name="chmura_linkow_matryca_contentful_poprawiona.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
