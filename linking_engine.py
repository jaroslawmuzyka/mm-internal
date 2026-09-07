"""
Silnik regul linkowania wewnetrznego oparty o breadcrumb (kategoria -> kategoria
+ marka + filtr). Niezalezny od Streamlit/UI - moze byc importowany i testowany
osobno.

Model danych:
    - Wgrywasz 4 rzeczy: liste URL-i "Kategorie", liste URL-i "Filtry"
      (strony z parametrami/faseta), liste URL-i "Marki" oraz crawl
      "Internal HTML" (np. eksport Screaming Frog z Custom Extraction dla
      breadcrumba) z kolumnami Address / Status Code / Indexability / H1
      oraz Breadcrumb_URL 1..N / Breadcrumb_Name 1..N.
    - Typ kazdego URL-a (category / filtered_category / brand) ustalany jest
      po przynaleznosci do odpowiedniej listy (Kategorie/Filtry/Marki), NIE po
      wzorcu URL-a - dzieki temu narzedzie dziala na dowolnej stronie e-commerce.

Reguly (patrz build_* funkcje nizej) zostaly wypracowane i zweryfikowane na
realnych danych sklepu e-commerce:
    - kategoria_tego_samego_poziomu: siostry po wspolnym rodzicu w breadcrumbie
      (POMIJANE gdy rodzicem jest kategoria L1 - szeroki "koszykowy" dzial,
      ktorego dzieci nie sa tematycznie spokrewnione, tylko formalnie na tym
      samym poziomie)
    - kategoria_podrzedna: kazdy przodek z breadcrumba -> ta kategoria, na
      kazdym poziomie ponizej (nie tylko bezposrednie dzieci), ale TYLKO do
      glebokosci `max_level_diff` (patrz run_all_rules) - liczonej jako
      Poziom_roznica = Target_Level - Source_Level. Kandydaci ponizej tego
      limitu (np. link z szerokiego dzialu 3-4 poziomy w dol) nie trafiaja
      do glownej listy, tylko do osobnego arkusza pomocniczego (nic nie
      ginie bez sladu, tylko wymaga recznej decyzji)
    - filtr_wlasny / filtr_tego_samego_poziomu / filtr_podrzedny: jak wyzej,
      ale dla stron z filtrem, dopasowanych do kategorii bazowej po IDENTYCZNEJ
      krotce Breadcrumb_Name (bo parametr URL nie zmienia breadcrumba w DOM).
      filtr_podrzedny podlega temu samemu limitowi `max_level_diff` co
      kategoria_podrzedna (ten sam mechanizm, ten sam problem "eksplozji"
      linkow pod szerokimi dzialami)
    - marka_precyzyjna_2seg / marka_orientacyjna_1seg(_UWAGA_KOLIZJA):
      dopasowanie kategorii do marki po 2 (precyzyjne) lub 1 (orientacyjne,
      wiecej propozycji, ryzyko falszywych trafien dla nazw powtarzajacych
      sie w >1 dziale) ostatnich segmentach breadcrumba. Kategorie/marki,
      ktorych OSTATNI segment breadcrumba jest slowem w pelni generycznym
      (patrz GENERIC_LEAF_EXCLUSIONS_DEFAULT, np. "Akcesoria" - nie niesie
      zadnej informacji o produkcie) sa CALKOWICIE wykluczone z dopasowania
      1-segmentowego (nie tylko oznaczone jako kolizja) - trafiaja do
      osobnej listy `brand_generic_excluded` zamiast do kandydatow. Dopasowanie
      2-segmentowe (precyzyjne) tych kategorii/marek nie dotyczy.

Sortowanie wynikow (all_candidates, cut_by_depth_candidates oraz kolejnosc
Link_1/Link_2/... w macierzy Contentful - patrz export.build_contentful_matrix):
    1. Source_URL rosnaco (A -> Z)
    2. w obrebie tego samego Source_URL - priorytet reguly wg RULE_SORT_ORDER:
       kategoria_podrzedna, filtr_wlasny, kategoria_tego_samego_poziomu,
       filtr_tego_samego_poziomu, potem pozostale reguly (marka_*, filtr_podrzedny)
    3. Target_URL rosnaco (tiebreaker dla determinizmu)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------
# Model danych
# --------------------------------------------------------------------------

@dataclass
class PageRow:
    url: str
    status_code: Optional[int]
    indexability: Optional[str]
    h1: Optional[str]
    breadcrumb_urls: tuple      # przodkowie, w kolejnosci (L1..Ln-1)
    breadcrumb_names: tuple     # przodkowie + biezaca strona, w kolejnosci (L1..Ln)
    url_type: str                # category / filtered_category / brand / other

    @property
    def level(self) -> int:
        return len(self.breadcrumb_names)

    @property
    def level_label(self) -> str:
        return f"L{self.level}" if self.level > 0 else "L0 (brak breadcrumb)"

    @property
    def direct_parent_url(self) -> Optional[str]:
        return self.breadcrumb_urls[-1] if self.breadcrumb_urls else None

    def is_eligible(self, exclude_3xx: bool, exclude_4xx: bool, exclude_noindex: bool) -> bool:
        if self.level <= 0:
            return False
        if self.status_code is None:
            return False
        if exclude_noindex and self.indexability != "Indexable":
            return False
        if exclude_3xx and 300 <= self.status_code < 400:
            return False
        if exclude_4xx and 400 <= self.status_code < 500:
            return False
        if self.status_code >= 500:
            # Bledy serwera nigdy nie sa dobrym kandydatem na link
            return False
        return True


def _candidate(source: PageRow, target: PageRow, rule: str) -> dict:
    return {
        "Source_URL": source.url,
        "Target_URL": target.url,
        "Rule": rule,
        "Source_Level": source.level_label,
        "Source_Type": source.url_type,
        "Target_Type": target.url_type,
        "Target_Level": target.level_label,
        "Poziom_roznica": target.level - source.level,
        "Anchor": target.h1,
    }


# --------------------------------------------------------------------------
# Budowa PageRow z surowych danych (Internal HTML + listy URL-i)
# --------------------------------------------------------------------------

def build_pages(
    internal_html_rows: list[dict],
    category_urls: set,
    filtry_urls: set,
    marka_urls: set,
    exclude_3xx: bool = True,
    exclude_4xx: bool = True,
    exclude_noindex: bool = True,
) -> list[PageRow]:
    """
    internal_html_rows: lista dictow z kluczami (co najmniej):
        "url", "status_code", "indexability", "h1",
        "breadcrumb_urls" (tuple), "breadcrumb_names" (tuple)
    Zwraca liste PageRow, TYLKO dla URL-i ktore:
      - sa w co najmniej jednej z list (Kategorie/Filtry/Marki)
      - przechodza filtr eligibility (status/indexability wg checkboxow)
    Deduplikuje po URL (zachowuje wpis z najgłębszym breadcrumbem, na wypadek
    duplikatow w eksporcie crawlera).
    """
    seen: dict[str, PageRow] = {}
    for r in internal_html_rows:
        url = r.get("url")
        if not url:
            continue

        if url in category_urls:
            url_type = "category"
        elif url in filtry_urls:
            url_type = "filtered_category"
        elif url in marka_urls:
            url_type = "brand"
        else:
            continue  # URL spoza wszystkich list wejsciowych - pomijamy

        row = PageRow(
            url=url,
            status_code=r.get("status_code"),
            indexability=r.get("indexability"),
            h1=r.get("h1"),
            breadcrumb_urls=tuple(r.get("breadcrumb_urls") or ()),
            breadcrumb_names=tuple(r.get("breadcrumb_names") or ()),
            url_type=url_type,
        )
        existing = seen.get(url)
        if existing is None or row.level > existing.level:
            seen[url] = row

    pages = list(seen.values())
    return [p for p in pages if p.is_eligible(exclude_3xx, exclude_4xx, exclude_noindex)]


# --------------------------------------------------------------------------
# Reguła A: kategoria -> kategoria
# --------------------------------------------------------------------------

def build_category_hierarchy_candidates(
    pages: list[PageRow],
) -> tuple[list[dict], list[PageRow], list[PageRow]]:
    """
    Zwraca (kandydaci, kategorie_L1, kategorie_L2_pod_L1_bez_siostr).
    """
    page_by_url = {p.url: p for p in pages}
    categories = [p for p in pages if p.url_type == "category"]

    l1_categories = [p for p in categories if p.direct_parent_url is None]

    groups: dict[str, list[PageRow]] = defaultdict(list)
    for p in categories:
        if p.direct_parent_url is not None:
            groups[p.direct_parent_url].append(p)

    candidates = []
    l2_under_l1_no_siblings: list[PageRow] = []

    # -- ten sam poziom: siostry po wspólnym rodzicu (poza dziecmi L1) --
    for parent_url, members in groups.items():
        parent = page_by_url.get(parent_url)
        if parent is not None and parent.level == 1:
            l2_under_l1_no_siblings.extend(members)
            continue
        for source in members:
            for target in members:
                if source.url == target.url:
                    continue
                candidates.append(_candidate(source, target, "kategoria_tego_samego_poziomu"))

    # -- poniżej: każdy przodek z breadcrumba -> ta kategoria, na każdym poziomie --
    for descendant in categories:
        for ancestor_url in descendant.breadcrumb_urls:
            ancestor = page_by_url.get(ancestor_url)
            if ancestor is None or ancestor.url_type != "category":
                continue
            candidates.append(_candidate(ancestor, descendant, "kategoria_podrzedna"))

    return candidates, l1_categories, l2_under_l1_no_siblings


# --------------------------------------------------------------------------
# Reguła B: kategoria -> marka
# --------------------------------------------------------------------------

KNOWN_COLLISION_LEAVES_DEFAULT = {
    "Akcesoria",
    "Akcesoria AGD",
    "Akcesoria do lodówek",
    "Gaming",
    "Gry",
    "Telefony i smartwatche",
    "Zabawki",
}

# Ostatnie segmenty breadcrumba, ktore same w sobie nie niosa zadnej informacji
# o produkcie/dziale (np. "Akcesoria" pasuje jednoczesnie do "Akcesoria AGD" i
# do "DJI Akcesoria" - dopasowanie po samej nazwie jest bez sensu niezaleznie
# od tego, czy technicznie "koliduje" miedzy dzialami). Kategorie/marki z takim
# leafem sa CALKOWICIE wykluczone z dopasowania 1-segmentowego (nie tylko
# oznaczone jak w KNOWN_COLLISION_LEAVES_DEFAULT).
GENERIC_LEAF_EXCLUSIONS_DEFAULT = {
    "Akcesoria",
}


def find_collision_leaves(pages: list[PageRow]) -> set:
    """
    Wykrywa nazwy ostatniego segmentu breadcrumba kategorii, ktore powtarzaja
    sie pod >1 kategoria L1 (czyli sa "kolizyjne" dla dopasowania 1-segmentowego).
    Dziala na dowolnym zbiorze danych.
    """
    categories = [p for p in pages if p.url_type == "category" and p.breadcrumb_names]
    leaf_to_l1 = defaultdict(set)
    for c in categories:
        leaf_to_l1[c.breadcrumb_names[-1]].add(c.breadcrumb_names[0])
    return {leaf for leaf, l1s in leaf_to_l1.items() if len(l1s) > 1}


def build_category_brand_candidates(
    pages: list[PageRow],
    collision_leaves: set = None,
    generic_leaf_exclusions: set = None,
) -> tuple[list[dict], list[PageRow]]:
    """
    Zwraca (kandydaci, kategorie_wykluczone_generyczny_leaf).
    `generic_leaf_exclusions` - patrz GENERIC_LEAF_EXCLUSIONS_DEFAULT: kategorie
    i marki, ktorych ostatni segment breadcrumba jest na tej liscie, sa
    calkowicie pomijane przy dopasowaniu 1-segmentowym (nie trafiaja nawet do
    indeksu po stronie marki, ani nie sa sprawdzane po stronie kategorii).
    """
    if collision_leaves is None:
        collision_leaves = find_collision_leaves(pages) or KNOWN_COLLISION_LEAVES_DEFAULT
    if generic_leaf_exclusions is None:
        generic_leaf_exclusions = GENERIC_LEAF_EXCLUSIONS_DEFAULT

    categories_2 = [p for p in pages if p.url_type == "category" and p.level >= 2]
    categories_1_all = [p for p in pages if p.url_type == "category" and p.level >= 1]
    brands_2 = [p for p in pages if p.url_type == "brand" and p.level >= 2]
    brands_1_all = [p for p in pages if p.url_type == "brand" and p.level >= 1]

    categories_1 = [c for c in categories_1_all if c.breadcrumb_names[-1] not in generic_leaf_exclusions]
    brands_1 = [b for b in brands_1_all if b.breadcrumb_names[-1] not in generic_leaf_exclusions]
    generic_excluded_categories = [
        c for c in categories_1_all if c.breadcrumb_names[-1] in generic_leaf_exclusions
    ]

    candidates = []

    idx2: dict[tuple, list[PageRow]] = defaultdict(list)
    for b in brands_2:
        idx2[b.breadcrumb_names[-2:]].append(b)
    for c in categories_2:
        for b in idx2.get(c.breadcrumb_names[-2:], []):
            candidates.append(_candidate(c, b, "marka_precyzyjna_2seg"))

    idx1: dict[str, list[PageRow]] = defaultdict(list)
    for b in brands_1:
        idx1[b.breadcrumb_names[-1]].append(b)
    for c in categories_1:
        leaf = c.breadcrumb_names[-1]
        rule = (
            "marka_orientacyjna_1seg_UWAGA_KOLIZJA"
            if leaf in collision_leaves
            else "marka_orientacyjna_1seg"
        )
        for b in idx1.get(leaf, []):
            candidates.append(_candidate(c, b, rule))

    return candidates, generic_excluded_categories


# --------------------------------------------------------------------------
# Reguła C: kategoria -> filtr (dopasowanie po identycznej krotce breadcrumba)
# --------------------------------------------------------------------------

def build_category_filter_candidates(pages: list[PageRow]) -> tuple[list[dict], list[str]]:
    page_by_url = {p.url: p for p in pages}
    categories = [p for p in pages if p.url_type == "category"]

    cat_by_names: dict[tuple, PageRow] = {}
    ambiguous_names = set()
    for c in categories:
        if c.breadcrumb_names in cat_by_names:
            ambiguous_names.add(c.breadcrumb_names)
        else:
            cat_by_names[c.breadcrumb_names] = c
    for names in ambiguous_names:
        cat_by_names.pop(names, None)

    groups: dict[str, list[PageRow]] = defaultdict(list)
    for c in categories:
        if c.direct_parent_url is not None:
            groups[c.direct_parent_url].append(c)

    filtered = [p for p in pages if p.url_type == "filtered_category"]

    candidates = []
    no_base_found = []

    for f in filtered:
        base = cat_by_names.get(f.breadcrumb_names)
        if base is None:
            no_base_found.append(f.url)
            continue

        candidates.append(_candidate(base, f, "filtr_wlasny"))

        parent = page_by_url.get(base.direct_parent_url) if base.direct_parent_url else None
        if parent is not None and parent.level != 1:
            for sibling in groups.get(base.direct_parent_url, []):
                if sibling.url == base.url:
                    continue
                candidates.append(_candidate(sibling, f, "filtr_tego_samego_poziomu"))

        for ancestor_url in base.breadcrumb_urls:
            ancestor = page_by_url.get(ancestor_url)
            if ancestor is None or ancestor.url_type != "category":
                continue
            candidates.append(_candidate(ancestor, f, "filtr_podrzedny"))

    return candidates, no_base_found


# --------------------------------------------------------------------------
# Scalanie duplikatow (ta sama para source->target z >1 reguly)
# --------------------------------------------------------------------------

def merge_candidates(raw_candidates: list[dict]) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for c in raw_candidates:
        key = (c["Source_URL"], c["Target_URL"])
        if key not in merged:
            merged[key] = dict(c)
        else:
            existing_rules = merged[key]["Rule"].split(" + ")
            if c["Rule"] not in existing_rules:
                merged[key]["Rule"] = merged[key]["Rule"] + " + " + c["Rule"]
    return list(merged.values())


# Kolejnosc sortowania wynikow (w obrebie tego samego Source_URL) - patrz
# _rule_sort_key. Reguly spoza tej listy (marka_*, filtr_podrzedny) ida na
# koniec, w kolejnosci w jakiej i tak trafily do kandydatow.
RULE_SORT_ORDER = [
    "kategoria_podrzedna",
    "filtr_wlasny",
    "kategoria_tego_samego_poziomu",
    "filtr_tego_samego_poziomu",
]


def rule_sort_key(rule: str) -> int:
    """Rule moze byc scalone z >1 reguly ('regulaA + regulaB') - liczy sie
    najwyzszy priorytet (najnizszy indeks) wsrod scalonych regul."""
    sub_rules = rule.split(" + ")
    ranks = [RULE_SORT_ORDER.index(r) for r in sub_rules if r in RULE_SORT_ORDER]
    return min(ranks) if ranks else len(RULE_SORT_ORDER)


def _candidate_sort_key(c: dict) -> tuple:
    return (c["Source_URL"], rule_sort_key(c["Rule"]), c["Target_URL"])


# Reguly, do ktorych stosuje sie limit `max_level_diff` (patrz run_all_rules) -
# obie licza sie "w dol" po dowolnej liczbie poziomow drzewa kategorii, wiec
# obie moga eksplodowac do setek propozycji pod szerokim dzialem L1/L2.
DEPTH_LIMITED_RULES = {"kategoria_podrzedna", "filtr_podrzedny"}


def _split_by_depth_limit(candidates: list[dict], max_level_diff: int) -> tuple[list[dict], list[dict]]:
    within, cut = [], []
    for c in candidates:
        if c["Rule"] in DEPTH_LIMITED_RULES and c["Poziom_roznica"] > max_level_diff:
            cut.append(c)
        else:
            within.append(c)
    return within, cut


def run_all_rules(pages: list[PageRow], max_level_diff: int = 1) -> dict:
    """
    Uruchamia wszystkie reguly i zwraca slownik z surowymi/pomocniczymi wynikami.
    `max_level_diff`: maksymalna Poziom_roznica (Target_Level - Source_Level)
    dopuszczalna dla regul kategoria_podrzedna / filtr_podrzedny - patrz
    DEPTH_LIMITED_RULES. Kandydaci ponizej limitu nie trafiaja do
    `all_candidates`, tylko do `cut_by_depth_candidates` (osobny arkusz do
    recznej oceny, nic nie ginie bez sladu).
    """
    hierarchy_candidates, l1_categories, l2_under_l1_no_siblings = build_category_hierarchy_candidates(pages)
    collision_leaves = find_collision_leaves(pages)
    brand_candidates, brand_generic_excluded = build_category_brand_candidates(pages, collision_leaves)
    filter_candidates, no_base_found = build_category_filter_candidates(pages)

    hierarchy_within, hierarchy_cut = _split_by_depth_limit(hierarchy_candidates, max_level_diff)
    filter_within, filter_cut = _split_by_depth_limit(filter_candidates, max_level_diff)

    raw = hierarchy_within + brand_candidates + filter_within
    all_candidates = sorted(merge_candidates(raw), key=_candidate_sort_key)
    cut_by_depth_candidates = sorted(
        merge_candidates(hierarchy_cut + filter_cut),
        key=_candidate_sort_key,
    )

    return {
        "all_candidates": all_candidates,
        "cut_by_depth_candidates": cut_by_depth_candidates,
        "max_level_diff": max_level_diff,
        "hierarchy_candidates": hierarchy_candidates,
        "brand_candidates": brand_candidates,
        "brand_generic_excluded": brand_generic_excluded,
        "filter_candidates": filter_candidates,
        "l1_categories": l1_categories,
        "l2_under_l1_no_siblings": l2_under_l1_no_siblings,
        "no_base_found": no_base_found,
        "collision_leaves": collision_leaves,
    }
