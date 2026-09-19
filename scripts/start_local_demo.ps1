[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8443
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$managePy = Join-Path $projectRoot 'manage.py'
$certGenerator = Join-Path $PSScriptRoot 'generate_local_cert.py'
$localDirectory = Join-Path $projectRoot '.local-dev'
$certificatePath = Join-Path $localDirectory 'local-cert.pem'
$keyPath = Join-Path $localDirectory 'local-key.pem'

if (-not (Test-Path -LiteralPath $managePy)) {
    throw "manage.py was not found at $managePy"
}

$pythonCandidates = @(
    (Join-Path $projectRoot 'venv\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv\Scripts\python.exe')
)
$pythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python is not installed or is not available on PATH.'
    }
    $pythonExe = $pythonCommand.Source
}

$lanAddress = Get-NetIPConfiguration |
    Where-Object { $_.IPv4Address -and $_.IPv4DefaultGateway } |
    ForEach-Object { $_.IPv4Address.IPAddress } |
    Where-Object { $_ -notmatch '^(127\.|169\.254\.)' } |
    Select-Object -First 1

if (-not $lanAddress) {
    throw 'No active LAN IPv4 address was found. Connect the laptop to Wi-Fi or Ethernet first.'
}

$previousEnvironment = @{
    DEBUG = $env:DEBUG
    ALLOWED_HOSTS = $env:ALLOWED_HOSTS
    CSRF_TRUSTED_ORIGINS = $env:CSRF_TRUSTED_ORIGINS
    PUBLIC_BASE_URL = $env:PUBLIC_BASE_URL
    SEALGUARD_RUN_STARTUP_INTEGRITY = $env:SEALGUARD_RUN_STARTUP_INTEGRITY
}

try {
    $env:DEBUG = 'True'
    $env:ALLOWED_HOSTS = "localhost,127.0.0.1,$lanAddress"
    $localUrl = "https://{0}:{1}" -f $lanAddress, $Port
    $env:CSRF_TRUSTED_ORIGINS = $localUrl
    $env:PUBLIC_BASE_URL = $localUrl
    $env:SEALGUARD_RUN_STARTUP_INTEGRITY = '0'

    $existingListener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingListener) {
        Write-Host ''
        Write-Host "SealGuard is already running at:" -ForegroundColor Yellow
        Write-Host "$localUrl/verify/physical/" -ForegroundColor Cyan
        Write-Host "Port $Port is already in use by process $($existingListener.OwningProcess)."
        Write-Host 'Use the existing server, or press Ctrl+C in its original terminal before launching again.'
        return
    }

    if (-not (Test-Path -LiteralPath $certificatePath) -or -not (Test-Path -LiteralPath $keyPath)) {
        & $pythonExe $certGenerator --host $lanAddress --cert $certificatePath --key $keyPath
        if ($LASTEXITCODE -ne 0) { throw 'Local HTTPS certificate generation failed.' }
    }

    Write-Host "Checking Django configuration..."
    & $pythonExe $managePy check
    if ($LASTEXITCODE -ne 0) { throw 'Django configuration check failed.' }

    Write-Host 'Applying database migrations...'
    & $pythonExe $managePy migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Database migrations failed.' }

    Write-Host ''
    Write-Host 'SealGuard is ready for local phone testing:' -ForegroundColor Green
    Write-Host $localUrl -ForegroundColor Cyan
    Write-Host 'The phone and laptop must be on the same Wi-Fi/router.'
    Write-Host 'Keep this window open. Press Ctrl+C to stop the server.'
    Write-Host ''

    & $pythonExe -m uvicorn capstone_system.asgi:application --host 0.0.0.0 --port $Port --ssl-keyfile $keyPath --ssl-certfile $certificatePath
}
finally {
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}
