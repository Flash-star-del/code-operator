# E4 有界交互会话与文件 Undo 设计规格

- 状态：项目作者已于 2026-08-30 分节批准范围、架构、消息生命周期、文件恢复、错误处理和验证设计
- 适用范围：`code_operator/session.py`、`code_operator/journal.py`、`code_operator/loop.py`、`code_operator/context.py`、`code_operator/tools/filesystem.py`、`code_operator/__main__.py` 及对应离线测试和文档
- 设计提交：`docs(session): design bounded interactive sessions and undo`
- 目标实现提交：`feat(session): add bounded interactive session and file undo`

## 背景与排期偏离

v4.5 把 E4 定义为可选的有限文件 Undo，并要求第一次完整录像通过后才开始。项目作者于 2026-08-30 明确决定延期录像、直接进行后续优化，随后批准把 E4 扩展为同进程交互会话与有限文件 Undo。本规格记录该真实决策，但不会把第一次完整录像标记为已完成；视频、ZIP 和最终提交门禁仍待后续执行。

当前实现是单任务 CLI：每次 `run_task()` 创建一个 `AgentLoop`，`AgentLoop.run()` 自行建立 system/user 消息并在一个任务结束后丢弃历史。该结构适合脚本、CI 和黄金 Eval，却无法让用户在同一进程中自然说“继续”“修改刚才的边界情况”，也无法让仅驻留内存的 `/undo` 在任务完成后继续可用。

## 目标与非目标

目标：

- 保持带任务参数的一次性 CLI 与现有 `run_task()` 调用兼容。
- 无任务参数启动时提供同进程持续交互会话。
- 同一会话内保留协议有效的 user/assistant/tool 历史，让后续请求理解此前工作。
- 每条用户输入独立计算模型轮次、工具调用、失败检测和 usage。
- 新增 `/undo`、`/new`、`/exit` 三个本地命令。
- 只撤销本会话中由成功 `write_file/edit_file` 直接产生的文件修改。
- 使用修改后哈希拒绝覆盖外部变化，并保持工作区、敏感路径、链接和 UTF-8 边界。
- 上下文超限时只裁剪完整用户回合或完整 assistant/tool 组，不破坏 tool call/result 配对。
- 工具执行中止后仍形成协议有效历史，允许用户留在会话中继续处理。

非目标：

- 不实现跨进程 Resume、磁盘 transcript、持久化 checkpoint 或会话选择器。
- 不实现对话分支、模型摘要、`/history`、多工作区、子 Agent 或工具并行。
- 不撤销 `run_command`、Git、依赖安装、进程、网络、工作区外或手工修改的副作用。
- 不恢复任意命令造成的文件变化，不以 Undo 替代 Git。
- 不引入 Agent SDK、第三方 Agent 运行时、Rich/TUI 或新的运行依赖。
- 不改变已由 P0 验证的 Moonshot Chat Completions、`tools/tool_calls` 和回放字段白名单。
- 不把 OpenAI、Codex 或 Claude Code 的源码、SDK 或内部协议移入本项目。

## 公开产品行为参考与独立实现边界

设计只参考公开产品行为：Claude Code 官方 CLI 文档区分交互会话、一次性 print、continue 和 resume；其 checkpoint 文档区分恢复代码、恢复对话和两者一起恢复，并明确 Bash 副作用不受恢复、checkpoint 不替代版本控制。OpenAI 官方 Responses 文档公开了 conversation 与 `previous_response_id` 的多轮状态模式。这些资料只用于确认成熟产品普遍将“会话上下文、文件恢复、一次性自动化模式”作为不同边界。

本项目仍使用 P0 已验证的 Moonshot Chat Completions，并在本地维护消息列表；不调用 OpenAI conversation/Responses，不引入 Claude Code 或 Codex SDK，不复制或翻译移植第三方会话、checkpoint、上下文或工具实现。实现完成时把上述来源和差异补入 `REFERENCES.md`。

## 方案选择

采用“可重入 `AgentLoop` + 独立 `AgentSession` + 独立 `ChangeJournal`”。

未采用的方案：

1. 每条输入重建 `AgentLoop` 并由 CLI 注入旧消息：会让 CLI 理解 tool-call 配对和裁剪内部结构，形成两套状态管理。
2. 把历史压成文本摘要后创建新任务：会丢失真实协议历史和指代语义，且引入未经要求的模型摘要。
3. 只共享 Undo 栈而不共享模型上下文：实现较小，但用户的后续自然语言无法可靠引用此前工作，不构成真正的交互会话。

