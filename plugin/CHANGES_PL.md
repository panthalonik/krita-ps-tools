# PS Tools — natywne maski na warstwach korekcyjnych

Bazą jest ps_tools_krita_en2.zip. Ta wersja zmienia wyłącznie obsługę masek na warstwach korekcyjnych (filter layers) i Blend If na tych warstwach.

## Co się zmieniło

- Maska na warstwie korekcyjnej jest teraz zwykłą maską przezroczystości Krity. Maluje się po niej na żywo, działa Cofnij, zapisuje się w .kra bez udziału wtyczki.
- Przyczyna dawnej „dziury”: Krita nadaje warstwom filtrów tryb mieszania Copy, w którym maska wycina przezroczystość w całym obrazie. Wtyczka przełącza taką warstwę na Normal, gdy tylko ma ona widoczną maskę przezroczystości. Tryb wybrany ręcznie (inny niż Copy) nie jest ruszany.
- Usunięty stary mechanizm: maska zaznaczenia „Layer Mask”, ukryty magazyn „Blend If (...)” i kopiowanie do wbudowanej maski warstwy po zakończeniu pociągnięcia.
- Blend If na warstwie korekcyjnej działa tak samo jak na zwykłej warstwie: osobna, widoczna maska „Blend If (...)”.
- Nowa warstwa korekcyjna tworzona przy aktywnym zaznaczeniu dostaje maskę przezroczystości z tego zaznaczenia.
- Panel warstw pokazuje maskę warstwy korekcyjnej jako drugą miniaturę, tak samo jak dla zwykłych warstw.
- Co 300 ms sprawdzana jest tylko aktywna warstwa; całe drzewo jest przeglądane raz na ok. 3 s (maska upuszczona lub wklejona na inną warstwę).

## Wersja en4 — grupowanie i odświeżanie Blend If

- Ctrl+G to teraz akcja wtyczki „Group Layers (keeps clipping stacks together)”. Jeśli zaznaczona jest warstwa należąca do stosu clipping mask, grupowany jest cały stos. Wcześniej Krita wyciągała pojedyncze warstwy z ukrytej grupy „(clip)”: stos się rozpadał, a po grupie zostawał pusty wiersz o nazwie warstwy bazowej.
- Pusta grupa „(clip)” (np. po przeciągnięciu warstw myszą) jest usuwana.
- Nowa grupa zawierająca bezpośrednio warstwy korekcyjne dostaje tryb Pass Through, jak w Photoshopie — korekcje działają na wszystko pod grupą. Nie dotyczy grup z maską, grup ze stosem clipping mask ani grup, które istniały wcześniej. Tryb można wyłączyć ręcznie i wtyczka go nie przywróci.
- Auto-odświeżanie Blend If / Color Lookup / Selective Color nie polega już tylko na miniaturach 96 px. Po każdym zakończonym pociągnięciu (lub innej operacji Krity) odświeżane są warstwy zależne, które „widzą” aktywną warstwę.

Ograniczenia: odświeżenie następuje po zakończeniu pociągnięcia, nie w jego trakcie. 


## Wersja en5 — domknięcie luk w clipping maskach

- Grupowanie z menu (menu kontekstowe panelu warstw i menu główne) używa teraz tej samej akcji co Ctrl+G, więc też nie rozrywa stosów clipping mask.
- Stos, który stracił warstwę bazową (usunięta, przeciągnięta albo zgrupowana osobno), jest zwalniany jak w Photoshopie: warstwy przestają być przycięte, a ukryta grupa „(clip)” znika. Wcześniej przycięta korekcja zostawała sama w izolowanej grupie i przestawała działać.
- To samo dla grupy „(clip)”, w której została jedna warstwa (także w dokumentach otwartych z pliku).

Testy: python verification/test_groups.py (29 testów, atrapa API Krity).

## Wersja en6 — zwalnianie clipping maski z grupy

- Release Clipping Mask na przyciętej grupie nie rozwiązuje już tej grupy. Przyczyna: Quick Ungroup Krity rozwiązuje aktywny węzeł, jeśli jest on grupą, a wtyczka ustawiała zwalnianą grupę jako aktywną. Warstwy z jej środka wypadały do stosu i były przycinane pojedynczo.

## Wersja en7 — maska na grupie z korekcjami, zwijanie grup, usuwanie maski

