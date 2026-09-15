$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:DEBUG = 'False'
python manage.py verify_integrity --quiet-success
