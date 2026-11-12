# Runs ci/smoke/ps_smoke.py inside the freshly built Krita PS (kritarunner,
# from the portable package - the same files people download), then checks
# the PSD it exported with ci/psd_inspect.py. Used by
# .github/workflows/build-windows.yml after the build.
$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent $PSScriptRoot
$KB   = if ($env:KB) { $env:KB } else { 'C:\kb' }
$out  = Join-Path $KB 'out\smoke'
New-Item -ItemType Directory -Force $out | Out-Null

# the portable package (made by the build step)
$pkg = Get-ChildItem (Join-Path $KB 'out') -Filter 'krita-x64-*.zip' -ErrorAction SilentlyContinue |
       Where-Object { $_.Name -notmatch '-dbg' } | Select-Object -First 1
$root = Join-Path $KB 'smoke-krita'
$runner = $null
if ($pkg) {
    if (Test-Path $root) { Remove-Item -Recurse -Force $root }
    7z x $pkg.FullName "-o$root" -y | Out-Null
    $runner = Get-ChildItem -Recurse $root -Include 'kritarunner.com', 'kritarunner_com.com' -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $runner) {
    $runner = Get-ChildItem -Recurse (Join-Path $KB 'krita\_install') -Include 'kritarunner.com', 'kritarunner_com.com' -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $runner) { Write-Host 'kritarunner(.com) not found'; exit 1 }
Write-Host "kritarunner: $($runner.FullName)"

# Krita sets up its own Python path (lib\krita-python-libs): put the script there
$pyLibs = Get-ChildItem -Recurse (Split-Path -Parent $runner.DirectoryName) -Directory -Filter 'krita-python-libs' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pyLibs) {
    Copy-Item (Join-Path $Repo 'ci\smoke\ps_smoke.py') $pyLibs.FullName -Force
    Write-Host "ps_smoke.py copied to $($pyLibs.FullName)"
}
$log = Join-Path $out 'runner.log'
$job = Start-Job -ScriptBlock {
    param($exe, $dir, $outDir, $logFile, $pp)
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Set-Location $dir
    & $exe -s ps_smoke -f main $outDir *> $logFile
    $LASTEXITCODE
} -ArgumentList $runner.FullName, $runner.DirectoryName, $out, $log, ''
if (-not (Wait-Job $job -Timeout 1200)) {
    Write-Host 'kritarunner did not finish in 20 minutes'
    Stop-Job $job
    Get-Process kritarunner* -ErrorAction SilentlyContinue | Stop-Process -Force
}
$code = Receive-Job $job -ErrorAction SilentlyContinue
Write-Host "kritarunner exit code: $code"
Write-Host '--- kritarunner output (last lines)'
Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 80
Write-Host '--- smoke.json'
Get-Content (Join-Path $out 'smoke.json') -ErrorAction SilentlyContinue

$psd = Join-Path $out 'smoke.psd'
if (-not (Test-Path $psd)) { Write-Host 'no smoke.psd was written'; exit 1 }
python (Join-Path $Repo 'ci\psd_inspect.py') $psd --check-smoke
$psdStatus = $LASTEXITCODE
$json = Get-Content (Join-Path $out 'smoke.json') -Raw | ConvertFrom-Json
if (-not $json.ok_render) { Write-Host 'FAIL: rendering checks (Blend If / clipping) did not match'; exit 1 }
if (-not $json.ok_groups) { Write-Host 'FAIL: Blend If / clipping / adjustment layers inside Pass Through groups'; exit 1 }
if ($json.engine_level -ge 6 -and -not $json.ok_cache) { Write-Host 'FAIL: the rendering speed-ups changed the pixels (see "cache" in smoke.json)'; exit 1 }
if (-not $json.ok_import) { Write-Host 'FAIL: the PSD did not import back with adjustment layers / clipping / Blend If'; exit 1 }
exit $psdStatus
