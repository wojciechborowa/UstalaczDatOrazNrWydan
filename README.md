# Czytnik wydań AI

Odczyt **daty** i **numeru wydania** ze skanów czasopism za pomocą modeli multimodalnych
przez OpenRouter. Bez OCR i bez Tesseracta — obraz trafia prosto do modelu, który czyta
winietę tak, jak czyta ją człowiek.

Działa uniwersalnie: France Football, Placar, Kicker, World Soccer i dowolny inny tytuł,
w dowolnym języku i dowolnej dekadzie. Prompt nie zawiera nazwy żadnej gazety.

Obsługiwane wejście: `.pdf` (czytana jest wybrana strona, domyślnie pierwsza) oraz
`.jpg .jpeg .png .bmp .tif .tiff .webp` (pojedyncze strony i rozkładówki).

## Tryb sesji: Kolekcja wydań albo Kolekcja stron

Przy nowej sesji (i przy dodaniu pierwszych plików) program pyta o tryb:

| Tryb | Pliki | Co ustala AI | Co jest z nazwy pliku |
|---|---|---|---|
| **Kolekcja wydań** | PDF-y z całymi wydaniami | tytuł, datę i numer wydania — z wybranej strony PDF-a (domyślnie 1.) | to, co nazwa jednoznacznie zawiera — tylko do porównania |
| **Kolekcja stron** | skany pojedynczych stron / rozkładówek | **dzień i miesiąc** | tytuł, rok, numer wydania, numer strony |

*Plik → Ustawienia sesji…* (albo przycisk *Ustawienia sesji…* nad tabelą) zmienia tryb
i stronę wysyłaną do AI — dla gazet, które datę i numer drukują np. na stronie 3.
Tryb i strona zapisują się w sesji. Starsza sesja (bez trybu) przy otwarciu pyta o tryb
z propozycją wg typu plików.

**Tylko odpowiedź AI.** Program niczego nie uzupełnia z kalendarza wydań, interpolacji,
sąsiednich plików ani chronologii — takich funkcji już nie ma. Dane wejściowe to nazwa
pliku (i ewentualnie zaimportowany raport); z nimi porównywany jest odczyt AI:

- odczyt **kompletny i zgodny** z nazwą pliku (i raportem) → **zielony**,
- odczyt niekompletny, niejednoznaczny, z nieistniejącą datą albo **niezgodny** z nazwą
  / raportem → **do weryfikacji**, a w kolumnie *Uwagi* krótko, po ludzku, dlaczego
  (np. „AI odczytało rok 1955, a w nazwie pliku jest 1954”),
- przy niezgodności **wygrywa nazwa pliku** — to ona jest w polach i w nowej nazwie;
  odczyt AI widać w *Uwagach*, w panelu szczegółów i na dole okna weryfikacji.

Z nazwy pliku porównywane jest tylko to, co jednoznaczne: pola wzorca nazwy, pełna data
(`1958-06-17`, `17.06.1958`, `17 juin 1958`, `June 17, 1958`), miesiąc słownie z rokiem,
czterocyfrowy rok (gdy w nazwie jest dokładnie jeden) i oznaczony numer (`nr 638`, `n° 638`,
`#638`). Samotna liczba (np. `12`) jest pomijana — nie wiadomo, czy to dzień, numer czy
strona. Tytułu nie porównujemy (skróty w nazwach dawałyby fałszywe alarmy).

**Pewność modelu nie decyduje o kolorze.** Kolumna *Pewność* pokazuje wartość z AI, można
po niej sortować, a pole **Pewność** nad tabelą filtruje: gotowe progi (`<0.8`, `>=0.9`,
`0.5-0.8`, `brak`…) albo własny wpis zatwierdzony Enterem.

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

Co da się odczytać z samej nazwy pliku, program bierze z nazwy. W kolekcji stron AI
pyta tylko o dzień i miesiąc; w kolekcji wydań AI czyta wszystko niezależnie, a dane
z nazwy służą do porównania. *Pliki → Wzorce nazw plików…* (albo przycisk *Wzorce nazw…*) —
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

Dla plików z danymi w nazwie prompt podaje modelowi tytuł, rok, numer i stronę oraz to,
że to pojedyncza strona albo rozkładówka ze środka wydania — model szuka tylko **dnia
i miesiąca**. Data z nazwy pliku nie jest mu podawana, żeby odczyt był niezależny.

