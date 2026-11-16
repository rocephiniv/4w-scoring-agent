param([switch]$Tests)
$ErrorActionPreference="Stop"
$root=Split-Path $PSScriptRoot -Parent
$python=Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONDONTWRITEBYTECODE="1"
& $python -B (Join-Path $PSScriptRoot "run_synthetic_demo.py") verify
if($LASTEXITCODE -ne 0){throw "Package verification failed"}
if($Tests){
 $receipt=Get-Content -LiteralPath (Join-Path $root ".install\receipt.json") -Raw|ConvertFrom-Json
 $env:FOURW_NODE_BINARY=$receipt.node
 $env:FOURW_ARTIFACT_RUNTIME=$receipt.artifact_runtime
 $env:PYTHONPATH=$root
 Push-Location $root
 try {& $python -B -m pytest tests -q -p no:cacheprovider; if($LASTEXITCODE -ne 0){throw "Synthetic tests failed"}} finally {Pop-Location}
}