## 总体架构

```text
CLI
├── 带任务参数
│   └── 临时 AgentSession -> 执行一次 -> close -> 返回现有退出码
└── 无任务参数
    └── 交互式 AgentSession
        ├── 普通输入 -> AgentLoop.run()
        ├── /undo -> ChangeJournal + FileTools 恢复
        ├── /new -> 清空会话、读取状态与 Undo 栈
        └── /exit -> close -> 退出

AgentSession
├── ModelClient（可自有或外部注入）
├── AgentLoop（可重复 run）
├── ToolRegistry / FileTools / SearchTools / CommandPolicy
├── ChangeJournal（内存、有界）
├── JsonlAudit（沿用既有工具/运行摘要）
└── TerminalTrace（沿用既有普通终端事件）
```

`AgentSession` 是资源和状态所有者，不允许 CLI 直接编辑协议消息。它提供任务执行、撤销、重置、撤销深度和关闭操作，并保证自有客户端恰好关闭一次；测试注入或外部拥有的客户端不被错误关闭。

`run_task()` 保留现有签名和一次性语义，内部使用一个临时 `AgentSession` 执行一次后关闭。现有 E2 runner、库调用、假模型集成和一次性 CLI 不需要理解会话状态。

## 消息生命周期

同一 Session 中，`AgentLoop` 的协议历史为：

```text
system
user-1
assistant tool_calls
tool results
assistant final
user-2
assistant tool_calls
tool results
assistant final
...
```

每次 `run(user_input)` 追加一条用户消息，并在同一历史上继续模型/工具循环。每次调用重新初始化：

- `model_rounds`
- `tool_call_count`
- `provider_tokens/provider_usage_complete`
- `consecutive_tool_failures`
- `previous_call_result/repeated_call_results`

会话保留消息、工具与策略对象、完整读取哈希、Journal、客户端、audit 和 trace 接线。上一条任务使用的轮次或工具数不消耗下一条任务的上限。

历史只保留可由当前协议合法回放的字段。不得把 `finish_reason`、usage、request ID、reasoning、内部停止状态或未知供应商字段加入消息。任何本地会话事件都不能伪造 assistant 或 tool 消息。

## 多用户回合上下文裁剪

`ContextManager` 从只解析一个 user 前缀升级为解析：

```text
system + [user turn 1] + [user turn 2] + ... + [current user turn]
```

一个用户回合从 user 消息开始，包含其后的零个或多个完整 assistant 组；assistant 组由一条 assistant 消息及其按 ID、按顺序匹配的所有 tool results 组成。没有工具调用的 assistant 最终回答也是一个完整组。

准备请求时按以下顺序裁剪：

1. 始终保留 system、六工具 Schema 和当前 user 输入。
2. 超限时先删除最旧的完整历史用户回合。
3. 仍超限时删除当前用户回合中最旧的完整 assistant/tool 组，但至少保留最新执行链。
4. 永远不拆分、重排或孤立 tool call/result。
5. system、当前 user 和最新链仍不能放入输入预算时，在请求模型前返回 `CONTEXT_LIMIT`。

不生成摘要，不用本地粗估冒充供应商 tokenizer。若因裁剪丢失较早语义，模型只能依据保留历史和真实工作区重新读取；文档不得宣称永久记忆。

## 失败、中止与协议有效性

Session 必须只提交完整协议单元。`CONTEXT_LIMIT` 在首个模型请求前发生时移除无法处理的本次 user 输入。供应商在返回 assistant 前失败时不新增 assistant 消息，但此前完整历史仍可继续使用。

当一个 assistant turn 含多个工具调用且执行期间发生 Ctrl-C：

- 已完成调用保留真实 `ToolResult`。
- 当前被中止调用返回匹配 ID 的 `USER_ABORTED`。
- 尚未执行调用返回匹配 ID 的 `NOT_EXECUTED_AFTER_ABORT`。
- 每个调用按原顺序恰好配对一个结果。
- 不再次请求模型，当前任务返回 `USER_ABORTED`。
- 下一条用户输入可以在协议有效的历史上继续。

实现需要让循环逐项收集结果或提供等价的部分结果边界，不能在 `execute_calls()` 抛出中止后丢失已完成结果，也不能为已经执行成功的调用伪造失败。

Provider、协议、轮次、工具预算、重复调用和连续失败等停止状态只结束当前任务；交互 Session 保留有效历史并返回提示。一次性模式仍按现有状态映射退出码。

## ChangeJournal 数据与容量

