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
- numer strony: dopełniony do 3 cyfr (z nazwy pliku albo ze skanu)

Każdy plik dostaje nazwę w tym formacie, także gdy czegoś nie udało się ustalić —
brakujące części zostają znacznikami (Windows nie pozwala na `?` w nazwach):

```
Placar - 1976-mm-dd - 000123.jpg                       ← nieznany dzień i miesiąc
Placar - rrrr-mm-dd - 000123.jpg                       ← znany tylko numer
Nieznany tytul - 1976-05-12 - nnnnnn.jpg               ← znana tylko data
```

Takie nazwy są „niekompletne” — przy zmianie nazw domyślnie pomijane.

**Kolekcje stron** (`lp - tytuł - rrrr-mm-dd - nr - str`) zachowują nazwę wejściową —
zmienia się tylko data, reszta (z `lp` na początku) zostaje bez zmian:

```
0001 - O Fluminense (RJ) - 1954-mm-dd - 022025 - 006-OST.jpg
0001 - O Fluminense (RJ) - 1954-07-20 - 022025 - 006-OST.jpg
```

## Wzorce nazw plików wejściowych

Co da się odczytać z samej nazwy pliku, program bierze z nazwy, a AI pyta tylko
o brakujące dane. *Pliki → Wzorce nazw plików…* (albo przycisk *Wzorce nazw…*) —
lista wzorców tej sesji, z podglądem na bieżących plikach.

| Pole | Znaczenie |
|---|---|
| `{lp}` | liczba porządkowa |
| `{tytul}` | tytuł gazety |
| `{rok}` | rok, 4 cyfry (albo `rrrr`) |
| `{mm}`, `{dd}` | miesiąc, dzień (albo `mm`, `dd` = nieznane) |
| `{nr}` | numer wydania, z dowolnym dopiskiem do następnego separatora: `023093A`, `022025 bis`, `010203s`, `023100-2`… |
| `{str}` | numer strony, z `-OST` (ostatnia strona wydania) |
| `{*}` | cokolwiek — pomijane |

Przykład: pliki `1956-962.pdf`, `2015-2224.pdf` (rok i numer Przeglądu Sportowego) —
wzorzec `{rok}-{nr}`, tytuł `Przeglad Sportowy`.

- Własny wzorzec zawierający `{lp}` i `{rok}-{mm}-{dd}` działa jak kolekcja — nazwa
  wyjściowa to wejściowa z uzupełnioną datą.
- Dopisek przy numerze zostaje w nazwie bez zmian i jest częścią numeru: `023167A`
  i `023167B` to dwa różne wydania.
- Wzorce wbudowane działają zawsze i są sprawdzane pierwsze: kolekcje stron i wydań
  (z `lp`) oraz nasz format wyjściowy — pliki już raz przemianowane też są rozpoznawane.
- Plik dostaje pierwszy pasujący wzorzec; kolumna **Wzorzec** pokazuje który.
  Pliki bez wzorca to „luźne” (filtr *luzne (bez wzorca)*) — AI ustala wszystko.
- Po dodaniu plików pasek stanu pokazuje, ile plików pasuje do którego wzorca.
- Wzorce zapisują się w sesji; wcześniej używane są na liście do wyboru.

### Kolekcje stron

Dla plików z danymi w nazwie prompt podaje modelowi tytuł, rok, numer i stronę —
model szuka tylko **dnia i miesiąca** (data z nazwy pliku nie jest mu podawana, żeby
odczyt był niezależny). Dodatkowo:

- **Jedno wydanie = jedna data.** Gdy co najmniej dwie strony tego samego numeru
  odczytano z tą samą datą (i żadna nie przeczy), wszystkie są pewne; strony bez
  odczytanej daty dostają datę z pozostałych stron. Strona z inną datą trafia do sprawdzenia.
- **Chronologia.** Pliki ustawione wg roku z nazwy i `lp` mają daty rosnące albo równe
  (działa i przy `lp` liczonym od nowa w każdej dekadzie, i ciągłym przez całą kolekcję).
  Nie w każdej kolekcji to prawda, więc *Narzędzia → Chronologia lp (ta sesja)* ma trzy
  ustawienia: **wyłączona** (w ogóle nie sprawdzana), **tylko ostrzeżenie** (domyślnie —
  uwaga w kolumnie Uwagi, kolor bez zmian) i **wpływa na pewność** (naruszenie
  chronologii = rekord do sprawdzenia). Ustawienie zapisuje się w sesji.