- **Rok ze skanu inny niż w nazwie** → do weryfikacji; w polach zostaje rok z nazwy,
  a okno weryfikacji pokazuje na dole, jaki rok odczytało AI.
- **Dwie różne daty na skanie** → obie w uwagach, rekord do weryfikacji.
- **Data już jest w nazwie** (np. ustalona innym programem) → porównywana z odczytem AI:
  zgodna = zielony, inna = do weryfikacji.
- Nie ma żadnych kontroli ciągłości, chronologii `lp` ani „jedno wydanie = jedna data”.

## Instalacja

```bash
pip install -r requirements.txt
python app.py
```

Wymaga Pythona 3.10+ z modułem `tkinter` (na Ubuntu/Debianie: `sudo apt install python3-tk`;
w Windows i macOS jest w standardowej instalacji).

## Jak używać

1. **Zakładka „API i model"** — wybierz dostawcę, wklej klucz, *Zapisz klucz*, *Testuj klucz*.
   Następnie *Pobierz listę modeli* — lista jest ograniczona do modeli przyjmujących obrazy.
   Zaznacz model i kliknij *Użyj zaznaczonego modelu*.
2. **Nowa sesja** (Ctrl+N) — wybierz tryb: Kolekcja wydań (i stronę dla AI) albo
   Kolekcja stron.
3. **Zakładka „Pliki i wyniki"** — *Dodaj folder* (opcjonalnie z podfolderami) lub
   *Dodaj pliki*. Zaznaczaj pojedynczo (klik w kwadracik), grupowo (Ctrl/Shift + spacja)
   albo przyciskami *Zaznacz wszystko / Odwróć*.
4. **Odczytaj daty za pomocą AI** — program pokaże, ile zapytań to zajmie, czy wystarczy
   limitu dziennego i ile to potrwa, i ruszy. *Pauza* i *Stop* działają w każdej chwili.
5. **Weryfikuj** — przejdź żółte rekordy (Enter zapisuje, P wstawia odczyt AI).
6. **Zmień nazwy** — dopiero teraz program dotyka dysku. Operacja jest logowana i odwracalna
   przyciskiem *Cofnij zmianę nazw*.

## Limity i liczniki zużycia

Program sam liczy, ile wysłał — bez pytania konta u dostawcy. Liczniki są osobne dla
każdego dostawcy, modelu i klucza:

- zapytania i tokeny w ostatniej minucie (tokeny z odpowiedzi API),
- zapytania i tokeny w bieżącej dobie limitu — Gemini odnawia limit o północy czasu
  pacyficznego (ok. 9:00 w Polsce), OpenRouter o północy UTC.

Limity modelu (**zapytań/min, tokenów/min, zapytań/dzień**) ustawiasz w zakładce API
(*Parametry przetwarzania*); przycisk *Domyślne dla modelu* wpisuje orientacyjne wartości
darmowego planu — dostawcy je zmieniają, a API ich nie podaje, więc warto je porównać
z AI Studio / OpenRouterem. `0` = bez limitu. Na tej podstawie program:

- przed startem podpowiada, ile zapytań zajmie odczyt, czy wystarczy limitu dziennego
  (i ile skanów zmieści się dziś) oraz ile to potrwa,
- zwalnia przed limitem zapytań i tokenów na minutę, zamiast łapać błąd 429,
- po wyczerpaniu limitu dziennego zatrzymuje odczyt z informacją, kiedy limit się odnowi —
  nieodczytane pliki zostają zaznaczone, wystarczy później wznowić,
- pokazuje bieżące zużycie nad tabelą, w zakładce API i w *Narzędzia → Zużycie limitów AI*.

Liczniki nie widzą zapytań wysłanych tym samym kluczem z innych programów.

**Zapisane klucze.** Pod polem klucza jest lista zapisanych kluczy (np. płatny i darmowy)
— przełączasz je ręcznie. Program nie rotuje kluczy automatycznie: limity darmowego planu
dotyczą projektu / konta, a omijanie ich wieloma kontami łamie zasady dostawców.

OpenRouter: modele `:free` mają 20 zapytań na minutę oraz **50 dziennie**, a po
jednorazowym zakupie 10 kredytów **1000 dziennie** — limity liczone są na konto.

## Dlaczego batch po 5 skanów

W jednym zapytaniu jedzie kilka obrazów naraz — limit liczy zapytania, nie obrazy. Każdy
skan ma na obrazie identyfikator, a model zwraca tablicę JSON z tym identyfikatorem, więc
wyniki nie pomylą się między plikami.