- Maska przezroczystości na grupie Pass Through działa na warstwy korekcyjne w tej grupie (także gdy grupa jest przycięta clipping maską). Krita ignoruje maski takich grup, więc wtyczka przepisuje maskę grupy do wbudowanych masek warstw korekcyjnych w środku (moduł groupmask.py). Dzieje się to po zakończeniu pociągnięcia, nie w jego trakcie. Maska nie obejmuje zwykłych warstw malarskich w takiej grupie, a przy kilku korekcjach miękkie krawędzie maski są stosowane osobno dla każdej z nich.
- Strzałka zwijania/rozwijania działa także na grupach, które mają maskę (wcześniej klik trafiał obok, bo wiersz jest rysowany z przesunięciem o miniaturę maski).
- Nowe polecenie „Remove Transparency Mask” w menu pod prawym przyciskiem (i jako akcja do skrótu): usuwa maskę zaznaczonej warstwy lub grupy. Maski Blend If nie rusza.

Testy: python verification/test_groupmask.py (14 testów, atrapa API Krity).

## Wersja en8 — maska nie da się już wyciągnąć z warstwy

- Przyczyna: kliknięcie miniatury maski było przejmowane przez wtyczkę, ale ruch myszy już nie. Qt traktował minimalny ruch jako przeciąganie zaznaczonego elementu (świeżo zaznaczonej maski), a upuszczona między wierszami maska trafiała do grupy jako osobna pozycja.
- Ruch myszy po kliknięciu obsłużonym przez wtyczkę (miniatura maski, strzałka grupy, Alt+klik) nie rozpoczyna już przeciągania.
- Maskę można upuścić tylko NA wiersz warstwy, która nie ma jeszcze maski (przeniesienie maski na inną warstwę, jak w Photoshopie). Między wierszami, na pustym obszarze i na innej masce upuszczenie jest zablokowane.

Testy: python verification/test_maskdrag.py (10 testów z prawdziwymi widżetami Qt).

## Wersja en9 — Blend If, Color Lookup i Selective Color na żywo (Krita PS)

Dotyczy Krita PS (build z tego repozytorium). W oficjalnej Kricie wtyczka działa jak dotąd.

- Blend If jest liczony przez silnik w trakcie składania obrazu (łatka `krita/patches/0002`): dla każdego piksela z koloru warstwy („This Layer”) i z tego, co jest pod nią w danej chwili („Underlying Layer”), jak w Photoshopie. Nie ma już kopii obrazu ani przeliczania po zakończeniu pociągnięcia — malowanie pod warstwą, na niej i ruch suwaków w oknie Blend If widać od razu.
- Ustawienia zostają w nazwie maski „Blend If (...)”, więc pliki .kra są zgodne w obie strony. W Krita PS piksele tej maski są pomijane; w oficjalnej Kricie wtyczka nadal je wylicza.
- Color Lookup i Selective Color to prawdziwe warstwy filtrów (filtry C++ `pscolorlookup`, `psselectivecolor`). Krita renderuje je na żywo jak każdą inną korekcję. Tablica LUT jest zapisana w warstwie, więc plik otwiera się na innym komputerze bez pliku LUT. Takie warstwy wymagają Krita PS.
- Stare, wyliczane warstwy Color Lookup / Selective Color są przy pierwszym otwarciu dokumentu zamieniane na warstwy filtrów (nazwa, krycie, tryb mieszania, widoczność, clipping i maski zostają). Warstwa, której pliku LUT nie da się znaleźć, zostaje po staremu. Zamiana to kilka kroków w historii Cofnij.
- Zmiana ustawień korekcji z panelu Properties nie blokuje już programu pełnym przerysowaniem obrazu: warstwa filtra odświeża się sama, asynchronicznie (łatka `krita/patches/0003`).
- Auto-odświeżanie w Krita PS nie ma nic do roboty (chyba że w dokumencie została stara warstwa bez pliku LUT).

Testy: python verification/test_native.py (14 testów, atrapa API Krity); testy kerneli C++ sprawdzają teraz także format tablicy LUT tworzony przez wtyczkę.

## Wersja en10 — clipping maski w silniku, grupowanie, PSD, wydajność (Krita PS)

