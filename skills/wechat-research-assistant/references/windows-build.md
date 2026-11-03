# Windows 安装固定提交的 WeFlow CLI

适用于 Windows x64（Intel/AMD）。这是第三方读取器的源码构建说明；WorkBuddy 或 Codex 可执行常规检查、下载与编译，实际提权、微信账号登录和首次密钥流程按目标电脑授权执行。不要将“本人操作首次微信登录”扩大为“全部安装都必须用户手动完成”。

## 为什么不能直接下载任意安装包

本工作流参考提交：`a19a4391840e8fe5fe01eea8ad7586d9a6c1945f`。

2026-11-03 核对时，上游最新正式 Release 为 `v5.0.1`，指向提交 `6115746c5f6e5124ed7aaf4febc300d5969cead3`。它的 Windows ZIP 不等于本工作流指定的提交。不能只看显示的 `5.0.1` 版本号就判断二者相同；上游 main 分支的版本字段也可能相同。

如果继续采用本工作流的固定提交，应按下述步骤构建。其他预编译包需要另行记录来源、构建提交和适配验收，不能默认为相同版本。本说明没有声称检查过所有历史/临时构建，也没有提供网盘 EXE。

## 1. 准备构建环境

由助手先检查已有工具，已有环境不重复安装：

- Git for Windows：[官方入口](https://git-scm.com/downloads/win)。
- Rust / rustup：[官方入口](https://rust-lang.org/tools/install/)。工程自带 `rust-toolchain.toml`，该提交指定 Rust `1.99.1`；由 rustup 按工程要求提供工具链，不擅自修改仓库版本要求。
- Visual Studio 的 C++ 构建工具与 Windows SDK。安装时选择“使用 C++ 的桌面开发”所需组件；见 [Rust 官方 MSVC 前置条件](https://rust-lang.github.io/rustup/installation/windows-msvc.html)。

常规安装可由目标助手准备和发起；遇到 UAC、安装器确认、策略限制或依赖安装量较大时，明确说明具体步骤再由用户参与。不要关闭杀毒、防火墙或修改安全策略来绕过失败。安装后若新终端仍找不到 `git`、`rustup`、`cargo`，先检查本机 PATH/重开终端，不复制别人的可执行文件。

该步骤需要下载工具链及 Cargo 依赖，时间和空间取决于本机环境与网络，不保证“几秒安装”。Windows ARM64 不直接套用 x64 流程；先检查上游对目标架构的密钥读取支持。

## 2. 在独立目录构建

以下 PowerShell 代码用于**新目录**。目录已存在时会停止，助手应先检查其远端、提交与本地改动后再决定复用，不能强行覆盖。每个外部命令失败都会停止后续步骤。

```powershell
$ErrorActionPreference = 'Stop'
$expectedCommit = 'a19a4391840e8fe5fe01eea8ad7586d9a6c1945f'
$sourceDir = Join-Path $env:USERPROFILE 'WeChatResearch\tools\weflow-src-a19a439'

if (Test-Path -LiteralPath $sourceDir) {
    throw '源码目录已存在，请先核对来源和本地改动，不要覆盖。'
}
New-Item -ItemType Directory -Path (Split-Path $sourceDir -Parent) -Force | Out-Null

git clone --filter=blob:none --no-checkout https://github.com/334456777/WeFlow.git $sourceDir
if ($LASTEXITCODE -ne 0) { throw 'Git 下载失败。' }

git -C $sourceDir checkout --detach $expectedCommit
if ($LASTEXITCODE -ne 0) { throw '无法切换到指定提交。' }

$actualCommit = git -C $sourceDir rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualCommit.Trim() -ne $expectedCommit) {
    throw '源码提交不匹配，停止构建。'
}

Push-Location $sourceDir
try {
    cargo build --locked --release -p weflow-cli --target x86_64-pc-windows-msvc
    if ($LASTEXITCODE -ne 0) { throw '构建失败，请检查脱敏后的错误及缺失依赖。' }

    $readerExe = Join-Path $sourceDir 'target\x86_64-pc-windows-msvc\release\weflow.exe'
    if (-not (Test-Path -LiteralPath $readerExe)) { throw '未找到构建产物。' }
    & $readerExe --version
    if ($LASTEXITCODE -ne 0) { throw '构建产物无法运行。' }
    & $readerExe --help
    if ($LASTEXITCODE -ne 0) { throw '读取器帮助命令失败。' }
    Get-FileHash -LiteralPath $readerExe -Algorithm SHA256
} finally {
    Pop-Location
}
```

产物路径为：

```text
%USERPROFILE%\WeChatResearch\tools\weflow-src-a19a439\target\x86_64-pc-windows-msvc\release\weflow.exe
```

将实际绝对路径填入微信工作流 `.private/settings.json` 的 `reader_executable`。不要求拷贝到系统目录或全局修改 PATH。

执行 `--version`、`--help` 成功，只能记为“读取器构建/启动通过”。构建来源和本机 SHA-256 便于核对文件，不等于第三方代码已完整审计。

## 3. 接通本人的微信

之后回到 [Windows 部署说明](windows.md) 配置本机数据路径、本人账号及初次密钥。首次 `key db --save` 需要目标电脑相应权限与本人配合微信操作，不能放进每日任务；不要把密钥输出到聊天或普通文档。

微信 `4.1.11.52` 不因“4.x”这个大版本而自动视为兼容。当前参考提交的上游 [验证记录](https://github.com/334456777/WeFlow/blob/a19a4391840e8fe5fe01eea8ad7586d9a6c1945f/docs/cli-unsupported.md) 记录了 `4.1.13.65` 的密钥提取验证，其他版本需在本机单独验收。没有验证前不要自动升级/降级微信。

不要把私有目录权限失败改为无条件继续。先保留具体错误、核对本机模块与权限；满足资料保护要求后才保存真实消息。安装读取器、完成首次解密、成功读取选定会话是三个分别验收的状态。

## 交给 WorkBuddy 的继续任务

```text
请按 skills/wechat-research-assistant/references/windows-build.md 为这台电脑准备固定提交的 WeFlow CLI。自动检查 Git、Rust/rustup、C++ 构建工具和 Windows SDK；在独立目录下载并核对 a19a4391840e8fe5fe01eea8ad7586d9a6c1945f，按文档编译并验证 weflow.exe，记录路径和 SHA-256。已有目录先核对并保留改动，不覆盖账号配置。需要系统授权或本人操作微信时再指出具体步骤让我参与。构建完成后只标记“读取器已准备”，不要把它等同于微信4.1.11.52已验证兼容。不要通过放宽私有目录保护来跳过错误。
```

说明中的构建入口根据该提交的 Cargo 工程及上游发布工作流核对。本项目未在目标用户的 Windows 电脑上执行构建；实际结果以目标机器的输出与验收记录为准。
