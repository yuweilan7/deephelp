param([ValidateSet('start','stop','restart','check','health')][string]$Action='start')
$ErrorActionPreference='Stop'
$base = Split-Path $PSScriptRoot -Parent
$cfgPath=Join-Path $base 'config/connections.env'
if (-not (Test-Path -LiteralPath $cfgPath)) {$cfgPath=Join-Path $base 'config/connections.env.example'}
$cfg=@{}
Get-Content -LiteralPath $cfgPath | ForEach-Object {if ($_ -match '^([A-Z_]+)=(.*)$') {$cfg[$matches[1]]=$matches[2]}}
$pidPath=Join-Path $PSScriptRoot '.tunnel-windows.json'
function Get-ManagedTunnel {
 if(Test-Path -LiteralPath $pidPath){
  $record=Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
  $p=Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)"
  $expectedHost=if($record.host){$record.host}else{$cfg.DEEPHELP_SSH_HOST}
  if($p -and $p.Name -eq 'ssh.exe' -and $p.CommandLine.Contains($expectedHost) -and $p.CommandLine.Contains('127.0.0.1:3306') -and $p.CommandLine.Contains('ServerAliveInterval=30')){return $p}
 }
 return $null
}
if($Action -in 'stop','restart'){
 $p=Get-ManagedTunnel
 if($p){Stop-Process -Id $p.ProcessId; Write-Output 'Managed tunnel stopped.'}
 if(Test-Path -LiteralPath $pidPath){Remove-Item -LiteralPath $pidPath}
 if($Action -eq 'stop'){exit 0}
}
$ports=@([int]$cfg.MYSQL_PORT,[int]$cfg.REDIS_PORT,[int]$cfg.MILVUS_LOCAL_PORT,[int]$cfg.MILVUS_WEBUI_PORT)
if($Action -in 'check','health'){
 foreach($port in $ports){$c=[Net.Sockets.TcpClient]::new(); try{$ok=$c.ConnectAsync('127.0.0.1',$port).Wait(1500); if(-not $ok -or -not $c.Connected){throw "Port $port unavailable"}; Write-Output "127.0.0.1:$port reachable"}finally{$c.Dispose()}}
 if($Action -eq 'health'){
  $python=Join-Path $base '.venv312/Scripts/python.exe'
  if(-not(Test-Path -LiteralPath $python)){$python=Join-Path $base '.venv/Scripts/python.exe'}
  & $python (Join-Path $PSScriptRoot 'test-connections.py') health
  if($LASTEXITCODE -ne 0){throw 'Authenticated health failed'}
 }
 exit 0
}
if(Get-ManagedTunnel){Write-Output 'Managed tunnel already running.';exit 0}
foreach($port in $ports){if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){throw "Local port $port occupied. Edit config/connections.env and update endpoint URI variables together."}}
$key=$cfg.DEEPHELP_SSH_KEY_WINDOWS
if(-not(Test-Path -LiteralPath $key)){throw "SSH key missing: $key"}
$sshArgs=@('-N','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o',('UserKnownHostsFile='+ (Join-Path $PSScriptRoot 'known_hosts')),'-o','KexAlgorithms=curve25519-sha256','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','ConnectTimeout=12','-i',$key,'-p',$cfg.DEEPHELP_SSH_PORT)
$remotePorts=@(3306,6379,19530,9091)
for($i=0;$i -lt 4;$i++){$sshArgs+=@('-L',"127.0.0.1:$($ports[$i]):127.0.0.1:$($remotePorts[$i])")}
$sshArgs+=($cfg.DEEPHELP_SSH_USER+'@'+$cfg.DEEPHELP_SSH_HOST)
$quoted=$sshArgs | ForEach-Object {'"'+$_.Replace('"','\"')+'"'}
$p=Start-Process -FilePath (Get-Command ssh.exe).Source -ArgumentList $quoted -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $PSScriptRoot '.tunnel-windows.log')
@{pid=$p.Id;host=$cfg.DEEPHELP_SSH_HOST;started=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $pidPath
Start-Sleep -Seconds 2
if($p.HasExited){throw 'SSH exited; inspect client/.tunnel-windows.log'}
Write-Output "Tunnel started, PID=$($p.Id). Run -Action health for authenticated verification."