Im większy batch, tym większe ryzyko, że model przesunie odpowiedzi albo zwróci ich za mało.
Program to wykrywa i **cały taki batch ponawia pojedynczo**, zamiast zapisać przesunięte
dane. Wartość 5 to rozsądny kompromis; można ją zmienić w zakładce API. Liczba skanów
w zapytaniu jest stała — losowanie jej nic nie daje, a psuje powtarzalność odczytu.

## Zasady odczytu i cache

**Zakaz zgadywania.** Prompt wymaga `null` wszędzie tam, gdzie czegoś nie widać, oraz
pierwszej daty z zakresu („du 15 au 21 mars"). Każe ufać nazwie miesiąca, gdy jest
wydrukowana, a przy niejasnej kolejności dzień/miesiąc podać obie możliwe daty.

**Cache po odcisku pliku.** Wynik każdego odczytu ląduje w SQLite pod kluczem
(odcisk pliku, model, tryb, strona). Przerwany przebieg wznawia się bez zużywania limitu,
a zmiana modelu albo strony wymusza ponowny odczyt.

**Ponów odczyt AI.** *Narzędzia* (albo prawy przycisk na wierszu) → *Ponów odczyt AI
podświetlonych* — nowe zapytanie bez cache, opcjonalnie z **obrazem dokładnym** (wyższa
rozdzielczość i powiększona góra strony nad całą stroną, jeden plik na zapytanie). Wynik
podlega tym samym zasadom: zielony albo do weryfikacji.

**Podgląd i ręczna poprawka.** Panel po prawej pokazuje górny pasek strony wysyłanej do AI,
odczyt AI, dane z nazwy pliku i z raportu. Podwójny klik w wiersz otwiera okno edycji.

## Dostawcy AI: OpenRouter i Google Gemini

W zakładce **API i model** wybierasz dostawcę. Każdy ma własny klucz, model i limity
— przełączanie niczego nie gubi.

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

## Kolory: zielony albo do weryfikacji

| Kolor | Znaczenie |
|---|---|
| zielony | odczyt AI kompletny i zgodny z nazwą pliku / raportem, albo poprawiony ręcznie — można zmieniać nazwy |
| pomarańczowy | do weryfikacji |
| czerwony | błąd odczytu (też do weryfikacji) |
| niebieski | nazwa już zmieniona |
| szary | jeszcze nieczytane |

Przy rekordzie do weryfikacji kolumna *Uwagi* zawsze mówi dlaczego (`DO WERYFIKACJI: …`).
Po wczytaniu sesji ocena jest liczona od nowa.

Filtry: *do weryfikacji*, *zielone (pewne)*, *niezgodne z nazwą/raportem*, *niekompletny
odczyt*, *z raportem*, *bez nowej nazwy*, *luźne (bez wzorca)* — plus filtr **Pewność**.

**Pasek stanu kolekcji** nad tabelą pokazuje wszystkie pliki naraz (w kolejności tabeli),
każdy w kolorze swojego stanu. Gdy na jeden punkt paska przypada kilka plików, widać
najgorszy z nich. Kliknięcie przenosi do tego pliku w tabeli; najechanie myszą pokazuje,
co to za plik. Pod paskiem są liczniki grup.

Sortowanie po kolumnie *Stara nazwa* ustawia pliki kolekcji wg roku z nazwy, a potem `lp` —
także gdy `lp` zaczyna się od nowa w każdej dekadzie.

## Weryfikacja

Przycisk **Weryfikuj (N)** (albo Ctrl+W) pyta o zakres: **wszystkie**, **widoczne**
(po filtrze), **zaznaczone** (☑), **widoczne i zaznaczone**, a gdy w tabeli podświetlisz
kilka wierszy — także **podświetlone**. Pole *tylko do weryfikacji* (domyślnie włączone)
pomija zielone i nieczytane; po jego wyłączeniu można przejrzeć także zielone.
Przy każdym zakresie widać liczbę rekordów.

Okno ma duży podgląd strony (w kolekcji wydań otwiera się na stronie wysyłanej do AI)
i pola do poprawki. Data ma osobne pola **dzień / miesiąc / rok**, pod nimi dzień tygodnia
wpisanej daty. W polach są wartości rekordu — przy niezgodności te z nazwy pliku — a na
dole okna **to, co odczytało AI**, gdy jest inne (np. „AI odczytało inny rok: 1955”).
Klawisz **P** wstawia odczyt AI do pól.

| Klawisz | Działanie |
|---|---|
| Enter | zapisz i przejdź do następnego |
| Tab | następne pole |
| ↑ / ↓ w polu dnia, miesiąca, roku | o jeden w przód / w tył (z przejściem przez miesiąc i rok) |
| P (w polach liczbowych) albo Alt+P | wstaw odczyt AI |
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

## Raport HTML i podsumowanie przelotu

Po zakończeniu odczytu program pokazuje podsumowanie: ile plików odczytano pewnie, ile do sprawdzenia, ile z błędem,
czas odczytu AI, czas na plik oraz **ile czasu zaoszczędzono** w porównaniu z pracą ręczną. Stamtąd (albo z menu
*Narzędzia → Raport HTML z tej sesji…*) można wygenerować raport HTML: jeden samodzielny plik, wyśrodkowany,
przygotowany pod zrzut ekranu. Działa też dla wczytanej, wcześniej zapisanej sesji.

- Czas ręczny to założenie (domyślnie 30 s na plik) i czas kontroli pliku „do sprawdzenia" (20 s) - oba można zmienić w oknie raportu.
- Czas odczytu AI jest zapisywany w sesji od tej wersji. W starszych sesjach brak go - raport pokaże szacunek (oznaczony) albo wpiszesz czas ręcznie.
- Ścieżki folderów nie trafiają do raportu; nazwy plików wymagających uwagi tylko po zaznaczeniu opcji.

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

**Raport to dane wejściowe do porównania — tak jak nazwa pliku.** Nie zmienia pól rekordu
i sam nie robi go zielonym: dane z raportu są zapamiętane przy rekordzie (widać je
w panelu szczegółów, filtr *z raportem*), a po odczycie AI program je porównuje —
zgodne = zielony, inna data lub numer = do weryfikacji z informacją, co podaje raport.
Rekord poprawiony ręcznie nie jest ruszany.

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
| Ctrl+W | Weryfikacja (z wyborem zakresu) |

## Eksport

*Plik → Eksport do CSV / do Excela* (gdy brakuje biblioteki `openpyxl`, program proponuje
ją doinstalować albo zapisuje CSV) zapisuje wszystkie kolumny, jakie program przechowuje:
obok daty i numeru także język, datę w oryginalnym brzmieniu, nazwę miesiąca, informację
czy rok był nadrukowany, pewność, datę i numer według AI, powód weryfikacji, użyty model,
status, uwagi i surową odpowiedź modelu.
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
| `assess.py` | ocena rekordu: zielony albo do weryfikacji (AI vs nazwa pliku i raport), tryby sesji |
| `name_facts.py` | jednoznaczne dane z nazwy pliku (data, rok, numer), nazwy miesięcy |
| `usage.py` | własne liczniki limitów: zapytania i tokeny na minutę i na dzień |
| `collection_map.py` | pasek stanu kolekcji |
| `filename_patterns.py` | wzorce nazw plików wejściowych |
| `rename_ops.py` | zmiana nazw z logiem i cofaniem |
| `updater.py` | aktualizacje z ZIP-a i z GitHuba, kopie zapasowe, cofanie |
| `cache_db.py` | cache SQLite po odcisku pliku (tylko wynik danego pliku, nie cała paczka) |
| `session.py`, `export.py`, `config.py` | sesje, CSV/XLSX, ustawienia |

## Zalecana kolejność przy dużej partii

1. Nowa sesja → tryb (i strona dla AI w kolekcji wydań).
2. Puść 20–30 plików, obejrzyj wyniki i porównaj z miniaturami.
3. Dopiero potem ruszaj z całością — podpowiedź przed startem pokaże, czy wystarczy limitu.
4. Jeśli masz raporty z innego programu — *Plik → Importuj raporty CSV…* (do porównania).
5. *Weryfikuj* — żółte rekordy przejdź ręcznie (Enter, P). Trudne można najpierw
   *Ponowić odczytem AI* z obrazem dokładnym albo innym modelem.
6. Eksportuj do Excela jako kopię bezpieczeństwa.
7. *Zmień nazwy* → zakres „tylko pewne”.

Gotyckie winiety (starszy Kicker) i mocno stylizowane liternictwo wychodzą gorzej —
takie wiersze warto ponowić na innym modelu.

