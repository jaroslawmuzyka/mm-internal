"""
Budowanie plikow wyjsciowych (xlsx) z gotowych kandydatow do linkowania:
  1. Plik "do oceny" - kandydaci + arkusze pomocnicze do recznej weryfikacji:
     Kandydaci do link. (kategorie) / (marki) / (filtry) - all_candidates
     rozbite na 3 osobne zeszyty wg Source_Type (BEZ kategorii L1 jako
     Source_URL - patrz nizej), L1_do_uzupelnienia (WSZYSTKIE automatyczne
     propozycje, z kazdej reguly, dla kategorii L1 jako Source_URL -
     przeniesione tu zamiast do zeszytow kandydatow, plus placeholder dla L1
     bez zadnych propozycji), L2_pod_L1_bez_siostr, Pominiete_zbyt_glebokie
     (kandydaci odcieci limitem max_level_diff), Marka_wykluczona_generyczna
     (kategorie z generycznym leafem, np. "Akcesoria", pominiete przy
     dopasowaniu marka 1-segmentowe), Wszystkie_adresy_wejsciowe (audyt: co
     dokladnie wgrano do narzedzia - URL / Typ / Zrodlo / Status Code /
     Indexability z Internal HTML, z czerwonym podswietleniem status <> 200
     i indexability <> "Indexable" - do szybkiego wylapania problemow),
     Diagnostyka.
     Kolumna `Podobienstwo` (0-1) jest wypelniona tylko dla wierszy z reguly
     embedding_podobienstwo - pusta dla reszty. Komorka `Target_URL` jest
     kolorowana wg pewnosci dopasowania (patrz linking_engine.RULE_CONFIDENCE_TIERS
     / TIER_FILL_COLORS ponizej) - zielony = najpewniejsze (dokladne
     dopasowanie strukturalne), przez zolty/pomaranczowy, az po czerwony =
     embedding_podobienstwo (najmniej pewne, do recznej weryfikacji).
  2. Macierz "do Contentful" - tak samo jak plik "do oceny", rozbita na 3
     osobne zeszyty wg Source_Type (patrz CONTENTFUL_SHEET_NAMES): `Matryca
     Contentful (kategorie)` / `(marki)` / `(filtry)`. W kazdym zeszycie
     jeden wiersz na zrodlowy URL, w kolejnych kolumnach URL-e, do ktorych ten
     URL ma linkowac. Kazda komorka Link_N jest kolorowana ta sama skala co
     Target_URL w pliku "do oceny".
"""

from __future__ import annotations

import io
from collections import Counter, defaultdict
from typing import Callable

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from linking_engine import rule_confidence_tier, rule_sort_key

# Kolory wypelnienia komorki Target_URL / Link_N wg poziomu pewnosci linku -
# patrz linking_engine.RULE_CONFIDENCE_TIERS (0 = najpewniejsze -> ciemna
# zielen, ostatni tier = embedding_podobienstwo -> czerwony). Sama skala
# kolorow (nie przypisanie regul do tierow - to jest w linking_engine,
# blizej definicji regul):
#   0 ciemna zielen, 1 jasna zielen, 2 zolty, 3 pomaranczowy, 4 czerwony
TIER_FILL_COLORS = ["70AD47", "C6EFCE", "FFEB9C", "FCE4D6", "FFC7CE"]

# Kandydaci sa rozbici na 3 osobne zeszyty wg Source_Type - zeby bylo jasno
# widac, ktore strony (kategorie/marki/filtry) SAME linkuja do innych, nie
# tylko sa targetem. Kolejnosc = kolejnosc zeszytow w pliku.
SOURCE_TYPE_SHEET_NAMES = [
    ("category", "Kandydaci do link. (kategorie)"),
    ("brand", "Kandydaci do link. (marki)"),
    ("filtered_category", "Kandydaci do link. (filtry)"),
]

# To samo rozbicie co SOURCE_TYPE_SHEET_NAMES, ale dla macierzy Contentful
# (patrz build_contentful_matrix) - osobne nazwy, bo to osobny plik/kontekst.
CONTENTFUL_SHEET_NAMES = [
    ("category", "Matryca Contentful (kategorie)"),
    ("brand", "Matryca Contentful (marki)"),
    ("filtered_category", "Matryca Contentful (filtry)"),
]


def _tier_fill(rule: str) -> PatternFill:
    color = TIER_FILL_COLORS[rule_confidence_tier(rule)]
    return PatternFill("solid", fgColor=color)


