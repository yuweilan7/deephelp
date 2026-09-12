param([ValidateSet('start','stop','restart','check','health')][string]$Action='start')
& (Join-Path $PSScriptRoot '../client/tunnel.ps1') -Action $Action