每条 `ChangeRecord` 保存：

```text
path: 相对工作区路径
before_content: str | None
before_hash: str | None
after_hash: str
source_tool: write_file | edit_file
```

`before_content=None` 表示本次成功创建新文件。Journal 只在真实写入完成后记录；`before_hash == after_hash` 的 no-op 写入不记录。每次修改单独入栈，因此对同一文件的 `A -> B -> C` 可依次恢复 `C -> B -> A`。

容量固定为最多 32 条且所有 `before_content` 的 UTF-8 字节合计最多 8 MiB。超过任一上限时淘汰最旧记录；新文件记录旧内容为零字节但计入条数。淘汰不使已成功文件写入失败。退出进程或执行 `/new` 后全部释放。

Journal 不保存模型消息、命令输出、API Key、绝对路径、工作区外内容或磁盘恢复文件。

## `/undo` 恢复算法

撤销前重新使用当前 `WorkspacePolicy` 解析相对路径，不信任记录时的路径对象。

恢复已有文件：

1. 目标必须仍存在、位于工作区内且为允许的普通 UTF-8 文件。
2. 当前内容哈希必须等于记录的 `after_hash`。
3. 使用同目录临时文件、flush/fsync 和 `os.replace` 原子恢复 `before_content`。
4. 同步 `FileTools` 完整读取哈希为 `before_hash`。
5. 生成从当前内容到旧内容的反向 unified diff。
6. 磁盘恢复和状态同步成功后才弹栈。

撤销新建文件：

1. 文件必须仍存在、仍是允许的普通文件且哈希等于 `after_hash`。
2. 删除该文件并清除完整读取哈希。
3. 生成从当前内容到空文件的反向 diff。
4. 成功后才弹栈。

文件被外部修改、删除、替换为目录、变成链接逃逸或类型变化时返回冲突，不覆盖、不视为幂等成功、不弹栈。稳定错误至少包括 `UNDO_EMPTY`、`UNDO_CONFLICT`、`PATH_DENIED`、`UNDO_READ_FAILED` 和 `UNDO_WRITE_FAILED`。

恢复操作中收到 Ctrl-C 时：原子替换前清理临时文件并保留记录；若信号恰好发生在替换后，则重新核对目标，已达到预期恢复状态时完成弹栈并明确报告成功，否则保留记录并报告状态不确定。不得只打印“中止”而隐瞒磁盘已改变。

## Undo 展示与模型同步

成功 `/undo` 输出工具来源、相对路径、剩余深度和有界反向 diff。所有不可信文本按现有顺序执行脱敏、终端安全编码和头尾截断。失败只显示稳定错误和非敏感说明。diff 仍需在公开或录制前人工检查。

`/undo` 不调用模型。成功后 Session 保存一条有界待通知事件；下一次普通用户输入发送给模型时，组合为：

```text
[本地会话事件]
用户已撤销 <path> 的最近一次直接文件修改。该文件状态可能与此前对话不同，编辑前必须重新读取。

[当前用户请求]
<用户原始输入>
```

该内容反映真实用户命令，不伪造 assistant/tool 消息；只在下一次输入中出现一次。路径继续经过脱敏和长度限制。

## 本地命令契约

只识别去除首尾空白后、忽略大小写且完整匹配的 `/undo`、`/new`、`/exit`。带额外参数或未知 `/xxx` 在本地拒绝，不发送给模型；自然语言中包含命令字符串不触发本地命令。

`/new`：

- 清空除 system 外的消息、完整读取哈希、Undo 栈和待通知事件。
- 不调用模型、不恢复文件、不删除 audit、不更换工作区或 Provider 配置。
- Undo 栈非空时先明确提示会丢失多少条撤销记录，默认拒绝。

`/exit`：

- Undo 栈为空时正常关闭。
- Undo 栈非空时提示记录将永久消失但文件保持当前状态，默认拒绝。
- EOF 无法交互确认时显示记录丢失提示后退出，不修改文件。

交互输入为空时提示后继续，不加载配置。提示处 Ctrl-C 只取消当前输入并回到提示；活动任务中 Ctrl-C 结束当前任务、清理活动子进程并回到提示。`/exit` 在首次普通任务前可直接执行，因此不得要求 API Key。

## CLI 初始化、退出码与资源

带任务参数时立即加载配置并创建临时 Session。无任务参数时先进入本地输入循环，在第一条普通任务到来时才加载配置和创建 Session。配置或工作区初始化失败无法安全继续，打印终端安全错误并以 2 退出。

