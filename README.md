# Czytnik wydań AI

Odczyt **daty** i **numeru wydania** ze skanów czasopism za pomocą modeli multimodalnych
przez OpenRouter. Bez OCR i bez Tesseracta — obraz trafia prosto do modelu, który czyta
winietę tak, jak czyta ją człowiek.

Działa uniwersalnie: France Football, Placar, Kicker, World Soccer i dowolny inny tytuł,
w dowolnym języku i dowolnej dekadzie. Prompt nie zawiera nazwy żadnej gazety.

Obsługiwane wejście: `.pdf` (czytana jest pierwsza strona) oraz `.jpg .jpeg .png .bmp
.tif .tiff .webp` (pojedyncze strony).

## Format nazw wyjściowych

```
France Football - 1958-06-17 - 000638.pdf              ← całe wydanie (PDF)
France Football - 1958-06-17 - 000638 - 015.jpg        ← pojedyncza strona (obraz)
France Football - 1958-06-17 - 000315-bis.pdf          ← wydanie specjalne
```

- data: `RRRR-MM-DD`
- numer wydania: dopełniony zerami do 6 cyfr; dopisek (`bis`, `special`, `hs`…) po myślniku
- numer strony: dopełniony do 3 cyfr, odczytywany ze skanu przez model

## Instalacja

```bash
pip install -r requirements.txt
python app.py
```

Wymaga Pythona 3.10+ z modułem `tkinter` (na Ubuntu/Debianie: `sudo apt install python3-tk`;
w Windows i macOS jest w standardowej instalacji).

## Jak używać

1. **Zakładka „API i model"** — wklej klucz z openrouter.ai, kliknij *Zapisz klucz*, potem
   *Testuj klucz i limity* (pokaże, ile darmowych zapytań zostało Ci na dziś).
   Następnie *Pobierz listę modeli* — lista jest ograniczona do modeli przyjmujących obrazy.
   Zaznacz model i kliknij *Użyj zaznaczonego modelu*.
2. **Zakładka „Pliki i wyniki"** — *Dodaj folder* (opcjonalnie z podfolderami) lub
   *Dodaj pliki*. Zaznaczaj pojedynczo (klik w kwadracik), grupowo (Ctrl/Shift + spacja)
   albo przyciskami *Zaznacz wszystko / Odwróć*.
3. **Odczytaj daty za pomocą AI** — program pokaże, ile zapytań to zajmie, i ruszy.
   Możesz w każdej chwili wcisnąć *Pauza* albo *Stop*.
4. **Sprawdź spójność** — sito błędów opisane niżej. Podejrzane wiersze zostaną podświetlone.
5. **Zmień nazwy** — dopiero teraz program dotyka dysku. Operacja jest logowana i odwracalna
   przyciskiem *Cofnij zmianę nazw*.

## Limity OpenRoutera, o których trzeba wiedzieć

Modele z sufiksem `:free` mają 20 zapytań na minutę oraz **50 zapytań dziennie**, a po
jednorazowym zakupie 10 kredytów — **1000 dziennie**. Limity są liczone globalnie na konto,
więc zakładanie dodatkowych kluczy nic nie daje.

Przy 2000 plików i domyślnym batchu 5 skanów na zapytanie wychodzi **400 zapytań**, czyli
jeden dzień pracy po doładowaniu konta. Bez doładowania byłoby to osiem dni.

Program sam pilnuje 20 zapytań na minutę i ponawia z rosnącym odstępem, honorując nagłówek
`Retry-After`.

## Dlaczego batch po 5 skanów

W jednym zapytaniu jedzie kilka obrazów naraz — limit liczy zapytania, nie obrazy. Obrazy
idą w znanej kolejności, a model zwraca tablicę JSON z polem `index`, które program mapuje
z powrotem na nazwy plików. Nie trzeba więc niczego nadpisywać na stronach.