- Clipping maski są częścią silnika (łatka `krita/patches/0004`), jak w Photoshopie. Przełącznik Krity „Inherit Alpha” (w panelu: „Clipping Mask”) to clipping mask: warstwa jest przycięta do najbliższej nieprzyciętej warstwy pod nią w tej samej grupie. Baza i przycięte warstwy są składane osobno (przycięta warstwa korekcyjna zmienia tylko bazę), a tryb mieszania bazy działa na cały stos („Blend Clipped Layers as Group”).
- Bez ukrytych grup „(clip)”: warstwy można przesuwać, grupować, scalać i duplikować bez rozpadania stosów, a panel warstw pokazuje je jak Photoshop (podkreślona baza, wcięcie ze strzałką). Grupy „(clip)” ze starszych dokumentów są przy pierwszym otwarciu rozbierane: warstwy wracają na miejsce grupy, baza dostaje tryb mieszania, krycie i widoczność grupy (wynik wygląda tak samo). Grupa z własną maską przy bazie, która też ma maskę, zostaje (dalej wyświetla się poprawnie).
- Clip / Release to jeden krok Cofnij (polecenie Krity). Release na przyciętej warstwie zwalnia ją i przycięte warstwy nad nią, jak w PS. Można przyciąć także warstwę bazową (dołącza do stosu pod nią). Przycięta grupa przestaje być Pass Through.
- Nawyki z Photoshopa: nowa warstwa w środku stosu albo utworzona przy zaznaczonej przyciętej warstwie jest przycinana; warstwa przeciągnięta do stosu jest przycinana, przycięta przeciągnięta poza stos — zwalniana; cały stos przesunięty razem zostaje bez zmian. Ctrl+G na warstwie ze stosu grupuje cały stos. Grupy ze stosami mogą być Pass Through.
- Clipping na dole grupy (nie ma do czego przyciąć) rysuje warstwę normalnie, jak w PS.
- PSD (łatka `0005`): eksport zapisuje flagę clipping i Blend If (blending ranges), maska Blend If nie jest już eksportowana jako maska warstwy; import odczytuje clipping i Blend If z plików Photoshopa.
- Wydajność: wtyczka nie przegląda już drzewa warstw co 300 ms — clipping reaguje na zmiany zgłaszane przez panel warstw (plus kontrola co ok. 3 s), odświeżanie panelu warstw jest wstrzymywane podczas malowania i grupowane przy zmianach miniatur, auto-odświeżanie w Krita PS prawie nic nie robi, test zajętości obrazu (blokada obrazu) nie jest wykonywany bez potrzeby.

Ograniczenia: Blend If „Underlying Layer” na warstwie bazowej stosu widzi tylko stos, nie obraz pod nim. Rozbieranie grup „(clip)” to kilka kroków w historii Cofnij.

Testy: python verification/test_clipping_native.py (18 testów, atrapa API Krity).

## Wersja en11 — poprawka: Cofnij po usuwaniu warstw

- Błąd: po usunięciu kilku warstw po kolei jedno Ctrl+Z przywracało wszystkie naraz, a w historii Cofnij pojawiało się „Property changes” zamiast „Remove nodes”. Przyczyna: Krita usuwa warstwę z opóźnieniem, w tle, a wtyczka (pilnowanie clippingu) zaglądała w tym czasie do stosu warstw, widziała stan „w połowie” i przełączała clipping. Ten dodatkowy krok cofania wchodził w drogę poleceniu usuwania.
- Teraz wtyczka czeka, aż Krita skończy polecenie. Reguły „przeciągnięta do stosu / poza stos” działają tylko przy czystym przesuwaniu warstw (usunięcie ani dodanie warstwy nigdy nie przełącza clippingu), a reguła „nowa warstwa w stosie” tylko dla jednej, świeżo utworzonej warstwy — nie dla warstw przywróconych przez Cofnij.
- Szybciej: blokada osi pędzla (Shift + przeciągnięcie) w C++, pasek opcji nie przeszukuje już wszystkich widżetów Krity, panel Properties nie kopiuje tablicy LUT kilka razy na sekundę; Instant Preview domyślnie włączony (łatka `0006`).

Testy: python verification/test_clipping_native.py (21 testów).

## Wersja en12 — Cofnij po usuwaniu warstw (silnik, łatka `0007`)