- **Rok ze skanu inny niż w nazwie** → do sprawdzenia.
- **Dwie różne daty na skanie** → obie w uwagach, rekord do sprawdzenia.
- **Data już jest w nazwie** (np. ustalona innym programem) → traktowana jako do
  potwierdzenia: AI odczyta to samo → pewne; co innego → do sprawdzenia z obiema
  wersjami. Plik już odczytany (cache) nie idzie ponownie do AI.

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

## Dostawcy AI: OpenRouter i Google Gemini

W zakładce **API i model** wybierasz dostawcę. Każdy ma własny klucz, model i limit
zapytań na minutę — przełączanie niczego nie gubi.

- **OpenRouter** — klucz z openrouter.ai/keys; darmowo 50 zapytań dziennie,
  po jednorazowym zakupie 10 kredytów 1000 dziennie.
- **Google Gemini** — klucz z aistudio.google.com/apikey; darmowe limity zależą od modelu
  (Flash-Lite ma ich najwięcej), aktualne pokazuje AI Studio. Odpowiedź ma wymuszony
  format JSON (`responseSchema`), więc rzadziej się psuje. Gdy nie da się pobrać listy
  modeli, program pokazuje listę zapasową.

**Identyfikator na obrazie.** Przed wysłaniem program dokleja nad każdym skanem biały
pasek `### 00042 ###`. Model przepisuje ten numer do odpowiedzi i po nim wynik trafia do
pliku — nawet jeśli model pomiesza kolejność obrazów w paczce. Odpowiedź z brakującym
albo obcym identyfikatorem jest odrzucana, a paczka ponawiana plik po pliku.

**Zasady odczytu.** Przycisk *Zasady odczytu i uwagi o kolekcji…* pozwala zmienić część
promptu opisującą, jak czytać datę, i dopisać uwagi o konkretnej kolekcji (mogą być po
polsku). Format odpowiedzi i identyfikatory dodaje program — tej części się nie edytuje.
Domyślne zasady: data tylko z winiety, żywej paginy lub stopki (nigdy z artykułów,
reklam ani kalendarzy), bez zgadywania, bez tłumaczenia odczytu, a gdy kolejność
dzień/miesiąc jest niejasna — obie możliwe daty w uwagach i rekord do sprawdzenia.

## Kolory: co jest pewne, a co do sprawdzenia

| Kolor | Znaczenie |
|---|---|
| zielony | **pewne** — można zmieniać nazwy |
| pomarańczowy | do sprawdzenia |
| czerwony | błąd odczytu |
| niebieski | nazwa już zmieniona |
| szary | jeszcze nieczytane |
| pomarańczowe tło | niezgodne z kalendarzem wydań albo odstające w walidacji |

Pewny (zielony) jest rekord poprawiony ręcznie, potwierdzony przez dwa niezależne źródła,
pewny w zaimportowanym raporcie, zgodny z kalendarzem wydań albo odczytany przez AI
z pewnością ≥ 0,80 bez żadnych zastrzeżeń. Filtry **„pewne”** i **„do sprawdzenia”**
pokazują te grupy.

**Pasek stanu kolekcji** nad tabelą pokazuje wszystkie pliki naraz (w kolejności tabeli),
każdy w kolorze swojego stanu. Gdy na jeden punkt paska przypada kilka plików, widać
najgorszy z nich. Kliknięcie przenosi do tego pliku w tabeli; najechanie myszą pokazuje,
co to za plik. Pod paskiem są liczniki grup.

## Kalendarz wydań

Z pewnych par numer–data (raporty, rekordy ręczne i potwierdzone, pewne odczyty AI)
program wylicza, jaka data powinna stać przy danym numerze: interpoluje między kilkoma
najbliższymi pewnymi wydaniami i bierze medianę, więc jedna błędna kotwica nie psuje
wyniku; restarty numeracji i numery specjalne są rozpoznawane. Przy sprawdzaniu rekordu
jego własna wartość jest pomijana — zgodność znaczy „sąsiednie, niezależne wydania
potwierdzają tę datę”.

Na 3335 pewnych wydaniach France Football kalendarz przewidział datę poprawnie
w 99,6% przypadków (test „z ukryciem” każdego wydania po kolei).

Numeracja wydań potrafi zaczynać się od nowa (ten sam numer w 1958 i 1968), więc kalendarz
porównuje numer tylko z pewnymi wydaniami z tego samego okresu (±1,5 roku od roku z nazwy
pliku albo od odczytanej daty). Tak samo *Sprawdź spójność* liczy osobno dla każdego roku,
a strony „tego samego wydania” muszą mieć też ten sam rok w nazwie.

