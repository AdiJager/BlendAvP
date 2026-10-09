# RIF Exporter — jak używać

Rozszerzenie do Blendera eksportujące scenę do formatu `.rif` z gry
**Aliens vs Predator (1999)**.

## Instalacja i włączenie

1. `Edit > Preferences > Add-ons > Install...` i wskaż paczkę rozszerzenia.
2. Włącz **RIF Exporter** na liście add-onów.

## Gdzie jest panel **RIF Light**

Ustawienia świateł, których Blender nie ma (zasięg w jednostkach gry, mnożnik
jasności, podgląd zasięgu), są **na obiekcie światła — nie w oknie eksportu**.
Znajdziesz je tak:

1. W viewporcie **zaznacz obiekt światła** (Point albo Spot).
2. Otwórz edytor **Properties** i zakładkę **Object** — pierwsza ikona
   w pionowym pasku po lewej (pomarańczowy kwadrat).
3. Panel **RIF Light** jest na górze stosu paneli.

Panel pokazuje się **tylko dla świateł Point/Spot**. Przy zaznaczonym meshu,
kamerze czy świetle Sun go nie ma — to normalne.

> Nie możesz znaleźć świateł w scenie? `Object > Add > AvP > Select RIF Lights`
> zaznaczy wszystkie i wypisze, ile ich jest.

## Co jest w panelu

| Pole | Znaczenie |
|---|---|
| **Reach (m)** | jak daleko światło sięga; `0` = domyślne 25 m |
| **Brightness Scale** | mnożnik mocy tylko dla gry; `0`/`1` = bez zmian |
| **Show Reach in Viewport** | rysuje zasięg światła w viewporcie |

Pod spodem panel wypisuje **na żywo**, co eksporter zapisze:

```
Power: 500 W
Engine brightness: 46592
```

Jeśli światło osiągnęło maksimum, zobaczysz ostrzeżenie
`At the ceiling - raise Reach or lower Power`.

## Wizualizacja zasięgu

Przy włączonym **Show Reach in Viewport** każde światło rysuje w viewporcie
swoją strefę:

- **Point** — sfera z drutu: pełny zasięg plus pierścienie na 2/3 i 1/3,
  żeby było widać, w której strefie wypada ściana;
- **Spot** — stożek o kącie z `Spot Size`.

Zaznaczone światło rysuje się wyraźnie, pozostałe blado.

## Gdy zasięg się nie pokazuje

Blender **połyka błędy z rysowania w viewporcie** — awaria wygląda dokładnie
tak samo, jak gdyby podgląd był po prostu wyłączony. Dlatego jest narzędzie
diagnostyczne:

**`Object > Add > AvP > Diagnose RIF Reach Overlay`**

Wypisze w konsoli (`Window > Toggle System Console`) oraz w UI:

- czy podgląd jest w ogóle zarejestrowany,
- ile świateł jest w aktywnej warstwie widoku,
- które światła kwalifikują się do rysowania — z ich `range_m` i `show_reach`,
- **ostatni błąd rysowania**, jeśli jakiś był.

Sprawdź najpierw dwie rzeczy:

1. **`Reach` niezerowy.** `Reach = 0` znaczy „użyj domyślnego 25 m" i rysuje
   się, ale jeśli ustawiłeś bardzo mały zasięg, okrąg może być mniejszy niż
   sądzisz — oddal widok.
2. **`Show Reach in Viewport` włączone** *dla tego światła* — to ustawienie
   per obiekt, nie globalne.

## Tekstury z gry

Gra trzyma tekstury w skompresowanych archiwach `Tex1.FFL`…`Tex58.FFL`
(1028 plików `.RIM`). Rozszerzenie potrafi je rozpakować, zamienić na `.png`
i zbudować z nich **bibliotekę materiałów** gotową do użycia na twoich
poziomach.

### Wskazanie katalogu gry

1. `Edit > Preferences > Add-ons`, znajdź **RIF Exporter** i rozwiń strzałkę
   po jego nazwie (otworzą się ustawienia rozszerzenia).
