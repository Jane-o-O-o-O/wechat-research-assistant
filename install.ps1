$ErrorActionPreference = 'Stop'
# 仅安装随包技能；无下载、提权、注册表、密钥提取或执行策略修改。
$source = Join-Path $PSScriptRoot 'skills\wechat-research-assistant'
$parent = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.agents\skills'
$target = Join-Path $parent 'wechat-research-assistant'
if (Test-Path -LiteralPath $target) { throw '技能目录已存在，请先比较文件；未覆盖。' }
New-Item -ItemType Directory -Path $parent -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Recurse
Write-Host "已复制技能到 $target；请重新打开或刷新 Codex 的技能列表。"
