# 交互模式只读本地命令（/help、/status、/config）设计规格

- 状态：项目作者已于 2026-09-02 批准范围与方案划分；`/help` 只列本地命令，明确不包含模型工具简介段
- 适用范围：`code_operator/__main__.py`、`code_operator/session.py`、`tests/test_cli.py`、`tests/test_session.py` 及 `README.md`、`README.txt`、`DESIGN.md`
- 设计提交：`docs(cli): design interactive read-only local commands`
- 目标实现提交：`feat(cli): add read-only local commands to interactive mode`

## 背景

E4 交互会话（`docs/superpowers/specs/2026-08-30-interactive-session-undo-design.md`）只提供 `/undo`、`/new`、`/exit` 三个本地命令。用户无法在不发送任务的情况下查看可用命令、会话状态或当前生效配置。本规格新增三个只读本地命令，全部在本地完成，不调用模型、不修改会话或文件状态。

## 目标与非目标

目标：

- 新增 `/help`、`/status`、`/config` 三个整行本地命令，仅交互模式可用。
- 三个命令均为只读：不调用模型、不写文件、不改会话历史、不动 Undo 栈、不扩展 JSONL audit schema。
- `/status` 的累计统计由 CLI 输入循环本地维护，不改动 `AgentLoop` 协议历史。
- 保持既有命令契约：整行、忽略大小写、不带参数；未知或带参数形式本地拒绝。
- 所有动态输出沿用既有脱敏、终端安全编码和长度截断管线。

非目标：

- 不实现 `/history`、`/compact`、`/model` 切换、`/undo N` 带参数形式或会话持久化。
- `/help` 不展示模型工具简介（项目作者 2026-09-02 明确排除）。
- 不改变 `/undo`、`/new`、`/exit` 的既有行为与确认流程。
- 不改变一次性模式的输出、退出码映射和 `run_task()` 兼容性。
- 不引入新依赖、Rich/TUI 或网络请求。

## 本地命令契约

`_local_command()` 的精确匹配集合扩展为 `/help`、`/status`、`/config`、`/undo`、`/new`、`/exit`。规则不变：去除首尾空白、忽略大小写、完整匹配；`/status foo` 等带参数形式与未知 `/xxx` 一样在本地拒绝，不发送给模型；自然语言中的同名片段不触发。

未知命令提示更新为完整列表：

```text
未知本地命令。支持：/help、/status、/config、/undo、/new、/exit。
```

一次性模式下，`/help`、`/status`、`/config` 与既有 `/undo`、`/new` 一致：打印 `<命令> 仅交互模式可用。` 并以退出码 2 结束；`/exit` 仍返回 0。

三个新命令在首个普通任务之前即可执行，因此不得要求 Session 已初始化；`/status` 在 Session 未创建时仍输出本地统计，`/config` 独立加载配置（见下）。

## `/help`

静态打印本地命令列表及一句话说明，分行为固定文本，不含任何动态内容：

```text
[帮助] 本地命令（整行输入，不带参数）：
  /help    显示本帮助
  /status  查看会话状态与用量统计
  /config  查看当前生效配置（不含 API Key）
  /undo    撤销最近一次直接文件修改
  /new     清空当前内存会话
  /exit    退出交互会话
其他输入将作为任务发送给模型。
```

## `/status`

输出字段与数据来源：

| 字段 | 来源 |
| --- | --- |
| 会话是否已初始化 | CLI 循环的 `session is None` 判断 |
| 工作区真实路径 | Session 存在时取新增的 `AgentSession.workspace` 只读属性；未初始化时显示"尚未初始化" |
| 撤销深度 | `session.undo_depth`（未初始化为 0） |
| 待注入事件数 | `session.pending_event_count`（未初始化为 0） |
| 已完成任务数 | CLI 循环本地计数（每次 `session.run()` 返回，或 Ctrl-C 合成 `USER_ABORTED` 结果后递增） |
| 上一任务状态/模型轮次/工具调用 | CLI 循环保存的最近一个 `RunResult`；无则显示"-" |
| 上一任务本地估算 token | 最近一个 `RunResult.estimated_context_tokens`，标注非供应商真实用量 |
| 累计供应商 token | 对非 `None` 的 `provider_total_tokens` 求和；任一任务为 `None` 时标注"不完整" |