- Prawdziwa przyczyna: Krita celowo łączyła operacje na warstwach wykonane w ciągu 0,75 s (usuń, duplikuj, przesuń, w górę/w dół, grupuj) w jeden krok „Remove Nodes”, który trafiał do historii dopiero ok. 2,25 s po ostatniej operacji. Szybkie Delete, Delete, Ctrl+Z przywracało więc wszystkie usunięte warstwy naraz.
- Teraz, jak w Photoshopie, każda operacja na warstwach to osobny krok Cofnij i od razu pojawia się w historii.
- Ctrl+Z / Ctrl+Shift+Z wciśnięte, gdy Krita jeszcze kończy operację albo przelicza obraz, nie jest już ignorowane: Krita czeka na koniec pracy (zwykle ułamek sekundy) i cofa dokładnie ostatni krok. Podczas malowania pędzlem i niezakończonych transformacji zostaje zachowanie Krity.

## Wersja en13 — grupy „Pass Through” jak w Photoshopie (łatka `0008`)

- Błąd: po zgrupowaniu warstw (bez tła) warstwa z Blend If znikała, a warstwy korekcyjne w grupie przestawały działać na warstwy pod grupą. Przyczyna: grupa w Kricie jest domyślnie izolowana — jej zawartość składa się na pustym płótnie, więc Blend If „widzi” pod sobą przezroczystość, a korekcje nie sięgają poza grupę.
- Teraz nowa grupa (Ctrl+G i przycisk nowej grupy) jest „Pass Through”, tak jak w Photoshopie: Blend If, clipping i warstwy korekcyjne w grupie działają tak samo jak bez niej. Quick Clipping Group zostaje izolowana. Grupy zrobione wcześniej można przełączyć ikoną Pass Through w panelu warstw.
- Wtyczka przełącza też na Pass Through nową grupę z warstwą z Blend If (wcześniej tylko z warstwami korekcyjnymi).

## Wersja en14 — clipping do grup (łatka `0009`)

- Błąd: warstwa przycięta (clipping) nad grupą Pass Through rysowała po całym obrazie zamiast tylko po zawartości grupy.
- Teraz, jak w Photoshopie: grupa Pass Through, do której coś jest przycięte (albo która sama jest przycięta), jest rysowana jako izolowana, a przycięte warstwy przycinają się do jej zawartości. Działa też dla grup w grupach (przycięcie do grupy wewnętrznej i zewnętrznej jednocześnie). Ustawienie Pass Through grupy zostaje bez zmian — po zwolnieniu clippingu grupa znowu działa jak Pass Through. Tak samo zachowuje się Photoshop: warstwy korekcyjne i Blend If w grupie, do której coś jest przycięte, działają tylko w obrębie grupy.
- Wtyczka nie wyłącza już Pass Through przy przycinaniu grupy (to był dodatkowy krok w historii Cofnij).
- Test automatyczny: clipping do grupy, do grupy w grupie, zwolnienie, ponowne przycięcie i usunięcie przyciętej warstwy.

## Wersja en15 — Blend If na grupie (łatka `0010`)

- Błąd: Blend If ustawiony na grupie (Pass Through) nie działał.
- Teraz grupa z Blend If jest rysowana jako jedna warstwa (izolowana, jak grupa z clippingiem) i cała jej zawartość jest wygaszana względem obrazu pod grupą — także dla grup w grupach. Wyłączenie Blend If (ukrycie / usunięcie maski) przywraca Pass Through.
- PSD: Blend If grupy jest zapisywany na rekordzie grupy (nie na zamykającym „</Layer group>”), a przy imporcie grupa dostaje jedną maskę Blend If.

## Wersja en16 — Camera Raw Filter i panel Color jak w Photoshopie

