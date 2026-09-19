param(
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $Destination) {
    $Destination = Join-Path (Split-Path $repositoryRoot -Parent) "SealGuard-Render-Backup-$timestamp"
}
$backupRoot = [System.IO.Path]::GetFullPath($Destination)
$sourceMedia = Join-Path $repositoryRoot 'media'
$sourceDatabase = Join-Path $repositoryRoot 'db.sqlite3'
$sourceKeys = Join-Path $repositoryRoot 'keys'

Write-Host 'Stop the local SealGuard server and pause uploads before continuing.'
Write-Host "Creating deployment backup at $backupRoot"

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $backupRoot 'keys') -Force | Out-Null

$sqliteBackup = @'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1], timeout=120)
destination = sqlite3.connect(sys.argv[2])
with destination:
    source.backup(destination)
result = destination.execute('PRAGMA integrity_check').fetchone()[0]
destination.close()
source.close()
if result != 'ok':
    raise SystemExit(f'Backup integrity check failed: {result}')
print('SQLite backup integrity: ok')
'@

& python -c $sqliteBackup $sourceDatabase (Join-Path $backupRoot 'db.sqlite3')
if ($LASTEXITCODE -ne 0) {
    throw 'SQLite backup failed.'
}

& robocopy $sourceMedia (Join-Path $backupRoot 'media') /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NP
if ($LASTEXITCODE -gt 7) {
    throw "Media copy failed with robocopy exit code $LASTEXITCODE."
}

Copy-Item -LiteralPath (Join-Path $sourceKeys 'private.pem') -Destination (Join-Path $backupRoot 'keys/private.pem')
Copy-Item -LiteralPath (Join-Path $sourceKeys 'public.pem') -Destination (Join-Path $backupRoot 'keys/public.pem')

$mediaFiles = Get-ChildItem (Join-Path $backupRoot 'media') -Recurse -File
$manifest = [ordered]@{
    created_at = (Get-Date).ToString('o')
    database_bytes = (Get-Item (Join-Path $backupRoot 'db.sqlite3')).Length
    database_sha256 = (Get-FileHash (Join-Path $backupRoot 'db.sqlite3') -Algorithm SHA256).Hash
    media_files = $mediaFiles.Count
    media_bytes = ($mediaFiles | Measure-Object Length -Sum).Sum
    private_key_sha256 = (Get-FileHash (Join-Path $backupRoot 'keys/private.pem') -Algorithm SHA256).Hash
    public_key_sha256 = (Get-FileHash (Join-Path $backupRoot 'keys/public.pem') -Algorithm SHA256).Hash
}
$manifest | ConvertTo-Json | Set-Content (Join-Path $backupRoot 'backup-manifest.json') -Encoding UTF8
$manifest | Format-List
Write-Host 'Backup complete. Keep this directory private and make a second copy on another drive.'
