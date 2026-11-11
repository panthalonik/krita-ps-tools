# Krita PS

Krita 5.3.4.1 z narzędziami w stylu Photoshopa: wtyczka `ps_tools` (panel
warstw, clipping maski, maski, Blend If, warstwy korekcyjne) oraz zmiany w C++
(prawdziwe filtry Color Lookup, Selective Color, Blend If; poprawki silnika).

## Jak pobrać i uruchomić

1. Wejdź w **Releases** (prawa kolumna na stronie repozytorium) i wybierz
   najnowszy build.
2. Pobierz plik `Krita-PS-5.3.4.1-buildN.zip` i rozpakuj go w dowolnym
   folderze (np. `C:\Krita-PS`).
3. Uruchom `bin\krita.exe` z rozpakowanego folderu.

Oficjalna Krita zostaje nietknięta. Obie wersje korzystają z tych samych
ustawień, zasobów i wtyczek Pythona (folder `%APPDATA%\krita`), więc wtyczka
`ps_tools` zainstalowana w Kricie działa też w Krita PS.

Nową wersję samej wtyczki (`ps_tools_plugin.zip` z tego samego wydania)
instaluje się w Kricie przez *Tools → Scripts → Import Python Plugin from
File* i ponowne uruchomienie.

## Co jest w repozytorium

| Folder | Zawartość |
|---|---|
| `plugin/` | wtyczka Pythona `ps_tools` i jej testy |
| `krita/` | pliki dokładane do źródeł Krity (np. `plugins/filters/psfilters`) i łatki silnika (`patches/*.patch`) |
| `ci/` | skrypty budowania (`build_windows.ps1`, `prepare_source.py`) |
| `.github/workflows/` | budowanie dla Windows na serwerach GitHuba i testy |

Budowanie odtwarza oficjalny proces KDE (krita-deps-management,
`run-ci-build.py`, `build-windows-package.py`) z kompilatorem llvm-mingw.

Krita jest na licencji GPL; zmiany w tym repozytorium również.