2. W polu **Game Directory** wskaż folder gry — ten, w którym są podfoldery
   `fastfile` i `avp_rifs`.

### Load Game Textures

Przycisk **Load Game Textures** robi wszystko po kolei:

1. czyta archiwa `Tex1.FFL`…`Tex58.FFL` (archiwa dźwięków są pomijane),
2. rozpakowuje je do `<katalog gry>\fastfile\_ffl_unpacked`,
3. zamienia każdy `.RIM` na `.png` obok niego,
4. tworzy **jeden materiał na każdą teksturę**.

Operacja jest jednorazowa i zajmuje chwilę. Można ją bezpiecznie przerwać
i uruchomić ponownie — to, co już jest zrobione, zostanie pominięte.

### Jak nazywają się materiały

Nazwa materiału to **ścieżka tekstury w grze**, np.
`Envrnmts\Battle\Cliff04.RIM`. Dzięki temu od razu widać, z którego poziomu
tekstura pochodzi, a nazwy są unikalne — w grze 109 nazw plików powtarza się
w różnych katalogach **z inną grafiką**, więc sama nazwa pliku byłaby
niejednoznaczna.

Wszystkie materiały mają włączony **backface culling**, żeby podgląd zgadzał
się z tym, jak gra rysuje ściany.

### Galeria materiałów

Przycisk **Build Texture Gallery** tworzy osobną scenę z siatką płaszczyzn —
każda z innym materiałem. Naciśnij `0`, żeby wejść w widok kamery i przejrzeć
całą bibliotekę. To najszybszy sposób, żeby znaleźć teksturę na oko.

**Clear Texture Library** usuwa materiały z gry i nieużywane obrazki, gdy
chcesz zacząć od nowa.

### Przypisywanie tekstur do ścian

Rozszerzenie **nie przypisuje tekstur do geometrii** — nie da się przewidzieć,
która tekstura pasuje na którą powierzchnię. Nadajesz materiały samodzielnie,
tak jak w każdym innym projekcie w Blenderze. Przy eksporcie każdy materiał
trafia do pliku jako osobny wpis, więc różne ściany mogą mieć różne tekstury.

### Co się dzieje bez tekstur z gry

Materiał bez tekstury z gry nadal się wyeksportuje — pod swoją nazwą. Jeśli
gra takiej tekstury nie znajdzie, **ściany stają się czarne i przestają
reagować na światło** (silnik degraduje je wtedy do nieoświetlonych
wielokątów). To najczęstsza przyczyna „ciemnej geometrii" — nie brak świateł.

## Skąd bierze się oświetlenie

| Co | Skąd w Blenderze |
|---|---|
| światło otoczenia (ambient) | `World > Color` × `World > Strength` |
| jasność i kolor lampy | `Power`, `Color`, `Temperature` obiektu światła |
| zasięg | `RIF Light > Reach` |
| stożek | `Spot Size` + `Spot Blend` |

**Uwaga na `World > Strength`.** To jasność całego otoczenia: przy `1.0`
poziom wyjdzie jednolicie jasny i żadne światło się nie przebije. Dla poziomu
sensowne wartości to **0,05–0,3**.

**Uwaga na `Power`.** 500 W w odległości 5 m daje średnią szarość, a 1000 W już
dobija do maksimum, jakie silnik potrafi zapisać. Powyżej tego progu moc
przestaje cokolwiek zmieniać — jedyną dźwignią jest `Reach`.

## Eksport

`File > Export > Aliens vs Predator RIF`. Trzy tryby:

| Tryb | Co zapisuje |
|---|---|
| **Single Shape** | jeden aktywny mesh |
| **Multi-Object** | wszystkie zaznaczone meshe, bez otoczenia |
| **Level Skeleton** | zaznaczone meshe + otoczenie poziomu (tekstury, światła, spawn) |

### Pola, które trzeba wypełnić

| Pole | Zalecenie |
|---|---|
| **Mode** | `Level Skeleton` do poziomu |
| **Level Name** | nazwa slotu, który podmieniasz w grze |
| **Safe Spawn** | zostaw włączone |