Sortowanie po kolumnie *Stara nazwa* ustawia pliki kolekcji wg roku z nazwy, a potem `lp` —
także gdy `lp` zaczyna się od nowa w każdej dekadzie.

Kalendarz sprawdza wyniki automatycznie po odczycie AI i imporcie raportów; ręcznie:
*Narzędzia → Sprawdź z kalendarzem wydań*.

## Dopracuj niepewne (jeden przycisk)

Przycisk **Dopracuj niepewne** na dole okna:
1. **Kalendarz wydań** (bez zapytań) — zgodne rekordy robią się zielone; brakującą datę
   (przy znanym numerze) albo numer (przy znanej dacie) uzupełnia jako kandydata.
2. **Drugi odczyt AI** tego, co dalej jest niepewne — wybranym dostawcą i modelem
   (najlepiej innym niż za pierwszym razem), opcjonalnie obrazem dokładnym: wyższa
   rozdzielczość i powiększona góra strony nad całą stroną, jeden plik na zapytanie.
3. **Głosowanie** — rekord robi się pewny tylko, gdy zgadzają się dwa niezależne źródła
   (dwa odczyty AI albo odczyt i kalendarz). Gdy dwa odczyty dają różne, ale każdy spójny
   wynik, rekord zostaje do sprawdzenia z opisem obu wersji.
4. **Podsumowanie** — ile zrobiło się pewnych, ile zostało do ręcznego sprawdzenia.

*Narzędzia → Ponów odczyt podświetlonych (dokładniej)…* robi to samo dla wybranych
wierszy, niezależnie od ich stanu.

## Weryfikacja niepewnych odczytów

Przycisk **Weryfikuj (N)** (albo Ctrl+W) otwiera okno z dużym podglądem strony i polami
do poprawki — po kolei dla rekordów „do sprawdzenia”. Gdy w tabeli podświetlisz kilka
wierszy, weryfikacja obejmie właśnie je.

Data ma osobne pola **dzień / miesiąc / rok**, pod nimi dzień tygodnia wpisanej daty
(łatwo porównać z okładką). Program podpowiada datę i numer z kalendarza wydań albo
z sąsiednich plików na liście — klawisz **P** przyjmuje podpowiedź. Podpowiedź odświeża
się, gdy zmienisz numer wydania.

| Klawisz | Działanie |
|---|---|
| Enter | zapisz i przejdź do następnego |
| Tab | następne pole |
| ↑ / ↓ w polu dnia, miesiąca, roku | o jeden w przód / w tył (z przejściem przez miesiąc i rok) |
| P (w polach liczbowych) albo Alt+P | przyjmij podpowiedź |
| Esc / Ctrl+↓ | pomiń |
| Ctrl+↑ | poprzedni rekord |
| PageUp / PageDown | poprzednia / następna strona PDF-a |
| kółko myszy, przeciąganie | powiększenie, przesuwanie podglądu |

Przycisk *Otwórz w przeglądarce PDF* otwiera plik w programie systemowym.

## Sesje

- *Plik → Wczytaj ostatnią sesję* (Ctrl+Shift+O) i *Plik → Ostatnie sesje* (10 ostatnich).
- *Zapisz sesję jako…* proponuje nazwę `Tytuł - RRRR-MM-DD - GG-MM`, np.
  `France Football - 2026-09-30 - 01-37`, w folderze ostatniej sesji.
- Wczytane raporty CSV obejmują też pliki dodane później — nie trzeba ich importować
  ponownie.

## Zmiana nazw

*Zmień nazwy* pyta o zakres: **tylko pewne** (domyślnie), tylko zaznaczone, tylko
widoczne (po filtrze) albo pewne spośród widocznych — przy każdym widać liczbę plików.
Pole *także niekompletne* dołącza nazwy ze znacznikami braków. Przed wykonaniem program
pokazuje przykłady; każdą operację można cofnąć.

## Tabela

- Kolumny dopasowują się do zawartości po dodaniu plików, wczytaniu sesji i odczycie;
  ręcznie: *Widok → Dopasuj kolumny* (Ctrl+D). Szerokości ustawione ręcznie są
  zapamiętywane.
- Prawy przycisk na nagłówku tabeli: pokaż / ukryj kolumny.
- Pełna treść długich uwag jest w panelu po prawej.

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

## Aktualizacje programu

