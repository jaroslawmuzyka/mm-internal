import streamlit as st
import pandas as pd
import numpy as np
import io
from collections import defaultdict

# 1. Konfiguracja strony (musi być ZAWSZE pierwsza)
st.set_page_config(page_title="AI Internal Linker", page_icon="🔒", layout="wide")

# --- MODUŁ LOGOWANIA ---
def check_password():
    """Zwraca `True` jeśli użytkownik podał poprawne hasło."""

    def password_entered():
        """Sprawdza czy wpisane hasło zgadza się z tym w sekretach."""
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Nie przechowujemy hasła w postaci tekstowej
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # Pierwsze uruchomienie, pokaż pole do wpisania hasła
        st.text_input(
            "Podaj hasło dostępu:", 
            type="password", 
            on_change=password_entered, 
            key="password"
        )
        st.info("Dane potrzebne do zalogowania znajdują się w Monday. Kontakt: jaroslaw.muzyka@performance-group.pl")
        return False
    elif not st.session_state["password_correct"]:
        # Hasło błędne
        st.text_input(
            "Podaj hasło dostępu:", 
            type="password", 
            on_change=password_entered, 
            key="password"
        )
        st.info("Dane potrzebne do zalogowania znajdują się w Monday. Kontakt: jaroslaw.muzyka@performance-group.pl")
        st.error("😕 Niepoprawne hasło")
        return False
    else:
        # Hasło poprawne
        return True

if not check_password():
    st.stop()  # Zatrzymuje ładowanie reszty aplikacji, jeśli brak autoryzacji

# =========================================================
# WŁAŚCIWA APLIKACJA (Kod wykonuje się tylko po zalogowaniu)
# =========================================================

# --- Funkcje pomocnicze ---

def parse_embedding(emb_str):
    """Konwertuje ciąg tekstowy '0.123, 0.456...' na wektor numpy."""
    try:
        if isinstance(emb_str, str):
            # Usuwamy ewentualne nawiasy klamrowe czy spacje
            clean_str = emb_str.replace('[', '').replace(']', '').strip()
            return np.fromstring(clean_str, sep=',')
        return np.array([])
    except:
        return np.array([])

def cosine_similarity_matrix(source_vecs, target_vecs):
    """
    Oblicza podobieństwo cosinusowe między dwiema macierzami wektorów.
    Zwraca macierz o wymiarach (len(source), len(target)).
    """
    # Normalizacja wektorów (L2 norm)
    source_norm = np.linalg.norm(source_vecs, axis=1, keepdims=True)
    target_norm = np.linalg.norm(target_vecs, axis=1, keepdims=True)
    
    # Unikanie dzielenia przez zero
    source_norm[source_norm == 0] = 1
    target_norm[target_norm == 0] = 1
    
    # Obliczenie cosinusa: (A . B) / (|A| * |B|)
    similarity = np.dot(source_vecs, target_vecs.T) / np.dot(source_norm, target_norm.T)
    return similarity

def load_data(uploaded_file):
    """Wczytuje plik segmentu i sprawdza kolumny."""
    try:
        df = pd.read_excel(uploaded_file)
        
        # Mapowanie kolumn (elastyczne podejście do nazw)
        required_cols = {
            'address': ['Address', 'URL', 'Adres'],
            'title': ['Title 1', 'Title', 'Tytuł'],
            'h1': ['H1-1', 'H1', 'Nagłówek 1'],
            'emb': ['Extract embeddings', 'Embedding', 'Vector']
        }
        
        col_map = {}
        for key, possible_names in required_cols.items():
            found = False
            for name in possible_names:
                # Szukamy kolumny zawierającej daną nazwę (case insensitive)
                match = next((c for c in df.columns if name.lower() in c.lower()), None)
                if match:
                    col_map[key] = match
                    found = True
                    break
            if not found:
                return None, f"Brak wymaganej kolumny dla: {key} (szukano: {possible_names})"

        # Filtrowanie i parsowanie
        df = df.dropna(subset=[col_map['address'], col_map['emb']])
        
        # Parsowanie embeddingów do nowej kolumny
        df['parsed_emb'] = df[col_map['emb']].apply(parse_embedding)
        
        # Usuwanie błędnych embeddingów (pustych)
        df = df[df['parsed_emb'].apply(lambda x: x.size > 0)]
        
        # Ustandaryzowanie nazw kolumn do dalszej pracy
        clean_df = pd.DataFrame({
            'Address': df[col_map['address']].astype(str).str.strip(),
            'Title': df[col_map['title']].fillna('').astype(str),
            'H1': df[col_map['h1']].fillna('').astype(str),
            'Embedding': df['parsed_emb']
        })
        
        return clean_df, None
        
    except Exception as e:
        return None, str(e)