Tekstury biorą się **z materiałów** — nie ma już pola `Texture Override`.
Każdy materiał nieść ze sobą nazwę tekstury w grze, więc różne ściany mogą
mieć różne tekstury bez żadnych dodatkowych ustawień.

## Zanim uruchomisz grę

Sprawdź plik walidatorem — wyłapuje błędy, które w grze objawiają się pustym
poziomem, bez żadnego komunikatu:

```
python avp_rif_exporter/tools/validate_level.py --game "<katalog gry>" <wyeksportowany>.rif
```

Podanie katalogu gry (`--game`, albo zmienna środowiskowa `AVP_GAME_DIR`)
włącza **najważniejsze sprawdzenie**: walidator porównuje każdą nazwę tekstury
z `BMPNAMES` z prawdziwymi archiwami gry i mówi wprost, jeśli jakiejś nie ma.

To istotne, bo **nierozwiązana tekstura nie tylko traci obrazek** — silnik
zamienia taką ścianę na nieoświetloną i przestaje ją rysować. Poziom, w którym
wszystkie tekstury są nierozwiązane, wygląda jak pustka, choć kolizje działają.
Szukaj linii:

```
note    all 15/15 BMPNAMES names exist in the game's archives
```

albo ostrzeżenia `... are not in the game's archives`, które wskazuje winowajcę.

## Typowe problemy

| Objaw | Przyczyna | Co zrobić |
|---|---|---|
| **Poziom jest pusty** | materiał bez tekstury z gry (nazwa nie istnieje w archiwach) | uruchom **Load Game Textures** i użyj materiałów z biblioteki |
| **Ściany czarne, światło ich nie łapie** | nierozwiązana tekstura — silnik degraduje ścianę do nieoświetlonej | jak wyżej; to nie brak świateł |
| **Wszystko jednolicie jasne** | `World > Strength` za wysokie | zejdź do 0,05–0,3 |
| **Wszystko czarne** | brak świateł i zerowy ambient | dodaj lampę albo podnieś `Power` |
| **Moc nic nie zmienia** | światło na sufcie jasności | skróć `Reach` |
| **Nie widzę panelu RIF Light** | zaznaczony obiekt nie jest światłem Point/Spot | zaznacz lampę; `Object > Add > AvP > Select RIF Lights` |
| **Nie widzę zasięgu** | `Reach = 0` albo `Show Reach in Viewport` wyłączone | ustaw `Reach` i włącz podgląd |
| **Load Game Textures nic nie robi** | zły `Game Directory` | wskaż folder, w którym jest podfolder `fastfile` |
| **Materiał ma nazwę `...coś.RIM`** | nazwa dłuższa niż 63 znaki (limit Blendera) | pełna nazwa jest w właściwości `RIM_PATH`, eksport użyje jej |
| **Jedna tekstura się nie zaimportowała** | grafika menu z 11 planami — nieobsługiwana | pomijana celowo, do poziomów niepotrzebna |
| **Testy w Blenderze pokazują stare błędy** | kopia testów w pliku `.blend` jest nieodświeżona | uruchom `tests/run_tests_from_disk.py` (patrz niżej) |

### Testy w Blenderze

Testy istnieją w dwóch miejscach: na dysku
(`avp_rif_exporter/tests/blender_roundtrip.py`) oraz jako **kopia w pliku
`.blend`**. Kopia jest migawką — poprawki na dysku jej nie zmieniają, więc
uruchamianie kopii pokazuje błędy, które zostały już naprawione.

Żeby uruchomić aktualną wersję z dysku, otwórz w Text Editorze
`avp_rif_exporter/tests/run_tests_from_disk.py` i naciśnij **Run Script**
(Alt+P). Skrypt wczytuje moduł wprost z pliku i wypisuje wynik.

## Dokumentacja techniczna

Notatki projektowe i hand-offy sesji leżą w `../context/` — zaczynając od
`README.md`, potem najnowszy `HANDOFF_S6_F*.md`.
