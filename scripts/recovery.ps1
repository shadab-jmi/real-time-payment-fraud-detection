param([switch]$Keep)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot)
$project = 'fraud-drill-' + [guid]::NewGuid().ToString('N').Substring(0,10)
$config = ".env.$project"
$result = [ordered]@{project=$project; started=(Get-Date).ToUniversalTime().ToString('o'); passed=@(); observations=@{}}
function DC {
    & docker compose --env-file $config -p $project -f compose.yaml -f compose.recovery.yaml @args
    if ($LASTEXITCODE) { throw "Recovery command failed: $args" }
}
function Tool { DC run --rm -T tools @args }
function Module { DC run --rm -T --entrypoint python tools -m tests.release @args }
function Sql([string]$statement) { ([string](DC exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d payments -Atc $statement)).Trim() }
function Pass([string]$name,$observed) {
    $result.passed += $name
    $result.observations[$name]=$observed
    Write-Output "PASS $name"
}
function Expect([bool]$condition,[string]$message) { if (!$condition) {throw $message} }
try {
    & "$PSScriptRoot/init.ps1" -ProjectName $project
    $hash=(Get-FileHash $config).Hash
    & "$PSScriptRoot/init.ps1" -ProjectName $project
    Expect ((Get-FileHash $config).Hash -eq $hash) 'Credential file changed'
    DC up -d --no-build --pull never --wait db redis kafka
    DC run --rm -T --entrypoint python tools -c "from packages.db.connect import engine,upgrade; c=engine.connect(); upgrade(c,'0002_evidence'); c.commit()"
    Tool topics
    Tool topics
    Tool seed-users
    Tool seed-users
    Tool train --seed 42 --rows 100000 | Out-Null
    Tool train --seed 42 --rows 100000 | Out-Null
    DC --profile observe up -d --no-build --pull never --wait api relay worker web prometheus grafana
    Expect ((Sql "SELECT count(*) FROM model_versions") -eq '1') 'Duplicate registry entry'
    Expect ((Sql "SELECT count(*) FROM users") -eq '2') 'Duplicate local users'
    Pass fresh_install 'Fresh isolated volumes, two users and one immutable model'
    DC exec -T relay touch /tmp/relay-after-ack
    DC exec -T worker touch /tmp/worker-after-commit
    Tool check pipeline
    foreach ($service in @('relay','worker')) {
        $logs=DC logs --no-color $service
        Expect ([bool]($logs -match "Recovery drill reached $service-after-")) "Crash marker was not reached: $service"
        $container=DC ps -q $service
        Expect ((docker inspect --format '{{.RestartCount}}' $container) -ge 1) "Crash restart missing: $service"
    }
    Pass crash_replay 'Both exact crash points reached, both processes restarted, parity and replay verified'
    DC stop worker
    Tool simulate --count 20 --seed 70
    $pending=Sql "SELECT count(*) FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM decisions d WHERE d.transaction_id=t.id)"
    Expect ($pending -eq '20') 'Wrong worker backlog'
    DC up -d --wait worker
    Tool reconcile
    Pass worker_backlog $pending
    DC stop redis
    Tool check pipeline
    DC up -d --wait redis
    Pass redis_fallback 'SQL history matched offline features, scores and contributions with Redis stopped'
    DC stop kafka
    Tool simulate --count 15 --seed 71
    $outbox=Sql "SELECT count(*) FROM outbox WHERE published_at IS NULL"
    Expect ([int]$outbox -ge 15) 'Broker outage lost outbox events'
    DC up -d --wait kafka
    Tool reconcile
    Pass broker_backlog $outbox
    $accepted=Sql "SELECT count(*) FROM transactions"
    DC stop db
    Module sql-outage
    DC up -d --wait db
    Expect ((Sql "SELECT count(*) FROM transactions") -eq $accepted) 'False receipt during SQL outage'
    Pass sql_503 'HTTP 503 and unchanged durable receipt count'
    DC stop relay
    Module malformed
    $qid=''
    for ($i=0;$i -lt 60 -and !$qid;$i++) {
        $qid=Sql "SELECT coalesce(max(id::text),'') FROM quarantine WHERE resolved_at IS NULL"
        if (!$qid) { Start-Sleep -Seconds 1 }
    }
    Expect ([bool]$qid) 'Missing quarantine record'
    Expect ((Sql "SELECT count(*) FROM events WHERE type='fraud.deadletter.v1'") -eq '1') 'Unexpected DLQ count'
    Tool repair-quarantine $qid
    DC up -d --wait relay
    Tool reconcile
    Expect ((Sql "SELECT count(*) FROM quarantine q JOIN decisions d ON d.transaction_id=q.transaction_id JOIN events e ON e.correlation_id=q.id AND e.type='fraud.quarantine.repaired.v1' WHERE q.resolved_at IS NOT NULL") -eq '1') 'Missing repair audit or decision'
    Pass quarantine_repair $qid
    Tool check ui
    Sql "INSERT INTO case_notes(id,case_id,author_id,body) SELECT gen_random_uuid(),v.case_id,u.id,'Synthetic reader continuity fixture' FROM v_case_evidence_v1 v CROSS JOIN users u WHERE u.role='analyst' AND EXISTS (SELECT 1 FROM transactions t WHERE t.device_id=v.device_id AND t.id<>v.transaction_id) AND NOT EXISTS (SELECT 1 FROM case_notes n WHERE n.case_id=v.case_id) LIMIT 1"
    Module reader "$project-reader.json"
    Tool migrate
    DC --profile observe down
    DC --profile observe up -d --no-build --pull never --wait db redis kafka
    DC --profile observe up -d --no-build --pull never --wait api relay worker web prometheus grafana
    Module reader "$project-reader.json"
    Pass reader_continuity "$project-reader.json"
    $network = docker network inspect --format '{{.Internal}}' "${project}_default"
    Expect ($network -eq 'true') 'Recovery runtime network has external routing'
    Tool check storage
    Tool check model | Select-Object -Last 1
    Tool check pipeline
    Pass offline_restart 'Internal-only runtime network, pull never, cold restart with original case, decision and artifact identity'
    $result.finished=(Get-Date).ToUniversalTime().ToString('o')
    $result | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 .local/release/recovery.json
} finally {
    if (!$Keep -and (Test-Path -LiteralPath $config)) {
        DC --profile observe down --volumes --remove-orphans
        Remove-Item -LiteralPath $config
    }
    Pop-Location
}
