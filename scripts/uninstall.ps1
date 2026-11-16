$ErrorActionPreference="Stop"
$root=Split-Path $PSScriptRoot -Parent
$python=Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONDONTWRITEBYTECODE="1"
$lockPath=Join-Path $root ".install\mutation.lock"
# Verify before taking the outer lock; take the same byte lock before deletion,
# then verify again under the lock through read-only verification.
& $python -B (Join-Path $PSScriptRoot "run_synthetic_demo.py") uninstall
if($LASTEXITCODE -ne 0){throw "Uninstall refused; reconcile modified installation"}
$lock=[IO.File]::Open($lockPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try {
 $lock.Lock(0,1)
 $receiptPath=Join-Path $root ".install\receipt.json"
 $receipt=Get-Content -LiteralPath $receiptPath -Raw|ConvertFrom-Json
 $target=[IO.Path]::GetFullPath((Join-Path $root ".venv"))
 if([IO.Path]::GetDirectoryName($target) -ne [IO.Path]::GetFullPath($root)){throw "Unsafe uninstall target"}
 if((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Redirected uninstall target"}
 # Confirm package and installed files again while holding the common lock.
 & $python -B (Join-Path $PSScriptRoot "run_synthetic_demo.py") verify
 if($LASTEXITCODE -ne 0){throw "Installation changed before uninstall"}
 foreach($item in $receipt.environment_files.psobject.Properties){
  $file=Join-Path $target $item.Name
  $sha=[Security.Cryptography.SHA256]::Create()
  try {$hash=[BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($file))).Replace("-","")} finally {$sha.Dispose()}
  if($hash -ne $item.Value){throw "Modified environment preserved"}
 }
 Remove-Item -LiteralPath $target -Recurse -Force
 if(Test-Path -LiteralPath $target){throw "Environment deletion incomplete"}
 $retired=Join-Path $root (".install\uninstalled-"+[guid]::NewGuid().ToString("N")+".json")
 Move-Item -LiteralPath $receiptPath -Destination $retired
 [pscustomobject]@{status="uninstalled_verified";path=$target}|ConvertTo-Json -Compress
} finally {
 try {$lock.Unlock(0,1)} finally {$lock.Dispose()}
}
