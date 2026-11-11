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

Ograniczenia: warstwa przycięta do grupy Pass Through (i grupa Pass Through jako przycięta warstwa) korzysta ze starego zachowania Krity „inherit alpha”. Blend If „Underlying Layer” na warstwie bazowej stosu widzi tylko stos, nie obraz pod nim. Rozbieranie grup „(clip)” to kilka kroków w historii Cofnij.

Testy: python verification/test_clipping_native.py (18 testów, atrapa API Krity).

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