def load_anchors(uploaded_file):
    """Wczytuje plik anchorów i zwraca słownik {url: [anchor1, anchor2]}."""
    try:
        df = pd.read_excel(uploaded_file)
        # Szukanie kolumn
        url_col = next((c for c in df.columns if 'url' in c.lower()), None)
        anc_col = next((c for c in df.columns if 'anchor' in c.lower()), None)
        
        if not url_col or not anc_col:
            return None, "Nie znaleziono kolumn 'URL' lub 'anchor' w pliku anchorów."
            
        anchors_map = defaultdict(list)
        for _, row in df.iterrows():
            u = str(row[url_col]).strip()
            a = str(row[anc_col]).strip()
            if u and a:
                anchors_map[u].append(a)
        
        return anchors_map, None
    except Exception as e:
        return None, str(e)

# --- Interfejs Użytkownika ---

st.title("🔗 AI Internal Linking Strategy")
st.markdown("""
Narzędzie automatyzuje proces linkowania wewnętrznego poprzez analizę podobieństwa semantycznego między podstronami i dopasowanie zdefiniowanych anchorów.
""")

with st.expander("Przykład zastosowania:"):
    st.markdown("""
    Klient ma sklep internetowy, w którym mamy kategorie, wpisy blogowe, podział na marki. Chcemy powiązać te segmenty ze sobą linkując do podobnych produktów, adekwatnych wpisów na blogu czy marek. Po wgraniu wymaganych plików dostaniemy gotową strategię linkowania wewnętrznego.
    """)

with st.expander("ℹ️ Instrukcja i format plików (tutaj znajdziesz przykładowe pliki!)"):
    st.markdown("""
    ### Co to narzędzie potrafi?
    Wgraj embeddingi z kategorii, marek, bloga. Opcjonalnie wgraj anchory dla każdego z tych adresów URL - dobrane na sztywno np z nagłówka H1 (np nazwa kategorii/bloga) lub z fraz TOP1-TOP20 z Ahrefsa. 
    Skrypt przygotuje pełną strategie linkowania wewnętrznego wraz z anchorami - wszystko będziesz mógł wyeksportować do XLSX.

    ### Instrukcja:
    Skrypt na podstawie embeddingów oblicza podobieństwo (**cosine similarity**) pomiędzy stronami w określonych segmentach. Segment to grupa podstron np. kategorie, blog, porady czy rankingi.

    1. **Określ liczbę segmentów** (np. Kategoria, Kategoria do Kategorii, Marki, Blog = 4).
    2. **Określ liczbę linków wewnętrznych** (np. Po 5 linków per strona).
    3. **Wgraj pliki z embeddingami** do każdego segmentu z osobna – każdy plik musi zawierać kolumny: `Address` (URL podstrony), `Title 1`, `H1-1`, `Extract embeddings` (pobierzesz ze Screaming Frog).
    4. **Opcjonalnie wgraj plik z anchorami** z dwiema kolumnami: `URL` oraz `anchor`. Jednemu URL-owi może odpowiadać wiele anchorów.

    Po wczytaniu plików skrypt:
    * Oblicza **cosine similarity** pomiędzy embeddingami podstron z segmentu głównego a pozostałymi.
    * Dla każdego URL z segmentu głównego wybiera najlepsze propozycje linkowania.
    * Anchor dla linku dobierany jest z pliku, który załadujemy do skryptu.
    """)

    st.markdown("### Pobierz przykładowe pliki:")
    col_dl1, col_dl2, col_dl3, col_dl4 = st.columns(4)
    
    try:
        def get_binary_file_downloader_html(bin_file, file_label='File'):
            with open(bin_file, 'rb') as f:
                data = f.read()
            return data

        col_dl1.download_button("Pobierz przykładowy plik (kategorie)", get_binary_file_downloader_html("kategorie.xlsx"), "kategorie.xlsx")
        col_dl2.download_button("Pobierz przykładowy plik (brandy)", get_binary_file_downloader_html("brandy.xlsx"), "brandy.xlsx")
        col_dl3.download_button("Pobierz przykładowy plik (blogi)", get_binary_file_downloader_html("blogi.xlsx"), "blogi.xlsx")
        col_dl4.download_button("Pobierz przykładowy plik (anchory)", get_binary_file_downloader_html("anchory.xlsx"), "anchory.xlsx")
    except FileNotFoundError:
        st.warning("Nie znaleziono plików przykładowych w katalogu głównym.")

