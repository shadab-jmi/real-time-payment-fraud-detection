param([ValidateRange(1,200)][int]$Rate=20,[ValidateRange(1,3600)][int]$Seconds=120)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot
Push-Location $root
try {
    $dir=New-Item -Force -ItemType Directory (Join-Path $root '.local/release')
    $job=Start-Job -ArgumentList $root,$Rate,$Seconds -ScriptBlock {
        param($root,$rate,$seconds)
        Set-Location $root
        docker compose run --rm tools load --rate $rate --seconds $seconds
        if ($LASTEXITCODE) {throw 'Load target missed; see .local/release/load.json'}
    }
    $samples=while ($job.State -in @('Running','NotStarted')) {
        $utc=(Get-Date).ToUniversalTime().ToString('o')
        docker compose --profile observe stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' |
            ForEach-Object {"$utc|$_"}
        Wait-Job $job -Timeout 5 | Out-Null
    }
    $samples | Set-Content (Join-Path $dir 'load-samples.txt')
    Receive-Job $job -ErrorAction Continue
    if ($job.State -ne 'Completed') {throw 'Benchmark failed'}
} finally {
    if ($job -and $job.State -notin @('Running','NotStarted')) {Remove-Job $job}
    Pop-Location
}
