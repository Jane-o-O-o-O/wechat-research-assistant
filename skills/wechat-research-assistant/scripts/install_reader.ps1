param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'WeChatResearch\readers\weflow-v5.0.1')
)

# Download a published CLI, verify it, and unpack. No compiler, key extraction,
# WeChat restart, administrator elevation, or persistent system-setting changes.
$ErrorActionPreference = 'Stop'
$nativeArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($nativeArch -ne 'X64') { throw '此安装入口仅适用于 Windows x64；请先核对当前架构的读取支持。' }
if (Test-Path -LiteralPath $Destination) { throw '目标目录已存在，请先检查并复用已有 CLI；未覆盖。' }

$downloadUrl = 'https://github.com/334456777/WeFlow/releases/download/v5.0.1/weflow-v5.0.1-windows-x64.zip'
$expectedSha256 = '8d88ca61c504c8d509fd7b6934e4d2261ba986f6a1e13df68c3732323e1b26fa'
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ('weflow-cli-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir | Out-Null
try {
    $zipFile = Join-Path $tempDir 'weflow.zip'
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipFile -UseBasicParsing -TimeoutSec 120
    $actualSha256 = (Get-FileHash -LiteralPath $zipFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) { throw '下载文件 SHA-256 不匹配，停止安装。' }

    $unpacked = Join-Path $tempDir 'unpacked'
    Expand-Archive -LiteralPath $zipFile -DestinationPath $unpacked
    $executables = @(Get-ChildItem -LiteralPath $unpacked -Recurse -File -Filter 'weflow.exe')
    if ($executables.Count -ne 1) { throw '压缩包中的 CLI 文件布局不符合预期。' }
    $downloadedExe = $executables[0].FullName
    $relativeExe = $downloadedExe.Substring($unpacked.Length).TrimStart([char[]]'\/')

    $versionOutput = & $downloadedExe --version
    if ($LASTEXITCODE -ne 0) { throw 'CLI 无法启动。' }
    & $downloadedExe --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CLI 帮助命令失败。' }

    New-Item -ItemType Directory -Path (Split-Path $Destination -Parent) -Force | Out-Null
    Move-Item -LiteralPath $unpacked -Destination $Destination
    $readerExe = Join-Path $Destination $relativeExe
    [pscustomobject]@{
        status = 'reader_ready'
        reader_executable = $readerExe
        version = (@($versionOutput) -join ' ')
        release = 'v5.0.1'
        archive_sha256 = $actualSha256
        wechat_connected = $false
    } | ConvertTo-Json -Compress
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