- **Camera Raw Filter** (filtr C++ `pscameraraw`, Krita PS): zakładki Light (Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Curve parametryczna i punktowa RGB / R / G / B, przycisk Auto), Color (White balance: As Shot / Auto / Custom, Temperature, Tint, „Neutral = foreground colour”, Vibrance, Saturation, Color Mixer HSL dla 8 kolorów, Color Grading z kołem dla Shadows / Midtones / Highlights / Global, Blending, Balance), Effects (Texture, Clarity, Dehaze, Vignette ze stylami, Grain) i Detail (Sharpening z Radius / Detail / Masking, Noise Reduction, Color Noise Reduction).
  - Filter > Camera Raw Filter… (Shift+Ctrl+A; Deselect dostaje wtedy Ctrl+D jak w PS, jeśli Ctrl+D jest wolne) dodaje do warstwy maskę filtra „Camera Raw Filter” — jak Smart Filter: można ją otworzyć i zmienić później (Shift+Ctrl+A albo panel Properties). W menu warstw korekcyjnych jest też „Camera Raw…” (warstwa działająca na wszystko pod nią).
  - Okno jak Adobe Camera Raw: ciemne, po lewej podgląd (Fit / 25–400%, kółko myszy, dwuklik = Fit ↔ 100%, przeciąganie przy powiększeniu), po prawej histogram RGB, „Edit” z Auto i B&W, Profile (Color / Monochrome) i zwijane panele Light, Color, Effects, Curve, Color Mixer, Color Grading, Detail — każdy z oczkiem, które wyłącza jego efekt. Suwaki jak w Camera Raw (linia z trójkątem, pole wartości, dwuklik = 0). Na dole przed/po (\ albo P), reset, OK / Cancel. Płótno Krity też pokazuje zmiany na żywo.
  - B&W: czarno-biały z „B&W Mix” (jasność każdego z 8 kolorów); Color Grading działa dalej (tonowanie).
  - Algorytmy Adobe nie są publiczne — to bliskie odpowiedniki (guided filter, dark channel, OKLab); suwaki mają te same nazwy, zakresy i kierunki. Działa w 8/16-bit i float, z Instant Preview.
  - PSD: Photoshop nie ma warstwy korekcyjnej Camera Raw — taka warstwa zapisuje się w PSD jako piksele.
- **Panel „Color (PS)”** (Settings > Dockers): kwadraty koloru pierwszego planu i tła (pod nimi czarny/biały i zamiana), pole nasycenia/jasności i pasek odcienia. Kliknięcie aktywnego kwadratu otwiera **Color Picker** jak w Photoshopie: pole + pasek zależny od wybranego przycisku (H, S, B, R, G, B, L, a, b), new / current, pola H S B / R G B / L a b / C M Y K / #, Only Web Colors, Add to Swatches (próbki pod panelem: klik = kolor, Ctrl+klik = tło, Alt+klik = usuń). CMYK liczony prostym wzorem (bez profilu drukarskiego, więc liczby różnią się od Photoshopa).
- Testy: jądro Camera Raw (kafelki dają ten sam wynik co cały obraz, każdy suwak w dobrą stronę, brak NaN), panele z prawdziwym PyQt5, pole/pasek Color Pickera w każdym trybie, test w zbudowanej Kricie (warstwa i maska, 8 i 16 bit).

## Wersja en17 — poprawka: Camera Raw nie zmieniał obrazu

- Przyczyna: Filter > Camera Raw Filter dodaje do warstwy maskę filtra, a Krita po zmianie ustawień maski ze skryptu (FilterMask.setFilter) nie przerysowywała obrazu — dla warstw filtra robiła to już łatka `0003`, dla masek nie. Łatka `0011`: maska przerysowuje się sama (na żywo, w tle). Poziom silnika 6.
- Wtyczka w starszym buildzie odświeża wtedy cały obraz sama (wolniej, ale działa).
- Test w zbudowanej Kricie sprawdza dokładnie to: nowe ustawienia maski Camera Raw mają zmienić obraz bez ręcznego odświeżania.

## Wersja en18 — szybszy Camera Raw Filter (m.in. duplikowanie warstwy)

- Pomiar w prawdziwym oknie Krity (4096×4096, w pełni zamalowana warstwa): samo Duplicate Layer trwa ok. 0,06 s w Krita PS (oficjalna Krita trzyma obraz zajęty ok. 2,3 s przez opóźnione łączenie operacji). Wolne jest dopiero przeliczanie filtrów na nowej warstwie: kopia warstwy z maską Camera Raw przelicza cały filtr od nowa.
- Jądro Camera Raw jest 1,7–2,5× szybsze, z identycznym wynikiem (różnice < 0,0001): sumy okienkowe idą wierszami (przyjazne dla pamięci podręcznej), filtr prowadzony liczy tylko potrzebne średnie, Color Noise Reduction liczy średnie przewodnika raz dla obu kanałów, Vibrance/Saturation nie liczy już sin/cos, a każdy kolejny krok liczy tylko obszar, którego potrzebują kroki po nim (wcześniej wszystkie kroki liczyły kafel z pełnym marginesem ~170 px).
- Dotyczy też podglądu przy przesuwaniu suwaków.

