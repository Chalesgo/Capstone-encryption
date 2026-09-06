$ErrorActionPreference = 'Stop'

$locustBase = 'http://localhost:8089'
$resultDirectory = Join-Path $PSScriptRoot 'results'
New-Item -ItemType Directory -Path $resultDirectory -Force | Out-Null
$results = @()

function Get-Stats {
    Invoke-RestMethod "$locustBase/stats/requests"
}

function Run-Locust([string]$testId, [string]$className, [int]$users, [int]$spawnRate, [string]$runTime, [int]$repetition) {
    $null = curl.exe -s http://localhost:8089/stop
    $null = Invoke-WebRequest "$locustBase/stats/reset" -UseBasicParsing
    $body = "user_count=$users&spawn_rate=$spawnRate&run_time=$runTime&user_classes=$className"
    $response = curl.exe -s -X POST -H "Content-Type: application/x-www-form-urlencoded" --data $body "$locustBase/swarm"
    $responseText = $response -join ''
    if ($responseText -notmatch '"success"\s*:\s*true') {
        throw "Locust did not start $testId repetition ${repetition}: $responseText"
    }

    do {
        Start-Sleep -Seconds 15
        $stats = Get-Stats
        $aggregate = $stats.stats | Where-Object name -eq 'Aggregated'
        Write-Host ("{0} rep {1} | {2} users | state={3} | requests={4} | failures={5:P2} | rps={6:N2}" -f `
            $testId, $repetition, $users, $stats.state, $aggregate.num_requests, $stats.fail_ratio, $aggregate.total_rps)
    } while ($stats.state -ne 'stopped')

    $aggregate = $stats.stats | Where-Object name -eq 'Aggregated'
    $script:results += [pscustomobject]@{
        test = $testId
        repetition = $repetition
        users = $users
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

# PERF-03: 30 concurrent login users, repeated three times.
1..3 | ForEach-Object { Run-Locust 'PERF-03' 'LoginUser' 30 1 '30s' $_ }

# PERF-05: focused verification workload. The formal 1,600-request count is
# checked from the aggregate request count rather than inferred from time.
Run-Locust 'PERF-05' 'VerificationUser' 50 1 '15m' 1

# PERF-07: 5 -> 50 users within 10 seconds, repeated three times.
1..3 | ForEach-Object { Run-Locust 'PERF-07' 'MixedWorkloadUser' 50 5 '2m' $_ }

# PERF-08: endurance run; this intentionally takes two hours.
Run-Locust 'PERF-08' 'MixedWorkloadUser' 10 1 '2h' 1

$resultPath = Join-Path $resultDirectory 'perf-remaining.json'
$results | ConvertTo-Json -Depth 4 | Set-Content -Path $resultPath -Encoding UTF8
Write-Host "Remaining automated performance runs complete. Results saved to $resultPath"
$results | Format-Table -AutoSize
