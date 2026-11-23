param([ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$ProjectName = 'payment-fraud')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$name = if ($ProjectName -eq 'payment-fraud') { '.env' } else { ".env.$ProjectName" }
$config = Join-Path $root $name
if (!(Test-Path -LiteralPath $config)) {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $temp = Join-Path $root (".env." + [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllText($temp, '')
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = Get-Acl -LiteralPath $temp
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
        Set-Acl -LiteralPath $temp -AclObject $acl
        $lines = foreach ($line in [IO.File]::ReadAllLines((Join-Path $root '.env.example'))) {
            if ($line -like 'COMPOSE_PROJECT_NAME=*') { "COMPOSE_PROJECT_NAME=$ProjectName" }
            elseif ($line -match '^[A-Z_]+=$') {
                $bytes = New-Object byte[] 32
                $rng.GetBytes($bytes)
                $line + [BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant()
            } else { $line }
        }
        [IO.File]::WriteAllLines($temp, [string[]]$lines, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temp, $config)
    } finally {
        $rng.Dispose()
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    }
}
Write-Output "Credential file: $config"
