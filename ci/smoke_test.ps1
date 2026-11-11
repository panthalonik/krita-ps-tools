# Runs ci/smoke/ps_smoke.py inside the freshly built Krita PS (kritarunner),
# then checks the PSD it exported with ci/psd_inspect.py. Used by
# .github/workflows/build-windows.yml after the build.
$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent $PSScriptRoot
$KB   = if ($env:KB) { $env:KB } else { 'C:\kb' }
$out  = Join-Path $KB 'out\smoke'
New-Item -ItemType Directory -Force $out | Out-Null

$runner = Get-ChildItem -Recurse (Join-Path $KB 'krita\_install') -Filter 'kritarunner.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $runner) { Write-Host 'kritarunner.exe not found'; exit 1 }
Write-Host "kritarunner: $($runner.FullName)"

$env:PYTHONPATH = (Join-Path $Repo 'ci\smoke') + ';' + $env:PYTHONPATH
$env:PATH = $runner.DirectoryName + ';' + $env:PATH
$p = Start-Process -FilePath $runner.FullName -ArgumentList @('-s', 'ps_smoke', '-f', 'main', $out) `
        -WorkingDirectory $runner.DirectoryName -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $out 'runner.out') -RedirectStandardError (Join-Path $out 'runner.err')
if (-not $p.WaitForExit(300000)) {
    Write-Host 'kritarunner did not finish in 5 minutes'
    $p.Kill()
}
Write-Host '--- kritarunner output (last lines)'
Get-Content (Join-Path $out 'runner.out'), (Join-Path $out 'runner.err') -ErrorAction SilentlyContinue | Select-Object -Last 60
Write-Host '--- smoke.json'
Get-Content (Join-Path $out 'smoke.json') -ErrorAction SilentlyContinue

$psd = Join-Path $out 'smoke.psd'
if (-not (Test-Path $psd)) { Write-Host 'no smoke.psd was written'; exit 1 }
python (Join-Path $Repo 'ci\psd_inspect.py') $psd --check-smoke
$psdStatus = $LASTEXITCODE
$json = Get-Content (Join-Path $out 'smoke.json') -Raw | ConvertFrom-Json
if (-not $json.ok_render) { Write-Host 'FAIL: rendering checks (Blend If / clipping) did not match'; exit 1 }
if (-not $json.ok_import) { Write-Host 'FAIL: the PSD did not import back with adjustment layers / clipping / Blend If'; exit 1 }
exit $psdStatus
