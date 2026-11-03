# Windows 部署与来源（2026-11-03 核对）

本包提供分析技能、可读的 Python 同步脚本、安装脚本和提示词；不包含微信数据库、聊天记录、模型密钥或第三方 EXE/DLL。当前在 Mac 上完成模拟验证，尚未在 Windows 微信实机运行。

## 读取器选择

- 当前用户的 `pandorafuture/wx-cli` 面向 Apple Silicon macOS，不能当 Windows 读取器：[原项目](https://github.com/pandorafuture/wx-cli)。
- 老 `sjzar/chatlog` 原仓库已移除代码，不能沿用旧“一键下载”教程：[作者说明](https://github.com/sjzar/chatlog)。
- 候选为独立维护者 `334456777/WeFlow` 的 Rust CLI 分支，**不是微信官方组件**。参考提交 `a19a4391840e8fe5fe01eea8ad7586d9a6c1945f`，不要将它与同名旧 Electron 安装包混同：[源码](https://github.com/334456777/WeFlow/tree/a19a4391840e8fe5fe01eea8ad7586d9a6c1945f)、[CLI 文档](https://github.com/334456777/WeFlow/blob/a19a4391840e8fe5fe01eea8ad7586d9a6c1945f/docs/native-cli.md)、[验证范围](https://github.com/334456777/WeFlow/blob/a19a4391840e8fe5fe01eea8ad7586d9a6c1945f/docs/cli-unsupported.md)。作者记录 Windows x64、微信 4.x 的少量真实账号验证；数据库密钥流程验证过 4.1.13.65。不能推广为所有 Windows/微信版本都可用。

目标机先核对 Windows 架构、微信版本、源码提交及构建来源。不要自动安装网盘或群文件中的“破解版”。本包没有审计第三方所有代码，不能保证其绝无后门。可审查固定源码后按上游构建说明生成程序；构建命令以该版本 Cargo/Makefile 为准，不凭空添加 DLL 下载地址。记录第三方 EXE/DLL 的 SHA-256；仅有哈希并不证明安全，须与可信来源/所构建版本对应。

## 首次配置

1. 安装并登录 Windows 版 Codex/ChatGPT 桌面开发环境；准备 Python 3.10+。不需要为本包另买一个 API。模型用量仍按用户当前服务计费，不能承诺免费。
2. 解压本包；在 PowerShell 运行 `./install.ps1`。它只复制技能到当前 Windows 用户 `~/.agents/skills/wechat-research-assistant`，已有目录会停止；不下载软件、不更改执行策略、不提取密钥。若策略禁止脚本，直接让 Codex 读取本包 SKILL.md 或手动复制同一文件夹，不绕过系统策略。
3. 在 Codex 选定的独立资料目录启动技能。让 Codex 检查读取器，但不要把 `config list`、数据库密钥或完整聊天直接打印到终端。`config get wxid` 足够核对所选账号。
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

官方说明：[技能](https://learn.chatgpt.com/docs/build-skills)、[本地定时任务](https://learn.chatgpt.com/docs/automations?surface=app)、[Windows 环境](https://learn.chatgpt.com/docs/windows/windows-sandbox)。不同客户端版本的技能安装路径可能不同，先核对当前官方文档；也可直接读 SKILL.md 使用本包，不依赖全局安装。

## 适配契约与已知限制

脚本使用 `weflow --json config get wxid` 和 `weflow --json chat messages <id> --limit N --since <秒>`；后续页用 `--after <opaque_cursor>`。按参考源码，返回 `success/data`，data 是由新到旧排列的“最接近该时间/游标的新消息”，每条有 `_cursor`、`serverId`、`createTime`、`sender`、`isSend`、`content`、`shard`、`localId`。直接从原始行归档，不读取其他会话正文。

每轮必须拉到空页；取每页第一条为下一页起点，检查游标不循环。逐条检查字段，Python 保留 64 位 ID 精度；server ID 缺失时用 shard/local ID/时间的组合去重。来源数据结构变动时失败，不静默吞消息。WeFlow 未报告 skipped，无法证明底层解析零遗漏；默认只保证读取器当前返回且本机已有的内容。补迁旧历史、换账号/库或读取器升级需要重新审核范围并按需回填，不能只靠旧游标保证历史完整。
