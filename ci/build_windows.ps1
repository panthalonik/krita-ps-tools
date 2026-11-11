# Builds "Krita PS" (Krita + this repository's changes) for Windows, the same
# way KDE's own CI builds Krita releases (krita-deps-management +
# run-ci-build.py + build-windows-package.py). Used by
# .github/workflows/build-windows.yml; can also be run by hand from a
# PowerShell 7 window on a Windows 10/11 machine with Git, Python 3.13 and
# 7-Zip installed:   pwsh ci\build_windows.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true      # a failing exe stops the script

$Repo      = Split-Path -Parent $PSScriptRoot
$KB        = if ($env:KB) { $env:KB } else { 'C:\kb' }
$KritaSha  = if ($env:KRITA_SHA) { $env:KRITA_SHA } else { 'bd6e8d061d2f0388c5b10278786cf0fe62e3135f' }
$KritaUrl  = 'https://invent.kde.org/graphics/krita.git'
$KritaUrlMirror = 'https://github.com/KDE/krita.git'
$LlvmName  = 'llvm-mingw-20251118-ucrt-x86_64'
$LlvmUrl   = "https://github.com/mstorsjo/llvm-mingw/releases/download/20251118/$LlvmName.zip"
$NinjaUrl  = 'https://github.com/ninja-build/ninja/releases/download/v1.12.1/ninja-win.zip'
$CcacheVer = '4.10.2'
$CcacheUrl = "https://github.com/ccache/ccache/releases/download/v$CcacheVer/ccache-$CcacheVer-windows-x86_64.zip"
$Src       = Join-Path $KB 'krita'

function Step($text) { Write-Host ''; Write-Host "===== $text =====" -ForegroundColor Cyan }

New-Item -ItemType Directory -Force $KB, "$KB\bin", "$KB\cache", "$KB\ccache" | Out-Null
git config --global core.longpaths true

Step 'tools: llvm-mingw (clang), ninja, ccache'
if (-not (Test-Path "$KB\llvm-mingw\bin\clang++.exe")) {
    curl.exe -L --fail --retry 3 -o "$KB\llvm.zip" $LlvmUrl
    7z x "$KB\llvm.zip" "-o$KB" -y | Out-Null
    Rename-Item "$KB\$LlvmName" 'llvm-mingw'
    Remove-Item "$KB\llvm.zip"
}
if (-not (Test-Path "$KB\bin\ninja.exe")) {
    curl.exe -L --fail --retry 3 -o "$KB\ninja.zip" $NinjaUrl
    7z x "$KB\ninja.zip" "-o$KB\bin" -y | Out-Null
    Remove-Item "$KB\ninja.zip"
}
if (-not (Test-Path "$KB\bin\ccache.exe")) {
    curl.exe -L --fail --retry 3 -o "$KB\ccache.zip" $CcacheUrl
    7z x "$KB\ccache.zip" "-o$KB\ccache-tmp" -y | Out-Null
    Copy-Item (Get-ChildItem "$KB\ccache-tmp" -Recurse -Filter ccache.exe | Select-Object -First 1).FullName "$KB\bin\"
    Remove-Item -Recurse "$KB\ccache-tmp", "$KB\ccache.zip"
}
# only our compiler: other MinGW / Strawberry gcc on the runner would be picked up by CMake
$clean = $env:PATH -split ';' | Where-Object { $_ -and $_ -notmatch 'mingw|strawberry|msys|cygwin|\\Git\\usr\\bin' }
$env:PATH = (@("$KB\llvm-mingw\bin", "$KB\llvm-mingw\x86_64-w64-mingw32\bin", "$KB\bin") + $clean) -join ';'
$env:CC = 'clang'
$env:CXX = 'clang++'
$env:MINGW_BIN_DIR = "$KB\llvm-mingw\bin"
clang++ --version
ninja --version
ccache --version | Select-Object -First 1

