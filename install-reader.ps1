param([string]$Destination)
$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'skills\wechat-research-assistant\scripts\install_reader.ps1'
& $installer @PSBoundParameters
