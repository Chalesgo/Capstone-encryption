[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$managePy = Join-Path $projectRoot 'manage.py'

if (-not (Test-Path -LiteralPath $managePy)) {
    throw "manage.py was not found at $managePy"
}

$ngrokCommand = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCommand) {
    throw 'ngrok is not installed or is not available on PATH. See the Remote advisor demo section in README.md.'
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

$tempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("sealguard-ngrok-{0}" -f [guid]::NewGuid())
New-Item -ItemType Directory -Path $tempDirectory | Out-Null
$ngrokOutput = Join-Path $tempDirectory 'ngrok.stdout.log'
$ngrokError = Join-Path $tempDirectory 'ngrok.stderr.log'
$djangoOutput = Join-Path $tempDirectory 'django.stdout.log'
$djangoError = Join-Path $tempDirectory 'django.stderr.log'

$ngrokProcess = $null
$djangoProcess = $null
$demoStarted = $false
$previousEnvironment = @{
    DEBUG = $env:DEBUG
    ALLOWED_HOSTS = $env:ALLOWED_HOSTS
    CSRF_TRUSTED_ORIGINS = $env:CSRF_TRUSTED_ORIGINS
}

try {
    Write-Host "Starting an ngrok tunnel for SealGuard on port $Port..."
    $ngrokProcess = Start-Process `
        -FilePath $ngrokCommand.Source `
        -ArgumentList @('http', "$Port", '--log=stdout') `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ngrokOutput `
        -RedirectStandardError $ngrokError

    $publicUrl = $null
    for ($attempt = 0; $attempt -lt 30 -and -not $publicUrl; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($ngrokProcess.HasExited) {
            $details = (Get-Content -LiteralPath $ngrokError -Tail 10 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            throw "ngrok stopped before creating a tunnel. Make sure your authtoken is configured.$([Environment]::NewLine)$details"
        }

        try {
            $tunnelResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
            $matchingTunnel = $tunnelResponse.tunnels |
                Where-Object {
                    $_.public_url -like 'https://*' -and
                    ($_.config.addr -match "(^|:)$Port$")
                } |
                Select-Object -First 1
            if ($matchingTunnel) {
                $publicUrl = $matchingTunnel.public_url.TrimEnd('/')
            }
        }
        catch {
            # The local ngrok API may need a few seconds to become available.
        }
    }

    if (-not $publicUrl) {
        throw 'Timed out while waiting for ngrok to provide a public HTTPS URL.'
    }

    $publicHost = ([uri]$publicUrl).Host
    $env:DEBUG = 'True'
    $env:ALLOWED_HOSTS = "localhost,127.0.0.1,$publicHost"
    $env:CSRF_TRUSTED_ORIGINS = $publicUrl

    Write-Host 'Starting the Django development server...'
    $djangoProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @('manage.py', 'runserver', "0.0.0.0:$Port", '--noreload') `
        -WorkingDirectory $projectRoot `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $djangoOutput `
        -RedirectStandardError $djangoError

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($djangoProcess.HasExited) {
            $details = (Get-Content -LiteralPath $djangoError -Tail 15 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            throw "Django stopped during startup.$([Environment]::NewLine)$details"
        }

        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $demoStarted = $true
            break
        }
        catch {
            # Keep polling while Django initializes.
        }
    }

    if (-not $demoStarted) {
        $details = (Get-Content -LiteralPath $djangoError -Tail 15 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        throw "Timed out while waiting for the Django development server.$([Environment]::NewLine)$details"
    }

    Write-Host ''
    Write-Host 'SealGuard is ready for the advisor demo:' -ForegroundColor Green
    Write-Host $publicUrl -ForegroundColor Cyan
    Write-Host ''
    Write-Host 'Keep this window open. Press Ctrl+C to stop Django and ngrok.'

    while (-not $djangoProcess.HasExited -and -not $ngrokProcess.HasExited) {
        Start-Sleep -Seconds 1
    }

    if ($djangoProcess.HasExited) {
        throw 'The Django development server stopped unexpectedly.'
    }
    if ($ngrokProcess.HasExited) {
        throw 'The ngrok tunnel stopped unexpectedly.'
    }
}
finally {
    foreach ($process in @($djangoProcess, $ngrokProcess)) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }

    if ($demoStarted) {
        Write-Host 'SealGuard demo stopped.'
    }

    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }

    Get-ChildItem -LiteralPath $tempDirectory -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempDirectory -Force -ErrorAction SilentlyContinue
}
