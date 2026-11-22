# Steps 4-5 end to end for one run directory, safe to re-run after an interruption:
#   1. generation for every paper/path without a result file (sequential, like one user of the app)
#   2. only once all result files exist: the Bloom judge (resumable), the manual-sample draw (once), the metrics
# Everything is appended to <RunDir>/pipeline.log.
param([Parameter(Mandatory)][string]$RunDir)

Set-Location (Split-Path $PSScriptRoot -Parent)
$py = '.\backend\.venv\Scripts\python.exe'
$log = Join-Path $RunDir 'pipeline.log'

function Invoke-Step([string]$Name, [string[]]$Arguments) {
    Add-Content $log "== $(Get-Date -Format o) start $Name"
    & $py -I -u -W ignore @Arguments *>> $log
    Add-Content $log "== $(Get-Date -Format o) end $Name (exit $LASTEXITCODE)"
}

# the local Qdrant index allows one process at a time, so wait for any generator that is still running
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'step4_generate\.py' }
if ($running) {
    Add-Content $log "== $(Get-Date -Format o) waiting for generator pid(s) $($running.ProcessId -join ', ')"
    Wait-Process -Id $running.ProcessId -ErrorAction SilentlyContinue
}

Invoke-Step 'generate' @('eval\step4_generate.py', '--run-dir', $RunDir)

$expected = 2 * @((Get-Content 'eval\data\generation_configs.json' -Raw | ConvertFrom-Json).PSObject.Properties).Count
$have = @(Get-ChildItem (Join-Path $RunDir 'results') -Filter '*.json').Count
if ($have -lt $expected) {
    Add-Content $log "== $(Get-Date -Format o) stopping before the judge: $have of $expected result files"
    exit 1
}

Invoke-Step 'judge' @('eval\step4_judge.py', '--run-dir', $RunDir)
if (-not (Test-Path 'eval\data\bloom_manual_sample.csv')) {
    Invoke-Step 'manual sample' @('eval\step4_manual_sample.py', '--run-dir', $RunDir)
}
Invoke-Step 'metrics' @('eval\step45_metrics.py', '--run-dir', $RunDir)
Add-Content $log "== $(Get-Date -Format o) pipeline finished"
