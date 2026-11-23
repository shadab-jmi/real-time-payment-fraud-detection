$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot)
function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $args" }
}
try {
    Invoke-Docker version
    Invoke-Docker compose version
    & "$PSScriptRoot/init.ps1"
    $hash = (Get-FileHash -LiteralPath .env).Hash
    & "$PSScriptRoot/init.ps1"
    if ((Get-FileHash -LiteralPath .env).Hash -ne $hash) { throw 'Initialization changed credentials' }
    Invoke-Docker compose config --quiet
    Invoke-Docker compose --profile tools config --quiet
    Invoke-Docker compose build tools
    Invoke-Docker compose up -d --wait --wait-timeout 180 db redis kafka
    foreach ($pass in 1..2) {
        Write-Output "Provisioning pass $pass"
        Invoke-Docker compose run --rm tools migrate
        Invoke-Docker compose run --rm tools topics
    }
    Invoke-Docker compose run --rm tools check storage
    # Only a newly created, randomly named scratch database is removed.
    $scratch = 'phase1_check_' + [guid]::NewGuid().ToString('N')
    Invoke-Docker compose exec -T db createdb -U postgres -O fraud_migrator $scratch
    try {
        Invoke-Docker compose run --rm -e "PGDATABASE=$scratch" tools check upgrade
    } finally {
        Invoke-Docker compose exec -T db dropdb -U postgres $scratch
    }
    Invoke-Docker compose ps
    Write-Output 'PASS: Phase 1 checkpoint'
} finally {
    Pop-Location
}