def _autosize(ws):
    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(length + 2, 10), 80)


def _write_table(ws, headers, rows, color_target_by_rule=False, extra_style=None, on_progress=None):
    """
    `color_target_by_rule`: koloruje komorke Target_URL wg pewnosci reguly
    (patrz TIER_FILL_COLORS) - tylko dla wierszy, ktore maja niepuste Rule
    (pomija np. placeholdery L1 bez zadnej propozycji).
    `extra_style(ws, row_num, row)`: opcjonalny dodatkowy hook stylowania
    wiersza (np. kolorowanie Status Code / Indexability w arkuszu adresow
    wejsciowych) - wywolywany po dopisaniu wiersza.
    `on_progress(i)`: opcjonalny callback wywolywany co ~1/30 wierszy (i = ile
    juz zapisano W TYM arkuszu).

    WAZNE: numer aktualnie dopisanego wiersza liczymy sami (`i + 1`, bo naglowek
    to wiersz 1) - NIGDY nie uzywac `ws.max_row` w tej petli. `ws.max_row` w
    openpyxl skanuje WSZYSTKIE komorki arkusza (`max(self._cells)`), wiec
    wywolane raz na wiersz zmienia zapis z O(n) w O(n^2) - dla ~15k wierszy to
    bylo >50s zamiast <2s (znalezione profilerem, patrz historia commitow).
    """
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4472C4")
    target_col = headers.index("Target_URL") + 1 if "Target_URL" in headers else None
    total = len(rows)
    report_every = max(total // 30, 1)
    for i, row in enumerate(rows, start=1):
        ws.append([row.get(h, "") for h in headers])
        row_num = i + 1  # naglowek to wiersz 1, i-ty wiersz danych to i+1
        rule = row.get("Rule")
        if color_target_by_rule and target_col and rule:
            ws.cell(row=row_num, column=target_col).fill = _tier_fill(rule)
        if extra_style:
            extra_style(ws, row_num, row)
        if on_progress and (i % report_every == 0 or i == total):
            on_progress(i)
    ws.freeze_panes = "A2"
    _autosize(ws)


def build_review_workbook(
    all_candidates: list[dict],
    l1_categories: list,
    l2_under_l1_no_siblings: list,
    no_base_found: list,
    pages: list,
    distinct_filtry_main_categories: int | None = None,
    cut_by_depth_candidates: list[dict] | None = None,
    max_level_diff: int | None = None,
    brand_generic_excluded: list | None = None,
    l1_outbound_candidates: list[dict] | None = None,
    embedding_top_n: int | None = None,
    embedding_skipped: list[str] | None = None,
    all_input_urls: list[dict] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> bytes:
    """
    `progress(stage_label, rows_written_so_far, grand_total_rows)`: opcjonalny
    callback do paska postepu - wywolywany podczas zapisu arkuszy (~30x per
    arkusz). To TU realnie plynie czas dla duzych analiz (stylowanie komorka-
    po-komorce w openpyxl dla kilkunastu tysiecy wierszy potrafi zajac
    dziesiatki sekund) - nie w liczeniu regul/embeddingow, ktore trwa
    milisekundy nawet dla tysiecy stron.
    """
    cut_by_depth_candidates = cut_by_depth_candidates or []
    brand_generic_excluded = brand_generic_excluded or []
    l1_outbound_candidates = l1_outbound_candidates or []
    embedding_skipped = embedding_skipped or []
    all_input_urls = all_input_urls or []

    type_counts = Counter(p.url_type for p in pages)
    rule_counts = Counter()
    for c in all_candidates:
        for r in c["Rule"].split(" + "):
            rule_counts[r] += 1

    candidate_headers = [
        "Source_URL", "Target_URL", "Rule", "Source_Level",
        "Source_Type", "Target_Type", "Target_Level",
        "Poziom_roznica", "Anchor", "Podobienstwo",
    ]
    candidates_by_source_type: dict[str, list[dict]] = defaultdict(list)
    for c in all_candidates:
        candidates_by_source_type[c["Source_Type"]].append(c)

    l1_headers = [
        "Source_URL", "Target_URL", "Rule", "Source_Level",
        "Source_Type", "Target_Type", "Target_Level",
        "Poziom_roznica", "Anchor", "Podobienstwo", "Uwaga",
    ]
    NOTE_L1_HAS_CANDIDATES = (
        "Kategoria L1 (departament najwyzszego poziomu) - ta propozycja NIE trafia automatycznie "
        "do zadnego z zeszytow Kandydaci do link. (...) (celowo - L1 wymaga recznej weryfikacji "
        "w calosci tutaj)."
    )
    NOTE_L1_NO_CANDIDATES = (
        "BRAK KANDYDATOW - departament najwyzszego poziomu (brak rodzica w breadcrumbie) bez "
        "zadnej automatycznej propozycji linkowania (ani w dol, ani do siostr - brak rodzica "
        "wyklucza reguly oparte o wspolnego rodzica). Wymaga recznego wskazania kategorii "
        "komplementarnych/podobnych."
    )
    l1_by_source: dict[str, list[dict]] = defaultdict(list)
    for c in l1_outbound_candidates:
        l1_by_source[c["Source_URL"]].append(c)

    l1_rows = []
    for p in sorted(l1_categories, key=lambda x: x.url):
        items = l1_by_source.get(p.url, [])
        if not items:
            l1_rows.append({
                "Source_URL": p.url, "Target_URL": "", "Rule": "", "Source_Level": p.level_label,
                "Source_Type": "category", "Target_Type": "", "Target_Level": "",
                "Poziom_roznica": "", "Anchor": "", "Uwaga": NOTE_L1_NO_CANDIDATES,
            })
        else:
            for c in items:
                row = dict(c)
                row["Uwaga"] = NOTE_L1_HAS_CANDIDATES
                l1_rows.append(row)

    l2_headers = ["Source_URL", "Source_Type", "Source_Level", "Dzial_L1_rodzic", "Target_URL", "Target_Type", "Anchor", "Uwaga"]
    l2_rows = [
        {
            "Source_URL": p.url,
            "Source_Type": "category",
            "Source_Level": p.level_label,
            "Dzial_L1_rodzic": p.direct_parent_url,
            "Target_URL": "",
            "Target_Type": "",
            "Anchor": "",
            "Uwaga": "BRAK REGULY - rodzic to szeroki dzial L1, wiec inne kategorie pod tym samym "
                     "L1 NIE sa automatycznie traktowane jako spokrewnione 'siostry' - wymaga "
                     "recznej selekcji, jesli w ogole warto tu linkowac",
        }
        for p in sorted(l2_under_l1_no_siblings, key=lambda x: (x.direct_parent_url or "", x.url))
    ]

    depth_headers = [
        "Source_URL", "Target_URL", "Rule", "Source_Level",
        "Source_Type", "Target_Type", "Target_Level",
        "Poziom_roznica", "Anchor", "Podobienstwo",
    ]

    generic_headers = ["Source_URL", "Source_Type", "Source_Level", "Anchor", "Uwaga"]
    generic_rows = [
        {
            "Source_URL": p.url,
            "Source_Type": "category",
            "Source_Level": p.level_label,
            "Anchor": p.h1,
            "Uwaga": "BRAK AUTOMATYCZNEJ PROPOZYCJI MARKA (1-segmentowe) - ostatni segment "
                     "breadcrumba jest slowem w pelni generycznym (np. \"Akcesoria\"), samo w "
                     "sobie nie niesie informacji o produkcie. Kategoria dalej dostaje normalnie "
                     "linki z regul kategoria_podrzedna / kategoria_tego_samego_poziomu / filtr_* "
                     "oraz z precyzyjnego dopasowania marka 2-segmentowego, jesli pasuje - traci "
                     "TYLKO orientacyjne dopasowanie marki po samej nazwie. Do recznej oceny, "
                     "jesli warto tu dodac link do marki.",
        }
        for p in sorted(brand_generic_excluded, key=lambda x: x.url)
    ]

    input_url_headers = ["URL", "Typ", "Źródło", "Status Code", "Indexability"]
    input_url_rows = sorted(all_input_urls, key=lambda r: (r.get("Typ", ""), r.get("URL", "")))
    status_col = input_url_headers.index("Status Code") + 1
    indexability_col = input_url_headers.index("Indexability") + 1
    bad_fill = PatternFill("solid", fgColor=TIER_FILL_COLORS[3])  # czerwony - do rzucenia sie w oczy

    def _style_input_url_row(ws, row_num, row):
        if row.get("Status Code") not in (None, "", 200):
            ws.cell(row=row_num, column=status_col).fill = bad_fill
        if row.get("Indexability") not in (None, "", "Indexable"):
            ws.cell(row=row_num, column=indexability_col).fill = bad_fill

    # Plan zapisu - wszystkie listy wierszy juz policzone, wiec grand_total
    # (mianownik paska postepu) jest znany PRZED zapisem pierwszego arkusza.
    sheet_plan = [
        (SOURCE_TYPE_SHEET_NAMES[0][1], candidate_headers, candidates_by_source_type.get("category", []), True, None),
        (SOURCE_TYPE_SHEET_NAMES[1][1], candidate_headers, candidates_by_source_type.get("brand", []), True, None),
        (SOURCE_TYPE_SHEET_NAMES[2][1], candidate_headers, candidates_by_source_type.get("filtered_category", []), True, None),
        ("L1_do_uzupelnienia", l1_headers, l1_rows, True, None),
        ("L2_pod_L1_bez_siostr", l2_headers, l2_rows, False, None),
        ("Pominiete_zbyt_glebokie", depth_headers, cut_by_depth_candidates, True, None),
        ("Marka_wykluczona_generyczna", generic_headers, generic_rows, False, None),
        ("Wszystkie_adresy_wejsciowe", input_url_headers, input_url_rows, False, _style_input_url_row),
    ]
    grand_total = sum(len(rows) for _, _, rows, _, _ in sheet_plan) or 1

    wb = openpyxl.Workbook()
    written_so_far = 0
    for i, (sheet_name, headers, rows, color_flag, extra_style) in enumerate(sheet_plan):
        ws = wb.active if i == 0 else wb.create_sheet(sheet_name)
        if i == 0:
            ws.title = sheet_name

        on_progress = None
        if progress:
            offset = written_so_far

            def on_progress(i_in_sheet, _offset=offset, _sheet=sheet_name):
                progress(_sheet, _offset + i_in_sheet, grand_total)

        _write_table(ws, headers, rows, color_target_by_rule=color_flag, extra_style=extra_style, on_progress=on_progress)
        if sheet_name == "Pominiete_zbyt_glebokie" and not rows:
            ws.append(["(brak - wszyscy kandydaci miesca sie w limicie glebokosci)"])
        written_so_far += len(rows)

    ws3 = wb.create_sheet("Diagnostyka")
    ws3.append(["Metryka", "Wartosc"])
    for c in ws3[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4472C4")
    diag_rows = [
        ("Wygenerowano kandydatow - unikalne pary source->target (po scaleniu duplikatow)", len(all_candidates)),
        ("", ""),
        ("Rozbicie wedlug reguly (wiersz moze liczyc sie w >1 regule):", ""),
    ]
    for rule, cnt in sorted(rule_counts.items(), key=lambda x: -x[1]):
        diag_rows.append((f"  - {rule}", cnt))
    diag_rows += [
        ("", ""),
        ("Kategorie L1 (brak automatycznych sasiadow tego samego poziomu)", len(l1_categories)),
        ("Propozycje z Source_URL = kategoria L1 (przeniesione do L1_do_uzupelnienia)", len(l1_outbound_candidates)),
        ("Kategorie L2 pod L1 (brak automatycznych 'siostr' - szeroki dzial)", len(l2_under_l1_no_siblings)),
        ("Strony filtrowane bez dopasowanej kategorii bazowej po breadcrumbie", len(no_base_found)),
        ("", ""),
        ("Limit roznicy poziomow dla kategoria_podrzedna / filtr_podrzedny (Poziom_roznica)", max_level_diff),
        ("Kandydaci odcieci limitem glebokosci (patrz arkusz Pominiete_zbyt_glebokie)", len(cut_by_depth_candidates)),
        ("Kategorie wykluczone z dopasowania marka 1-segmentowe - nazwa generyczna "
         "(patrz arkusz Marka_wykluczona_generyczna)", len(brand_generic_excluded)),
        ("", ""),
        ("Warstwa embedding_podobienstwo - liczba propozycji per strona (embedding_top_n)", embedding_top_n),
        ("Strony pominiete w warstwie embedding_podobienstwo (brak/zly/samotny embedding)", len(embedding_skipped)),
        ("", ""),
        ("Strony wziete pod uwage razem (po filtrach 3xx/4xx/noindex)", len(pages)),
        ("  - typu category", type_counts.get("category", 0)),
        ("  - typu brand", type_counts.get("brand", 0)),
        ("  - typu filtered_category", type_counts.get("filtered_category", 0)),
        ("", ""),
        ("Adresy wejsciowe razem - listy Kategorie/Filtry/Marki, przed dopasowaniem do "
         "crawla (patrz arkusz Wszystkie_adresy_wejsciowe)", len(all_input_urls)),
    ]
    for label, val in diag_rows:
        ws3.append([label, val])
    if no_base_found:
        ws3.append(["", ""])
        ws3.append(["Strony filtrowane bez dopasowanej kategorii bazowej (do sprawdzenia):", ""])
        for u in no_base_found:
            ws3.append([u, ""])
    _autosize(ws3)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_contentful_matrix(
    candidate_rows: list[dict],
    max_links: int | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> bytes:
    """
    candidate_rows: lista dictow z co najmniej kluczami Source_URL, Target_URL
    (opcjonalnie Poziom_roznica, Rule, Podobienstwo - uzywane do sortowania
    kolejnosci linkow w obrebie jednego zrodla: najpierw wg priorytetu reguly -
    patrz linking_engine.RULE_SORT_ORDER (kategoria_podrzedna, filtr_wlasny,
    kategoria_tego_samego_poziomu, filtr_tego_samego_poziomu, potem reszta),
    potem - dla wierszy embedding_podobienstwo - malejaco po Podobienstwo
    (najlepsze dopasowanie pierwsze), potem Poziom_roznica, na koniec
    Target_URL).

    `progress(stage_label, rows_written_so_far, grand_total_rows)`: opcjonalny
    callback do paska postepu, jak w build_review_workbook.

    Zwraca xlsx z 3 zeszytami wg Source_Type - patrz CONTENTFUL_SHEET_NAMES
    (`Matryca Contentful (kategorie)` / `(marki)` / `(filtry)`) - w kazdym
    pierwsza kolumna Source_URL, kolejne Link_1, Link_2, ...
    """
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in candidate_rows:
        src = r.get("Source_URL")
        tgt = r.get("Target_URL")
        if not src or not tgt:
            continue
        by_source[src].append(r)

    def sort_key(r):
        podobienstwo = r.get("Podobienstwo")
        podobienstwo_rank = -podobienstwo if isinstance(podobienstwo, (int, float)) else 0
        return (
            rule_sort_key(str(r.get("Rule") or "")),
            podobienstwo_rank,  # embedding_podobienstwo: najlepsze dopasowanie (najwyzszy score) pierwsze
            r.get("Poziom_roznica") if isinstance(r.get("Poziom_roznica"), (int, float)) else 0,
            str(r.get("Target_URL") or ""),
        )

    rows_by_source_type: dict[str, list[tuple]] = defaultdict(list)
    for src, items in by_source.items():
        source_type = items[0].get("Source_Type") or "category"
        items_sorted = sorted(items, key=sort_key)
        targets = []  # lista (Target_URL, Rule) - Rule uzywana tylko do koloru komorki
        seen = set()
        for it in items_sorted:
            t = it["Target_URL"]
            if t in seen:
                continue
            seen.add(t)
            targets.append((t, it.get("Rule") or ""))
        if max_links is not None:
            targets = targets[:max_links]
        rows_by_source_type[source_type].append((src, targets))

    sheet_plan = []
    for source_type, sheet_name in CONTENTFUL_SHEET_NAMES:
        rows_out_sorted = sorted(rows_by_source_type.get(source_type, []), key=lambda x: x[0])
        max_count = max((len(targets) for _, targets in rows_out_sorted), default=0)
        sheet_plan.append((sheet_name, rows_out_sorted, max_count))
    grand_total = sum(len(rows) for _, rows, _ in sheet_plan) or 1

    wb = openpyxl.Workbook()
    written_so_far = 0
    for i, (sheet_name, rows_out_sorted, max_count) in enumerate(sheet_plan):
        ws = wb.active if i == 0 else wb.create_sheet(sheet_name)
        if i == 0:
            ws.title = sheet_name

        headers = ["Source_URL"] + [f"Link_{j+1}" for j in range(max_count)]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="4472C4")

        offset = written_so_far
        total_in_sheet = len(rows_out_sorted)
        report_every = max(total_in_sheet // 30, 1)
        for j, (src, targets) in enumerate(rows_out_sorted, start=1):
            ws.append([src] + [t for t, _ in targets])
            row_num = j + 1  # naglowek to wiersz 1, j-ty wiersz danych to j+1
            for k, (_, rule) in enumerate(targets):
                if rule:
                    ws.cell(row=row_num, column=2 + k).fill = _tier_fill(rule)
            if progress and (j % report_every == 0 or j == total_in_sheet):
                progress(sheet_name, offset + j, grand_total)
        ws.freeze_panes = "A2"
        _autosize(ws)
        written_so_far += total_in_sheet

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