# --- Krok 1: Konfiguracja ---

col1, col2 = st.columns(2)
with col1:
    num_segments = st.number_input("Liczba segmentów (grup stron)", min_value=2, max_value=10, value=2)
with col2:
    limit_suggestions = st.number_input("Liczba linków na artykuł", min_value=1, max_value=20, value=3)

# Przechowywanie wgranych plików w sesji (aby nie znikały przy przeładowaniu)
if 'segment_files' not in st.session_state:
    st.session_state['segment_files'] = {}

st.subheader("📂 Wgraj pliki segmentów")

segments_data = [] # Lista słowników: {'name': str, 'df': DataFrame}
has_errors = False

for i in range(num_segments):
    c1, c2 = st.columns([1, 2])
    with c1:
        seg_name = st.text_input(f"Nazwa segmentu {i+1}", value=f"Segment {i+1}", key=f"name_{i}")
    with c2:
        seg_file = st.file_uploader(f"Plik dla: {seg_name}", type=['xlsx'], key=f"file_{i}")
    
    if seg_file:
        df, error = load_data(seg_file)
        if error:
            st.error(f"Błąd w pliku '{seg_name}': {error}")
            has_errors = True
        else:
            segments_data.append({'name': seg_name, 'df': df})
            st.success(f"✅ Wczytano {len(df)} adresów.")

st.subheader("⚓ Anchory (Opcjonalne)")
anchor_file = st.file_uploader("Plik z anchorami (.xlsx)", type=['xlsx'])

with st.expander("📖 Przypisywanie anchorów – przykład"):
    st.markdown("""
    Możesz uzupełnić wszystkie adresy URL konkretnym anchorem (np główną frazą lub nagłówkiem H1 linkowanego adresu) i dodatkowo dokleić wszystkie słowa kluczowe TOP1-TOP20 z Ahrefs dla tych adresów.

    Załóżmy, że w pliku **anchory.xlsx** znajdują się następujące dane:
    | URL | anchor |
    | :--- | :--- |
    | link1 | anchor1 |
    | link2 | anchor2 |
    | link3 | anchor3 |
    | link1 | anchor4 |
    | link1 | anchor5 |

    Dla adresu **link1** mamy więc 3 anchory: *anchor1*, *anchor4*, *anchor5*.

    Jeśli skrypt zaproponuje 5 linków prowadzących do **link1**, przypisze anchory w kolejności:
    1. anchor1
    2. anchor4
    3. anchor5
    4. anchor1 (wracamy do początku)
    5. anchor4

    W ten sposób masz pełną kontrolę nad tym, jakie anchory są przypisywane do każdego linku, a ich użycie jest maksymalnie zróżnicowane.
    """)
anchors_map = {}
if anchor_file:
    a_map, a_err = load_anchors(anchor_file)
    if a_err:
        st.error(f"Błąd anchorów: {a_err}")
    else:
        anchors_map = a_map
        st.success(f"✅ Wczytano anchory dla {len(anchors_map)} adresów URL.")

# --- Krok 2: Uruchomienie ---

