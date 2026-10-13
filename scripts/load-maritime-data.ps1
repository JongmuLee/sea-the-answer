param(
    [Parameter(Mandatory=$true)][string]$DataDir,
    [switch]$Apply,
    [string]$Uv = 'uv'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$data = (Resolve-Path -LiteralPath $DataDir).Path
$arguments = @('run','--locked','--project',(Join-Path $repo 'backend'),
               'python','-m','scripts.load_data','--data-dir',$data)
if ($Apply) { $arguments += '--apply' }
Push-Location $repo
try {
    & $Uv @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Maritime load failed; no schema or existing dataset was overwritten.' }
} finally { Pop-Location }