CLI 循环新增本地变量：`task_count`、`last_result`、`total_provider_tokens`、`provider_tokens_incomplete`。`/new` 确认重置 Session 时，这四个变量一并归零；`/undo` 不影响统计。

`AgentSession` 新增：

```python
@property
def workspace(self) -> Path:
    return self._workspace_policy.workspace
```

这是本次唯一的 `session.py` 改动，只读、不改变任何既有行为。

## `/config`

每次执行时调用既有 `_load_config(args)` 即时读取当前环境变量与 CLI 参数，不依赖 Session，因此首个任务前即可验证配置。显示字段：

- `CODE_OPERATOR_MODEL`、`CODE_OPERATOR_BASE_URL`
- `context_window`、`max_output_tokens`、`max_model_rounds`、`max_tool_calls`（含各自生效来源后的最终值）
- 审批模式：`--ask-all`、`--auto-approve-tests` 或默认（仅 pytest 相关自动放行规则不变，此处只显示模式）

**绝不打印 API Key**，也不打印其长度或前后缀。输出前以 `Redactor([config.api_key])` 对全部字段脱敏，再经 `terminal_safe_text` 与长度截断。

`load_provider_config()` 抛出 `ConfigError`（如缺少环境变量）时，打印终端安全错误并回到提示符，不影响会话与进程退出码。

## 输出安全

所有含动态值的行复用 `_safe_single_line()`（脱敏 → `terminal_safe_text` → 截断 4000 字符、单行）。多行固定帮助文本为可信常量，直接打印。不新增 ANSI、OSC 或控制字符输出。

## TDD 实施顺序

### 1. CLI 命令红—绿

扩展 `tests/test_cli.py`，沿用现有 stdin 驱动与假模型模式，覆盖：

- 三个命令在 Session 未初始化时的输出形态（`/status` 显示未初始化、`/config` 在缺失环境变量时报错并继续、注入环境变量后显示字段且不含 Key）。
- 普通任务执行后 `/status` 显示任务计数、上一任务统计与累计 token；供应商 usage 缺失时标注"不完整"。
- `/new` 确认后统计归零。
- 带参数形式（如 `/status all`）与未知命令被拒绝并显示完整命令列表。
- 一次性模式下三个命令返回退出码 2。
- 输出中不出现 API Key；动态值经终端安全编码。

先确认预期失败（命令未识别），再实现 `_local_command()` 扩展、REPL 分支、三个打印函数与本地统计变量。

### 2. Session 属性红—绿

扩展 `tests/test_session.py`，断言 `session.workspace` 返回工作区真实路径。先失败后补属性。

## 验证与兼容门禁

实现提交前必须同时满足：

- 完整离线测试通过，无未模拟网络请求。
- 一次性 CLI 输出与退出码兼容；E2 Golden Eval runner 离线测试不变。
- 六工具 Schema、P0 回放白名单、JSONL audit schema 不变。
- `compileall`、`scripts/preflight_submission.py` 和 `git diff --check` 通过。
- 当前 API Key、Bearer、私钥头扫描无禁止提交项。
- 运行与开发依赖不增加。

本阶段只使用假模型和离线测试，不需要真实 API 探针。

## 文档与人工审核

实现稳定后同步更新 `README.md`、`README.txt`、`DESIGN.md` 的交互命令清单，并在 `REVIEW_LOG.md` 追加记录。README.txt 继续通过 1000 Unicode 字符预检。设计与实现分两个真实提交，分别形成人工审核记录；任何远端推送仍须单独授权。

## 回退规则

以下任一情况出现时，不保留半成品入口：

- 任一命令可能调用模型、修改文件或会话状态。
- 一次性 CLI 或既有三个命令行为回归。
- 输出可能泄漏 API Key 或绕过终端安全编码。
- 完整离线测试无法稳定通过。

回退时移除三个命令与 `workspace` 属性，恢复既有三命令交互模式；稳定核心不受影响。