if len(segments_data) == num_segments and not has_errors:
    st.divider()
    
    # Wybór segmentu głównego
    segment_names = [s['name'] for s in segments_data]
    main_seg_idx = st.selectbox("Wybierz Segment Główny (skąd linkujemy?):", range(len(segment_names)), format_func=lambda x: segment_names[x])
    
    if st.button("🚀 Generuj Strategię Linkowania", type="primary"):
        with st.spinner("Analizuję wektory i obliczam podobieństwo..."):
            
            main_segment = segments_data[main_seg_idx]
            other_segments = [s for i, s in enumerate(segments_data) if i != main_seg_idx]
            
            # Przygotowanie macierzy wektorów dla segmentu głównego
            # Stackujemy wektory do macierzy numpy (N x Dymensje)
            main_vecs = np.vstack(main_segment['df']['Embedding'].values)
            
            results = []
            usage_counts = defaultdict(int) # Do rotacji anchorów: {url_target: count}
            
            # Zbieramy wszystkie URL z "innych" segmentów, aby znaleźć nielinkowane
            all_target_urls = set()
            linked_target_urls = set()
            
            # Główna pętla przetwarzania
            # Iterujemy po innych segmentach
            for target_seg in other_segments:
                target_df = target_seg['df']
                target_vecs = np.vstack(target_df['Embedding'].values)
                
                # Dodajemy do puli wszystkich URLi
                all_target_urls.update(target_df['Address'].tolist())
                
                # Obliczamy podobieństwo WSZYSTKO vs WSZYSTKO dla tej pary segmentów
                # Wynik to macierz: wiersze = main_urls, kolumny = target_urls
                sim_matrix = cosine_similarity_matrix(main_vecs, target_vecs)
                
                for i in range(len(main_segment['df'])):
                    source_url = main_segment['df'].iloc[i]['Address']
                    
                    # Sortujemy wyniki dla tego wiersza (malejąco)
                    # argsort zwraca indeksy posortowane rosnąco, więc bierzemy od tyłu
                    scores = sim_matrix[i]
                    best_indices = np.argsort(scores)[::-1]
                    
                    # Bierzemy top N, pomijając ten sam URL (autolinkowanie)
                    count = 0
                    for target_idx in best_indices:
                        if count >= limit_suggestions:
                            break
                        
                        target_row = target_df.iloc[target_idx]
                        target_url = target_row['Address']
                        score = scores[target_idx]
                        
                        # Pomijamy autolinkowanie
                        if source_url == target_url:
                            continue
                            
                        # --- Logika Anchorów ---
                        # Sprawdzamy czy mamy zdefiniowane anchory dla TARGET URL
                        target_anchors = anchors_map.get(target_url, [])
                        
                        if target_anchors:
                            # Rotacja anchorów
                            anchor_idx = usage_counts[target_url] % len(target_anchors)
                            chosen_anchor = target_anchors[anchor_idx]
                        else:
                            chosen_anchor = "BRAK ANCHORA (użyj H1)"
                        
                        # Zwiększamy licznik użycia targetu
                        usage_counts[target_url] += 1
                        linked_target_urls.add(target_url)
                        
                        results.append({
                            'URL Źródłowy': source_url,
                            'Linkuje do': target_url,
                            'Segment Docelowy': target_seg['name'],
                            'Score': round(score, 4),
                            'Anchor': chosen_anchor,
                            'H1 Docelowy': target_row['H1']
                        })
                        count += 1

            # --- Generowanie wyników ---
            results_df = pd.DataFrame(results)
            if not results_df.empty:
                results_df = results_df.sort_values(by=['URL Źródłowy', 'Segment Docelowy', 'Score'], ascending=[True, True, False])
            
            # --- Generowanie nielinkowanych ---
            unlinked_list = list(all_target_urls - linked_target_urls)
            unlinked_df = pd.DataFrame(unlinked_list, columns=['Nielinkowany URL'])
            if not unlinked_df.empty:
                unlinked_df = unlinked_df.sort_values(by='Nielinkowany URL')
            
            # Wyświetlanie
            st.success("✅ Analiza zakończona!")
            
            tab1, tab2 = st.tabs(["📊 Propozycje Linkowania", "🚫 Nielinkowane Adresy"])
            
            with tab1:
                st.dataframe(results_df, use_container_width=True)
                
                # Eksport
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    results_df.to_excel(writer, index=False, sheet_name='Linki')
                
                st.download_button(
                    label="📥 Pobierz strategię (.xlsx)",
                    data=buffer.getvalue(),
                    file_name="strategia_linkowania.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
            with tab2:
                st.write(f"Znaleziono {len(unlinked_df)} adresów, które nie otrzymały żadnego linku.")
                st.dataframe(unlinked_df, use_container_width=True)
                
                buffer_un = io.BytesIO()
                with pd.ExcelWriter(buffer_un, engine='xlsxwriter') as writer:
                    unlinked_df.to_excel(writer, index=False, sheet_name='Nielinkowane')
                    
                st.download_button(
                    label="📥 Pobierz nielinkowane (.xlsx)",
                    data=buffer_un.getvalue(),
                    file_name="nielinkowane_urls.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

elif len(segments_data) < num_segments:
    st.info("Wgraj wszystkie wymagane pliki segmentów, aby rozpocząć.")
