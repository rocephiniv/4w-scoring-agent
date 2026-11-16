param([string]$Python="python",[string]$Node="node",[string]$ArtifactRuntime="",[switch]$Development)
$ErrorActionPreference="Stop"
$root=Split-Path $PSScriptRoot -Parent
$env:PYTHONDONTWRITEBYTECODE="1"
$extra=@()
if($Development){$extra+= "--development"}
if($ArtifactRuntime -ne ""){$extra+= @("--artifact-runtime",$ArtifactRuntime)}
& $Python -B (Join-Path $PSScriptRoot "run_synthetic_demo.py") install --node $Node @extra
if($LASTEXITCODE -ne 0){throw "Installation incomplete. Preserve diagnostics and reconcile before retrying."}