Im większy batch, tym większe ryzyko, że model przesunie odpowiedzi albo zwróci ich za mało.
Program to wykrywa (sprawdza długość tablicy i ciągłość indeksów) i **cały taki batch
ponawia pojedynczo**, zamiast zapisać przesunięte dane. Wartość 5 to rozsądny kompromis;
można ją zmienić w zakładce API.

## Sita jakości

**Walidacja krzyżowa numer ↔ data.** Periodyk wydaje numery liniowo w czasie. Program
dopasowuje odporną prostą (estymator Theila-Sena) do par (data, numer) osobno dla każdego
tytułu i oznacza rekordy mocno odstające od tej prostej. To wyłapuje literówki modelu
lepiej niż jego własne pole „pewność" — numer 8632 wśród 620…650 rzuca się w oczy
natychmiast. Wymaga co najmniej 6 poprawnie odczytanych rekordów danego tytułu.

**Uzupełnianie brakującego roku.** Wiele okładek podaje tylko dzień i miesiąc. Model ma
wtedy zakaz zgadywania roku (zwraca `null`), a program dolicza go z ciągu numerów, rozpoznając
nazwy miesięcy po francusku, niemiecku, portugalsku, angielsku, włosku, hiszpańsku i polsku.
Sprawdza też sąsiednie lata, żeby poprawnie obsłużyć numery na przełomie grudnia i stycznia.
Każdy tak uzupełniony rekord dostaje adnotację w kolumnie „Uwagi".