*Pomoc →*
- **Sprawdź aktualizacje…** — pobiera najnowszą wersję z repozytorium na GitHubie
  (domyślnie `wojciechborowa/UstalaczDatOrazNrWydan`, gałąź `claude/program-fix-izr5og`;
  zmiana: *Źródło aktualizacji…*), pokazuje listę zmienianych plików i ostatnie zmiany.
- **Aktualizuj program z pliku ZIP…** — to samo z paczki ZIP (np. z kilkoma poprawionymi
  plikami). Paczka może mieć pliki w podfolderze i opcjonalny `update.json`
  (`{"version": "2.0.1", "notes": "..."}`).
- **Cofnij ostatnią aktualizację…** — przywraca pliki sprzed aktualizacji.

Przed podmianą program robi kopię zastępowanych plików (folder `backups` w folderze
danych), zapisuje sesję, podmienia tylko pliki programu (`.py`, `.md`, `.txt`, `.json`,
`.bat`) w swoim folderze i uruchamia się ponownie, wracając do tej samej sesji. Paczki
z niebezpiecznymi ścieżkami (`..`, ścieżki bezwzględne) są odrzucane.

## Bezpieczeństwo danych

Dane programu (klucze API, cache odpowiedzi, logi zmian nazw w `renames/`) leżą w:
- Windows: `%APPDATA%\Czytnik wydan AI\` (Win+R, wpisz `%APPDATA%`),
- Linux / macOS: `~/.gazeta_ai/`.

Przy pierwszym uruchomieniu w Windows dane ze starego folderu `.gazeta_ai` są kopiowane
do nowego (stary zostaje jako zapas). Klucze **nie trafiają do pliku sesji** — sesję
można spokojnie komuś wysłać.
Zmiana nazw to jedyna operacja dotykająca Twoich plików; odczyt AI nigdy ich nie modyfikuje.

## Skróty klawiszowe

| Skrót | Działanie |
|---|---|
| Ctrl+N | Nowa sesja |
| Ctrl+O | Otwórz sesję |
| Ctrl+Shift+O | Wczytaj ostatnią sesję |
| Ctrl+S | Zapisz sesję |
| Ctrl+Shift+S | Zapisz sesję jako… |
| Spacja | Przełącz zaznaczenie podświetlonych wierszy |
| P | Otwórz podświetlony plik w domyślnym programie (też prawy przycisk myszy) |
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
| `ai_base.py` | wspólne dla dostawców AI: limit zapytań, ponawianie, błędy |
| `gemini_client.py` | Google Gemini API: modele, zapytanie z `responseSchema` |
| `openrouter_client.py` | HTTP, lista modeli, limit zapytań, ponawianie |
| `prompt.py` | uniwersalny prompt i odporny parser JSON |
| `render.py` | PDF/obraz → JPEG w base64 (rasteryzacja, nie OCR) |
| `naming.py` | budowa nazw plików, kolizje |
| `validate.py` | walidacja krzyżowa, uzupełnianie roku, nazwy miesięcy |
| `calendar_model.py` | kalendarz wydań: numer → data i odwrotnie |
| `refine.py` | dopracowanie niepewnych: uzupełnianie z kalendarza, głosowanie źródeł |
| `collection_map.py` | pasek stanu kolekcji |
| `filename_patterns.py` | wzorce nazw plików wejściowych |
| `collection_checks.py` | kolekcje: jedno wydanie = jedna data, chronologia `lp` |
| `rename_ops.py` | zmiana nazw z logiem i cofaniem |
| `updater.py` | aktualizacje z ZIP-a i z GitHuba, kopie zapasowe, cofanie |
| `cache_db.py` | cache SQLite po odcisku pliku (tylko wynik danego pliku, nie cała paczka) |
| `session.py`, `export.py`, `config.py` | sesje, CSV/XLSX, ustawienia |

## Zalecana kolejność przy dużej partii

1. Puść 20–30 plików, obejrzyj wyniki i porównaj z miniaturami.
2. Dopiero potem ruszaj z całością.
3. Jeśli masz raporty z innego programu — *Plik → Importuj raporty CSV…*.
4. *Dopracuj niepewne* — kalendarz wydań i drugi odczyt AI zazielenią większość.
5. *Weryfikuj* — resztę przejdź ręcznie (Enter, P).
6. Eksportuj do Excela jako kopię bezpieczeństwa.
7. Filtr „pewne” → *Zaznacz widoczne* → *Zmień nazwy*.

Gotyckie winiety (starszy Kicker) i mocno stylizowane liternictwo wychodzą gorzej —
takie wiersze warto ponowić na innym modelu.