一次性模式保留现有 `COMPLETED=0`、`USER_ABORTED=130`、其他任务失败为 1、参数/配置错误为 2。交互模式中单个任务失败只显示状态，不决定进程退出码；正常 `/exit` 或 EOF 为 0，Session 初始化失败为 2，未处理的 CLI 异常为 1。

`AgentSession` 提供幂等 `close()` 和上下文管理。正常退出、拒绝退出、Ctrl-C、EOF 和异常路径都必须保证自有 `ModelClient` 最多关闭一次；非自有客户端不关闭。活动命令进程沿用现有终止边界。会话和 Journal 不落盘。

`/undo` 和 `/new` 不扩展冻结的 JSONL audit schema。终端明确显示本地命令；若未来要持久化会话操作审计，需要单独设计隐私、轮转和恢复语义。

## TDD 实施顺序

### 1. ChangeJournal 红—绿

新增 `tests/test_journal.py`，覆盖已有文件恢复、新建文件删除、连续撤销、空栈、外部变化、路径/链接逃逸、no-op、容量淘汰、原子失败、读取哈希同步、diff 有界脱敏和命令副作用不记录。先确认模块或行为缺失的预期失败，再实现最小 journal 和 FileTools 接线。

### 2. 多用户上下文红—绿

扩展 `tests/test_context.py`，覆盖多用户回合解析、两级裁剪、当前输入保留、完整配对、最终回答归组、非法结构拒绝和最小集合超限。

### 3. 可重入 AgentLoop 红—绿

扩展 `tests/test_loop.py`，覆盖第二次输入看到第一次完整历史、每次计数重置、失败后继续、重置后历史为空、中止调用逐项配对和现有一次性行为兼容。

### 4. AgentSession 红—绿

新增 `tests/test_session.py`，覆盖资源所有权、连续任务、Undo 不请求模型、待通知事件一次性消费、`/new` 清理和无磁盘 session/checkpoint 文件。

### 5. CLI 红—绿

扩展 `tests/test_cli.py`，覆盖双模式、延迟配置、精确命令、确认、Ctrl-C、EOF、交互失败继续、一次性退出码和所有输出的脱敏/终端安全。

每一阶段都必须保存“先失败且原因为功能缺失，再最小实现转绿”的证据。不得先写生产代码再补测试。

## 验证与兼容门禁

实现提交前必须同时满足：

- 六工具 Schema 精确快照不变。
- P0 assistant 回放白名单和 tool ID 配对不变。
- `run_task()` 现有调用与一次性 CLI 输出/退出码兼容。
- E2 Golden Eval runner 离线测试不变。
- journal、context、loop、session、CLI 定向测试通过。
- 完整离线测试通过，无未模拟网络请求。
- `compileall`、提交预检测试和 `git diff --check` 通过。
- 当前 API Key、Bearer、私钥头、audit、缓存、PID 和临时文件扫描无禁止提交项。
- 运行与开发依赖不增加 Agent SDK、Agent 框架或终端框架。

本阶段默认只使用假模型和离线测试。新的 `kimi-k3` 连续会话探针需要重新展示隔离数据范围并取得明确数据出境授权；此前针对单次最终演示的授权不自动扩展。

## 文档与人工审核

实现稳定后同步更新 `README.md`、`README.txt`、`DESIGN.md`、`REFERENCES.md`、`REVIEW_LOG.md` 和根目录 v4.5 计划。README.txt 继续通过 1000 Unicode 字符预检；文档明确无跨进程 Resume、无命令副作用恢复、无跨平台泛化声明。

设计和实现分成两个真实提交。设计先形成 `E4-DESIGN-001` 人工审核记录；实现通过完整补丁、红—绿证据、全量测试、安全扫描和文档一致性审查后，再形成 `E4-001`。任何远端推送必须重新执行 v4.5 第 9.5 节门禁并取得当次明确授权。

## 回退规则

以下任一情况出现时，不保留半成品入口：

- 中止后无法稳定保证每个 tool call 恰好配对一个真实结果。
- 一次性 CLI、P0 协议或 Golden Eval 兼容性回归。
- Undo 可能覆盖外部变化或越过路径边界。
- `/new`、`/exit` 或异常路径可能泄漏、错误持久化会话内容或重复关闭资源。
- 完整离线测试无法稳定通过。
- 功能无法作为一个清晰实现提交解释和移除。

回退时恢复当前单任务 CLI，不注册 `/undo`、`/new`，不宣称会话能力；稳定核心和既有真实证据不受影响。