**Zakaz zgadywania.** Prompt wymaga `null` wszędzie tam, gdzie czegoś nie widać, oraz
pierwszej daty z zakresu („du 15 au 21 mars"). Zabrania też amerykańskiej kolejności
miesiąc/dzień i każe ufać nazwie miesiąca, gdy jest wydrukowana.

**Cache po odcisku pliku.** Wynik każdego odczytu ląduje w SQLite pod kluczem
(odcisk pliku, model). Przerwany przebieg wznawia się bez zużywania limitu, a zmiana modelu
wymusza ponowny odczyt.

**Podgląd i ręczna poprawka.** Panel po prawej pokazuje górny pasek strony — ten sam
fragment, który czytał model — więc 2000 rekordów da się zweryfikować wzrokiem. Podwójny
klik w wiersz otwiera okno edycji.

## Weryfikacja niepewnych odczytów

Przycisk **Weryfikuj (N)** nad tabelą (albo Ctrl+W) otwiera okno z dużym podglądem strony
i polami do poprawki — po kolei dla każdego rekordu, który wymaga sprawdzenia: błąd, brak
danych, pewność poniżej 0,80, rekord podejrzany w walidacji krzyżowej albo bez nowej nazwy.
Te same rekordy pokazuje filtr **„do sprawdzenia”**. Gdy w tabeli podświetlisz kilka
wierszy, weryfikacja obejmie właśnie je.

| Klawisz | Działanie |
|---|---|
| Enter | zapisz i przejdź do następnego |
| Esc / ↓ | pomiń |
| ↑ | poprzedni rekord |
| PageUp / PageDown | poprzednia / następna strona PDF-a |
| kółko myszy, przeciąganie | powiększenie, przesuwanie podglądu |

Datę można wpisać jako `RRRR-MM-DD` albo `DD.MM.RRRR`. Przycisk *Otwórz w przeglądarce
PDF* otwiera plik w programie systemowym.

## Import raportów CSV z innego programu

*Plik → Importuj raporty CSV…* wczytuje raporty dat (np. `raport_dat.csv`) — jeden albo
wiele naraz, nazwy dowolne. Kolejne importy dokładają wiedzę do poprzednich.
Wymagana jest kolumna `data_koncowa` oraz co najmniej jedna z `skrot`, `sciezka`, `plik`.

Pliki z listy są dopasowywane do wierszy raportu po kolei:
1. **skrót pliku** (`skrot`, np. `h2:…`) — liczony tak samo jak w programie, który zrobił
   raport (MD5 z rozmiaru + 256 kB początku + 256 kB końca). Działa mimo zmiany nazwy
   i przeniesienia pliku,
2. pełna ścieżka,
3. nazwa pliku + rozmiar.

Co się dzieje z dopasowanym rekordem:
- wiersz **pewny** → data, numer (z dopiskiem `bis`/`special`) i tytuł trafiają do
  rekordu, status „z raportu”, rekord zostaje odznaczony, więc nie idzie już do AI,
- wiersz **wątpliwy / brak** → podpowiedź w uwagach, rekord trafia do „do sprawdzenia”,
- **raporty podają różne dane** dla tego samego pliku → rekord trafia do „do sprawdzenia”,
- rekord **poprawiony ręcznie** nie jest nadpisywany.

Skróty liczone są w tle i zapamiętywane w sesji, więc kolejny import ich nie przelicza.

## Bezpieczeństwo danych

Klucz API leży w `~/.gazeta_ai/config.json` z prawami 600 i **nie trafia do pliku sesji** —
sesję można spokojnie komuś wysłać. Logi zmian nazw są w `~/.gazeta_ai/renames/`.
Zmiana nazw to jedyna operacja dotykająca Twoich plików; odczyt AI nigdy ich nie modyfikuje.

## Skróty klawiszowe

| Skrót | Działanie |
|---|---|
| Ctrl+N | Nowa sesja |
| Ctrl+O | Otwórz sesję |
| Ctrl+S | Zapisz sesję |
| Ctrl+Shift+S | Zapisz sesję jako… |
| Spacja | Przełącz zaznaczenie podświetlonych wierszy |
| F5 | Odśwież tabelę |
| Ctrl+W | Weryfikacja niepewnych odczytów |

## Eksport

*Plik → Eksport do CSV / do Excela* zapisuje wszystkie kolumny, jakie program przechowuje:
obok daty i numeru także język, datę w oryginalnym brzmieniu, nazwę miesiąca, informację
czy rok był nadrukowany, pewność, użyty model, status, uwagi i surową odpowiedź modelu.
Ta ostatnia pozwala ustalić, czy błąd zawinił model, czy parser.

## Struktura

| Plik | Rola |
|---|---|
| `app.py` | GUI: tabela, zakładki, paski postępu, sesje, dialogi |
| `report_import.py` | import raportów CSV z innego programu, skrót pliku `h2` |
| `verify.py` | okno weryfikacji: duży podgląd strony + poprawka ręczna |
| `worker.py` | wątek roboczy: batche, cache, pauza/stop, zdarzenia do GUI |
| `openrouter_client.py` | HTTP, lista modeli, limit zapytań, ponawianie |
| `prompt.py` | uniwersalny prompt i odporny parser JSON |
| `render.py` | PDF/obraz → JPEG w base64 (rasteryzacja, nie OCR) |
| `naming.py` | budowa nazw plików, kolizje |
| `validate.py` | walidacja krzyżowa, uzupełnianie roku, nazwy miesięcy |
| `rename_ops.py` | zmiana nazw z logiem i cofaniem |
| `cache_db.py` | cache SQLite po odcisku pliku |
| `session.py`, `export.py`, `config.py` | sesje, CSV/XLSX, ustawienia |

## Zalecana kolejność przy dużej partii

1. Puść 20–30 plików, obejrzyj wyniki i porównaj z miniaturami.
2. Dopiero potem ruszaj z całością.
3. Po odczycie: *Sprawdź spójność* → *Uzupełnij brakujące lata* → przejrzyj filtr
   „podejrzane" i „brak danych", popraw ręcznie.
4. Eksportuj do Excela jako kopię bezpieczeństwa.
5. Na końcu *Zmień nazwy*.

Gotyckie winiety (starszy Kicker) i mocno stylizowane liternictwo wychodzą gorzej —
takie wiersze warto ponowić na innym modelu.