Step "Krita source at $KritaSha"
if (-not (Test-Path "$Src\CMakeLists.txt")) {
    New-Item -ItemType Directory -Force $Src | Out-Null
    Push-Location $Src
    git init -q .
    git remote add origin $KritaUrl
    git fetch -q --depth 1 origin $KritaSha
    if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
    git checkout -q FETCH_HEAD
    Pop-Location
}
python "$Repo\ci\prepare_source.py" --krita $Src --repo $Repo

Step 'KDE build tools (krita-deps-management, krita-ci-utilities)'
Push-Location $Src
if (-not (Test-Path 'krita-deps-management\tools\setup-env.py')) {
    git clone -q --depth 1 https://invent.kde.org/packaging/krita-deps-management.git
}
if (-not (Test-Path 'krita-deps-management\ci-utilities\run-ci-build.py')) {
    git clone -q --depth 1 https://invent.kde.org/packaging/krita-ci-utilities.git krita-deps-management\ci-utilities
}
if (-not (Test-Path "$KB\venv\Scripts\python.exe")) { python -m venv "$KB\venv" --upgrade-deps }
& "$KB\venv\Scripts\Activate.ps1"
python -m pip install -q -r krita-deps-management\requirements.txt
python -m pip install -q -r build-tools\ci-scripts\requirements-windows.txt

Step 'dependencies + build (official KDE CI scripts)'
$env:KDECI_BUILD_TYPE = 'Release'
$env:KDECI_CC_CACHE = "$KB\ccache"
$env:KDECI_CACHE_PATH = "$KB\cache"
$env:KDECI_GITLAB_SERVER = 'https://invent.kde.org/'
$env:KDECI_PACKAGE_PROJECT = 'teams/ci-artifacts/krita-windows'
$env:KDECI_REPO_METADATA_PATH = "$Src\krita-deps-management\repo-metadata"
$env:KDECI_COMPRESS_PACKAGES_ON_DOWNLOAD = 'False'
$env:KDECI_EXTRA_CMAKE_ARGS = '-DHIDE_SAFE_ASSERTS=ON -DBUILD_TESTING=OFF'
$env:KRITACI_SKIP_DEBUG_PACKAGE = 'True'
$env:KRITACI_BUILD_INSTALLERS = 'False'
$env:KRITACI_SKIP_BUILD_3RDPARTY_PLUGINS = if ($env:KRITACI_SKIP_BUILD_3RDPARTY_PLUGINS) { $env:KRITACI_SKIP_BUILD_3RDPARTY_PLUGINS } else { 'True' }
$env:PYTHONUTF8 = '1'
$env:CCACHE_COMPRESS = '1'
$env:CI_COMMIT_SHORT_SHA = if ($env:GITHUB_SHA) { $env:GITHUB_SHA.Substring(0, 8) } else { 'local' }
python -u krita-deps-management\tools\replace-branch-in-seed-file.py krita-deps-management\latest\krita-deps.yml -p -o branch-corrected-deps.yml -d master
python -u krita-deps-management\tools\generate-deps-file.py -s branch-corrected-deps.yml -o .kde-ci.yml
python -u krita-deps-management\ci-utilities\run-ci-build.py --project krita --branch master --platform Windows/Qt5/Shared --skip-publishing

Step 'checking that the ps filters plugin was built'
$dll = Get-ChildItem -Recurse "$Src\_install" -Filter 'kritapsfilters.dll' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $dll) { throw 'kritapsfilters.dll is missing from the install folder' }
Write-Host "found $($dll.FullName)"

Step 'package (portable zip)'
Get-ChildItem $Src -Filter 'krita-x64-*.zip' | Remove-Item -ErrorAction SilentlyContinue
python -u build-tools\ci-scripts\build-windows-package.py
$zip = Get-ChildItem $Src -Filter 'krita-x64-*.zip' | Where-Object { $_.Name -notmatch '-dbg' } | Select-Object -First 1
if (-not $zip) { throw 'package zip not found' }
Pop-Location
New-Item -ItemType Directory -Force "$KB\out" | Out-Null
Copy-Item $zip.FullName "$KB\out\"
Write-Host "PACKAGE: $KB\out\$($zip.Name)"
