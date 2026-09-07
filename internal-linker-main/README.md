# internal-linker
🧠 Aplikacja AI (Streamlit) do automatyzacji linkowania wewnętrznego. Wykorzystuje Embeddings i Cosine Similarity do łączenia powiązanych tematycznie podstron. Obsługuje rotację anchorów.

# 🧠 AI Internal Linker Strategy

Zaawansowane narzędzie SEO, które automatycznie generuje strategię linkowania wewnętrznego na podstawie semantycznego podobieństwa treści (embeddings). Aplikacja napisana w Pythonie (Streamlit), będąca szybszą i nowocześniejszą wersją skryptów PHP.

🔗 **[Uruchom aplikację na Streamlit Cloud](https://share.streamlit.io/)**

## 🚀 Jak to działa?

Narzędzie analizuje wektory (embeddings) wygenerowane dla Twoich podstron (np. przez OpenAI API lub inne modele NLP) i oblicza matematyczne podobieństwo (Cosine Similarity) między nimi.

1.  **Definiujesz segmenty:** Np. "Artykuły blogowe" (źródło) i "Produkty" (cel).
2.  **Analiza semantyczna:** Skrypt nie patrzy na słowa kluczowe, ale na znaczenie (wektor). Dzięki temu połączy artykuł o "awarii silnika" z produktem "olej silnikowy", nawet jeśli słowa się nie pokrywają.
3.  **Inteligentne Anchory:** Jeśli wgrasz plik z anchorami, system będzie je przydzielał cyklicznie (Round-Robin), dbając o różnorodność profilu linków.

## 📂 Format danych

### 1. Pliki Segmentów (.xlsx)
Każdy plik (np. `blog.xlsx`, `sklep.xlsx`) musi zawierać kolumny (nazwy mogą być w różnych wariantach, np. Title lub Tytuł):
*   **Address / URL:** Adres podstrony.
*   **Title / Title 1:** Tytuł strony.
*   **H1 / H1-1:** Nagłówek H1.
*   **Extract embeddings:** Ciąg liczbowy wektora (np. `0.0123, -0.5123, ...`).

### 2. Plik Anchorów (.xlsx) - Opcjonalnie
Służy do przypisywania konkretnych słów kluczowych do URLi.
*   **URL:** Adres docelowy.
*   **anchor:** Tekst zakotwiczenia.
*   *Jeden URL może mieć wiele wierszy z różnymi anchorami.*

## 🛠️ Instalacja lokalna

1.  Sklonuj repozytorium:
    ```bash
    git clone https://github.com/TWOJA_NAZWA/ai-linker.git
    ```
2.  Zainstaluj zależności:
    ```bash
    pip install -r requirements.txt
    ```
3.  Uruchom:
    ```bash
    streamlit run app.py
    ```

## ⚡ Technologie
*   **Streamlit:** Frontend i interakcja.
*   **NumPy:** Obliczenia macierzowe (działa błyskawicznie nawet przy tysiącach URLi).
*   **Pandas:** Obsługa danych Excel.
