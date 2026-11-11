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

$env:PYTHONPATH = (Join-Path $Repo 'ci\smoke') + ';' + $env:PYTHONPATH
$log = Join-Path $out 'runner.log'
$job = Start-Job -ScriptBlock {
    param($exe, $dir, $outDir, $logFile, $pp)
    $env:PYTHONPATH = $pp
    Set-Location $dir
    & $exe -s ps_smoke -f main $outDir *> $logFile
    $LASTEXITCODE
} -ArgumentList $runner.FullName, $runner.DirectoryName, $out, $log, $env:PYTHONPATH
if (-not (Wait-Job $job -Timeout 420)) {
    Write-Host 'kritarunner did not finish in 7 minutes'
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
if (-not $json.ok_import) { Write-Host 'FAIL: the PSD did not import back with adjustment layers / clipping / Blend If'; exit 1 }
exit $psdStatus
