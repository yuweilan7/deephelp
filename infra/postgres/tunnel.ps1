param([ValidateSet('start','health','stop')][string]$Action = 'health')
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$stateDir = Join-Path $workspace '.local/postgres'
$stateFile = Join-Path $stateDir 'tunnel.json'
$knownHosts = Join-Path $workspace 'infra/client/known_hosts'
$settings = @{}
Get-Content -LiteralPath (Join-Path $workspace 'infra/config/connections.env') | ForEach-Object {
    if ($_ -match '^([A-Z_0-9]+)=(.*)$') { $settings[$matches[1]] = $matches[2].Trim().Trim('"') }
}
function OwnedProcess {
    if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
    $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $candidate) { return $null }
    if ($candidate.Name -ne 'ssh.exe' -or
        -not $candidate.CommandLine.Contains('127.0.0.1:15432:127.0.0.1:15432') -or
        -not $candidate.CommandLine.Contains($knownHosts)) { throw 'Saved PID is no longer this PG tunnel' }
    return $candidate
}
function PortReady {
    $socket = [Net.Sockets.TcpClient]::new()
    try {
        $connect = $socket.ConnectAsync('127.0.0.1',15432)
        if (-not $connect.Wait(2000)) { return $false }
        return $socket.Connected
    } catch { return $false } finally { $socket.Dispose() }
}
if ($Action -eq 'stop') {
    $owned = OwnedProcess
    if ($owned) { Stop-Process -Id $owned.ProcessId; Wait-Process -Id $owned.ProcessId -Timeout 5 -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    Write-Output 'PG tunnel stopped; existing infrastructure tunnels are independent'
    exit 0
}
if ($Action -eq 'start') {
    $owned = OwnedProcess
    if (-not $owned) {
        if (PortReady) { throw 'Port 15432 is owned elsewhere; will not replace that process' }
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        foreach ($value in @($knownHosts,$settings['DEEPHELP_SSH_KEY_WINDOWS'])) {
            if ($value.Contains('"')) { throw 'Invalid SSH path' }
        }
        $sshArguments = @('-N','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes',
            '-o','KexAlgorithms=curve25519-sha256','-o',('"UserKnownHostsFile='+$knownHosts+'"'),
            '-o','ExitOnForwardFailure=yes','-i',('"'+$settings['DEEPHELP_SSH_KEY_WINDOWS']+'"'),
            '-p',$settings['DEEPHELP_SSH_PORT'],'-L','127.0.0.1:15432:127.0.0.1:15432',
            ($settings['DEEPHELP_SSH_USER']+'@'+$settings['DEEPHELP_SSH_HOST']))
        $child = Start-Process ssh -ArgumentList $sshArguments -WindowStyle Hidden -PassThru
        @{pid=$child.Id;port=15432} | ConvertTo-Json | Set-Content -LiteralPath $stateFile
        $limit = [DateTime]::UtcNow.AddSeconds(10)
        while (-not (PortReady)) {
            if ($child.HasExited -or [DateTime]::UtcNow -gt $limit) { throw 'PG tunnel did not start; inspect owned process' }
            Start-Sleep -Milliseconds 100
        }
    }
}
if (-not (PortReady)) { throw 'PG forwarding port is unavailable' }
Write-Output 'PASS: PG forwarding port 15432; database semantics require the explicit probe'
