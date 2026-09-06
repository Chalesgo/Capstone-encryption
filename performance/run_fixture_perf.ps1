$ErrorActionPreference = 'Stop'

$locustBase = 'http://localhost:8089'
$resultDirectory = Join-Path $PSScriptRoot 'results'
New-Item -ItemType Directory -Path $resultDirectory -Force | Out-Null
$results = @()

function Get-Stats { Invoke-RestMethod "$locustBase/stats/requests" }

function Run-Window([string]$testId, [string]$band, [string]$className, [int]$users, [int]$repetition) {
    do {
        $state = (Get-Stats).state
        if ($state -ne 'stopped') { Start-Sleep -Seconds 15 }
    } while ($state -ne 'stopped')

    $null = Invoke-WebRequest "$locustBase/stats/reset" -UseBasicParsing
    $body = "user_count=$users&spawn_rate=1&run_time=60s&user_classes=$className"
    $null = curl.exe -s -X POST -H "Content-Type: application/x-www-form-urlencoded" -H "X-Requested-With: XMLHttpRequest" --data $body "$locustBase/swarm"
    $env:SEALGUARD_FIXTURE_BAND = $band

    do {
        Start-Sleep -Seconds 10
        $stats = Get-Stats
        $aggregate = $stats.stats | Where-Object name -eq 'Aggregated'
        Write-Host ("{0} {1} rep {2} | {3} users | state={4} | requests={5} | failures={6:P2}" -f `
            $testId, $band, $repetition, $users, $stats.state, $aggregate.num_requests, $stats.fail_ratio)
    } while ($stats.state -ne 'stopped')

    $aggregate = $stats.stats | Where-Object name -eq 'Aggregated'
    $script:results += [pscustomobject]@{
        test = $testId; band = $band; users = $users; repetition = $repetition
        requests = $aggregate.num_requests; failures = $aggregate.num_failures
        failure_rate = $stats.fail_ratio; average_ms = $aggregate.avg_response_time
        p95_ms = $aggregate.'response_time_percentile_0.95'
        p99_ms = $aggregate.'response_time_percentile_0.99'
        throughput_rps = $aggregate.total_rps; completed_at = (Get-Date).ToString('o')
    }
}

# PERF-04: three size rounds across the 5, 10, and 20-user levels. This is
# exactly (5 + 10 + 20) users x 3 size rounds = 105 uploads.
$bandClasses = @{
    '1mb' = 'Fixture1MBUser'
    '5mb' = 'Fixture5MBUser'
    'near_limit' = 'FixtureNearLimitUser'
}
$levels = @(5, 10, 20)
foreach ($level in $levels) {
    $repetition = 0
    foreach ($band in $bandClasses.Keys | Sort-Object) {
        $repetition++
        Run-Window 'PERF-04' $band $bandClasses[$band] $level $repetition
    }
}

# PERF-09: ten files from each size band.
foreach ($band in $bandClasses.Keys | Sort-Object) {
    Run-Window 'PERF-09' $band $bandClasses[$band] 10 1
}

$resultPath = Join-Path $resultDirectory 'perf-fixture-runs.json'
$results | ConvertTo-Json -Depth 4 | Set-Content -Path $resultPath -Encoding UTF8
Write-Host "Fixture performance runs complete. Results saved to $resultPath"
$results | Format-Table -AutoSize
