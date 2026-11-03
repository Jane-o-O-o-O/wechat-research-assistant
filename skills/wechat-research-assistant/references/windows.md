# Windows 部署与来源（2026-11-03 核对）

本包提供分析技能、Python 同步脚本、安装脚本和提示词；不包含微信数据库、聊天记录、模型密钥或第三方 EXE/DLL。默认下载上游已编译的 CLI，不要求 Git、Rust、Visual Studio 或 Windows SDK。三系统单元检查已经运行；真实 Windows 微信读取仍需在本人电脑验收。

## 读取器选择

- 当前用户的 `pandorafuture/wx-cli` 面向 Apple Silicon macOS，不能当 Windows 读取器：[原项目](https://github.com/pandorafuture/wx-cli)。
- 老 `sjzar/chatlog` 原仓库已移除代码，不能沿用旧“一键下载”教程：[作者说明](https://github.com/sjzar/chatlog)。
- 默认采用独立维护者 `334456777/WeFlow` 发布的 [v5.0.1 CLI](https://github.com/334456777/WeFlow/releases/tag/v5.0.1)，构建源码提交为 `6115746c5f6e5124ed7aaf4febc300d5969cead3`。它**不是微信官方组件**，也不是旧 Electron 桌面安装包。
- 本工作流兼容发布版原始行格式及后续 `a19a4391840e8fe5fe01eea8ad7586d9a6c1945f` 的结构化行格式。旧说明中强制构建 a19 提交的要求已取消；普通使用者优先使用现成 CLI。
- 上游的 [验证范围](https://github.com/334456777/WeFlow/blob/v5.0.1/docs/cli-unsupported.md) 仅记录少量 Windows x64、微信 4.x 账号验证，密钥流程验证过 4.1.13.65。微信 4.1.11.52 等其他版本仍需本人电脑验收，不自动升级或降级微信。

## 下载、解压即可准备 CLI

先检查本机是否已有可用 `weflow.exe`；若有，核对版本、来源及 JSON 格式后复用。没有时，WorkBuddy/Codex 可以在仓库目录执行：

```powershell
./install-reader.ps1
```

脚本用于 Windows x64，下载约 11.4 MB 的 [weflow-v5.0.1-windows-x64.zip](https://github.com/334456777/WeFlow/releases/download/v5.0.1/weflow-v5.0.1-windows-x64.zip)，核对固定 SHA-256 后解压，并检查 `--version` / `--help`。不安装编译器，不自动提权，不获取微信密钥。已有目标目录不覆盖。

默认程序在 `%LOCALAPPDATA%\WeChatResearch\readers\weflow-v5.0.1` 下，准确 EXE 路径由脚本返回。ZIP 的预期 SHA-256 为：

```text
8d88ca61c504c8d509fd7b6934e4d2261ba986f6a1e13df68c3732323e1b26fa
```

也可以从同一上游页面手动下载、核对哈希并解压。来源与哈希只能核对文件完整性，不等于第三方全部代码已经审计。网络或本机策略阻止下载时，报告具体阻塞，不关闭安全软件或改用不明来源。

**普通查询直接调用 CLI：**

```powershell
& '<weflow.exe绝对路径>' --json chat messages '<已授权联系人或群ID>' --limit 50
```

实际执行时捕获 stdout/stderr，完整正文只写入受保护的 `.private`，不要直接把私聊或密钥打印到共享终端。需要可靠分页、归档、去重与提交进度时使用本包 `workflow.py`；它调用的仍是同一个 CLI，不启动额外服务。

源码构建仅供维护者复现历史开发版本，见 [可选源码构建](windows-build.md)，不作为默认安装步骤。

## 首次配置

1. 使用已有的 WorkBuddy 或 Codex。直接查询 CLI 无需 Python；本包归档助手与免 Key 搜索使用 Python 3.10+ 标准库。不需要为本包另买一个 API，模型用量仍按当前服务计费。
2. Codex 可运行 `install.ps1` 安装 Skill；它仅复制到 `.agents/skills`，不负责安装读取器。WorkBuddy 使用其自定义技能导入入口，或先直接读取 SKILL.md 执行工作流；不要把 Codex 技能目录当作 WorkBuddy 必需路径。下载 CLI 使用的是另一个脚本 `install-reader.ps1`。
3. 在选定的独立资料目录启动技能。检查读取器，但不要把 `config list`、数据库密钥或完整聊天直接打印到终端。`config get wxid` 足够核对所选账号。
4. 微信本机必须已有聊天。首次解密由账号本人在自己电脑上配置；参考版本 `weflow key db --save` 会用进程 hook，通常需管理员终端，并要求本人退出/重新打开微信、点击进入。**这是首次人工步骤，不是无感读取**。`--save` 避免把密钥打印出来。不要将本步骤放进每日任务，不能替用户关闭杀毒、防火墙或修改系统安全策略。未接受/未完成时标记读取阻塞。
5. 图片密钥是另一项可选配置；先做文本链路，不因缺图片密钥阻塞文字分析。WeFlow 配置及缓存也含明文/密钥，应与 `.private` 同样限制访问；不要移到云盘或同步给朋友。

## 本包命令

以下 `<...>` 必须在目标电脑替换；实际 Python 命令可能是 `py -3`。

```powershell
py -3 <技能目录>/scripts/workflow.py init --workspace <资料目录>
```

编辑 `<资料目录>/.private/settings.json`：填 `reader_executable` 的绝对 EXE 路径、`self_wxid`（裸账号 ID）、初次起始时间和明确选择的 `conversations`。`slug` 使用简短英文字母/数字/下划线/连字符；`id` 是稳定账号/群 ID；`name` 是便于阅读的名称；`goal` 说明交往/业务目标。一个来源可关联多个项目。不要放密钥。

```json
{
  "reader_executable": "C:\\Tools\\weflow\\weflow.exe",
  "self_wxid": "YOUR_OWN_WXID",
  "initial_since": "2026-11-01T00:00:00+08:00",
  "page_size": 200,
  "max_pages": 1000,
  "conversations": [
    {"slug": "selected-person", "id": "CONFIRMED_CONTACT_ID", "name": "选定联系人", "goal": "梳理需求和跟进事项"}
  ]
}
```

```powershell
py -3 <技能目录>/scripts/workflow.py doctor --workspace <资料目录>
py -3 <技能目录>/scripts/workflow.py sync --workspace <资料目录>
# Codex 读取 review.jsonl，完成分析与工作资料后，再执行：
py -3 <技能目录>/scripts/workflow.py commit --workspace <资料目录> --source selected-person --batch <批次ID> --report 分析批次/selected-person/<批次ID>.md
```

`sync` 只捕获，不调用模型；`commit` 只核查批次、报告存在与非空及其证据标记，不能判断报告是否分析正确。待处理批次在 `.private/pending`，未提交先处理旧批次，再同步新消息。按来源失败隔离，抓取中断可重试；锁冲突停止。异常退出遗留锁时先确认无工作进程，再删除 `.private/workflow.lock`，不自动抢锁。

Windows 使用当前用户及 SYSTEM 的专用 DACL 保护新建 `.private`，Unix 使用目录 700/文件 600。不要通过共享压缩包复制运行目录。管理员仍可能读取本机文件；这不是磁盘加密。

## 自动运行

在目标机手动验收一次后，用 `scheduled-prompt.md` 创建**本地**每日任务；默认建议北京时间 22:00，可由用户调整。定时任务要串行运行并覆盖未提交批次。电脑必须开机，桌面应用运行，微信数据可读；关机时不能声称会继续处理。不要把任务配置成没有本机文件访问的纯网页任务。该环境无法创建桌面任务时只保存可复制提示词，并明确尚未启用。

官方说明：[Codex 技能](https://learn.chatgpt.com/docs/build-skills)、[Codex 本地定时任务](https://learn.chatgpt.com/docs/automations?surface=app)、[WorkBuddy 技能](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)、[WorkBuddy 定时任务](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Automation-Guide)。不同客户端版本的技能安装路径可能不同，先核对当前官方文档；也可直接读 SKILL.md 使用本包，不依赖全局安装。

## 适配契约与已知限制

脚本使用 `weflow --json config get wxid` 和 `weflow --json chat messages <id> --limit N --since <秒>`；后续页用 `--after <opaque_cursor>`。返回 `success/data`，data 是由新到旧排列的“最接近该时间/游标的新消息”。v5.0.1 原始行使用 `local_id`、`server_id`、`message_content`、`sender_username`、`db_name` 等字段；后续版本使用 `localId`、`serverId`、`content`、`sender`、`shard` 等字段。本包统一为同一处理格式，原始分页仍按原样归档；不能识别的结构失败，不静默丢弃。

每轮必须拉到空页；取每页第一条为下一页起点，检查游标不循环。逐条检查字段，Python 保留 64 位 ID 精度；server ID 缺失时用 shard/local ID/时间的组合去重。来源数据结构变动时失败，不静默吞消息。WeFlow 未报告 skipped，无法证明底层解析零遗漏；默认只保证读取器当前返回且本机已有的内容。补迁旧历史、换账号/库或读取器升级需要重新审核范围并按需回填，不能只靠旧游标保证历史完整。