## Wersja en19 — szybsze otwieranie PSD

- Pomiar otwierania dużego dokumentu (4096×4096, 7 warstw): .kra ok. 1–1,4 s, .psd 4–6,5 s — i to całe 4–6 s okno Krity stoi. Wtyczka nie dokłada tu nic istotnego.
- Przyczyna po stronie Krity: przy wczytywaniu PSD każdy piksel szedł przez wywołanie funkcji i wyszukiwanie każdego kanału w słowniku. Łatka 0012: warstwy RGB i Grayscale 8/16-bit są przepisywane całymi wierszami, z identycznym wynikiem.
- Test w zbudowanej Kricie: PSD zapisany i otwarty ponownie ma bajt w bajt te same piksele (RGB/szarość, 8/16 bit, warstwy przesunięte), a wydanie nie jest publikowane, gdy test dymny nie przejdzie (paczka zostaje wtedy do pobrania przy przebiegu).

## Wersja en20 — obrazy 16-bit (np. mapy głębi z Blendera)

- Łatka 0013: obraz 16-bit jest wyświetlany przez 8-bitowe tekstury karty graficznej, tak jak obraz 8-bit. Tekstury 16-bit (OpenGL: RGBA16, Direct3D/ANGLE: half float) to na wielu sterownikach wolna ścieżka — przy każdym pełnym odświeżeniu dużego obrazu 16-bit (otwarcie, duplikat warstwy) okno stawało na kilka sekund, a obraz 8-bit tej samej wielkości działał płynnie. Monitor i tak pokazuje 8 bitów. Nie dotyczy trybu HDR i OpenColorIO. Wpis „psTools16BitCanvasTextures=true” w kritarc przywraca zachowanie Krity.
- Łatka 0014: PNG w skali szarości bez profilu ICC jest w sRGB (tak zakłada PNG, Blender, Photoshop i przeglądarki). Krita dawała 16-bitowym szarym PNG liniowy profil (g10): obraz wyglądał za jasno, a konwersja na RGB zmieniała jego wartości. Teraz dostaje profil szarości sRGB; liniowy tylko gdy plik ma gAMA 1.0 bez znacznika sRGB. Test w zbudowanej Kricie sprawdza profil i niezmienione piksele.
- Łatka 0015: przy otwieraniu dokumentu Krita przelicza cały obraz na potrzeby ekranu w głównym wątku, kawałek po kawałku — okno stało: 4096×4096 PNG 8-bit ok. 0,6 s, 16-bit szary ok. 2–2,6 s (pomiar w prawdziwym oknie). Kawałki są teraz przeliczane równolegle na wszystkich rdzeniach.

## Dokumenty ze starszej wersji

Przy pierwszym otwarciu dokumentu wtyczka zamienia „Layer Mask” (lub ukryty magazyn) na maskę przezroczystości z tą samą zawartością, przywraca pełną wbudowaną maskę warstwy i przelicza Blend If. Po zapisaniu taki dokument nie będzie poprawnie obsługiwany przez starszą wersję wtyczki — przed pierwszym użyciem zrób kopię ważnych plików .kra.

## Znane ograniczenie

Na pikselach półprzezroczystych (obraz bez pełnego tła) warstwa korekcyjna w trybie Normal lekko zwiększa krycie. Przy nieprzezroczystym tle różnicy nie ma.

## Weryfikacja

python verification/test_masks.py   — 17 testów logiki masek i migracji
python verification/test_changes.py — 6 wcześniejszych testów (harmonogram, cache, NumPy)

Testy masek działają na atrapie API Krity (verification/fake_krita.py), nie na uruchomionej Kricie. W prawdziwej Kricie potwierdzone zostało jedynie samo zachowanie maski w trybie Normal (test ręczny na warstwie Invert).

## Instalacja

Zaimportuj ZIP jako aktualizację istniejącego dodatku i uruchom Kritę ponownie.
