$ErrorActionPreference = 'Stop'

$locustBase = 'http://localhost:8089'
$levels = @(10, 20, 30, 50)
$results = @()

function Get-Stats {
    Invoke-RestMethod "$locustBase/stats/requests"
}

function Start-Level([int]$userCount) {
    Invoke-WebRequest "$locustBase/stats/reset" -UseBasicParsing | Out-Null
    $body = "user_count=$userCount&spawn_rate=1&run_time=5m&user_classes=MixedWorkloadUser"
    $response = curl.exe -s -X POST -H "Content-Type: application/x-www-form-urlencoded" --data $body "$locustBase/swarm"
    $responseText = $response -join ''
    if ($responseText -notmatch '"success"\s*:\s*true') {
        throw "Locust did not start level ${userCount}: $response"
    }

    do {
        Start-Sleep -Seconds 15
        $stats = Get-Stats
        Write-Host ("{0} users | state={1} | requests={2} | failures={3:P2} | rps={4:N2}" -f `
            $userCount, $stats.state, ($stats.stats | Where-Object name -eq 'Aggregated').num_requests,
            $stats.fail_ratio, $stats.total_rps)
    } while ($stats.state -ne 'stopped')

    $aggregate = $stats.stats | Where-Object name -eq 'Aggregated'
    $script:results += [pscustomobject]@{
        users = $userCount
        requests = $aggregate.num_requests
        failures = $aggregate.num_failures
        failure_rate = $stats.fail_ratio
        average_ms = $aggregate.avg_response_time
        p95_ms = $aggregate.'response_time_percentile_0.95'
        p99_ms = $aggregate.'response_time_percentile_0.99'
        throughput_rps = $aggregate.total_rps
        completed_at = (Get-Date).ToString('o')
    }
}

Invoke-WebRequest "$locustBase/stop" -UseBasicParsing | Out-Null
foreach ($level in $levels) {
    Write-Host "Starting PERF-02 level: $level users"
    Start-Level $level
}

$resultDirectory = Join-Path $PSScriptRoot 'results'
New-Item -ItemType Directory -Path $resultDirectory -Force | Out-Null
$resultPath = Join-Path $resultDirectory 'perf02-load-ramp.json'
$results | ConvertTo-Json -Depth 4 | Set-Content -Path $resultPath -Encoding UTF8
Write-Host "PERF-02 complete. Results saved to $resultPath"
$results | Format-Table -AutoSize
