# code-operator 设计说明

本文随实际实现和验证证据持续更新。未完成、未测试或仅列入计划的内容，不作为已经实现的能力声明。

## 计划修订记录

### 2026-08-27：项目完整更名

项目展示名和仓库目录统一为 `code-operator`，Python 包及 `python -m` 启动模块统一为 `code_operator`，环境变量前缀统一为 `CODE_OPERATOR_`，本地运行状态目录统一为 `.code-operator/`。已推送提交中的旧名称作为真实历史保留，不通过改写历史消除；后续实现、测试、文档和演示均只使用新标识。

### 2026-08-27：v4.5 执行勘误

本节是《code-operator 实施计划 v4.5（开源经验校准、核心闭环与交付收敛版）》的执行勘误，不另行发布 v4.6。若本节与 v4.5 的排期、阶段门槛或砍项口径冲突，以本节为准；题目 PDF 的硬要求始终具有最高优先级。

1. **关键路径顺序调整为 `M0 -> P0 -> R0 -> M1`。** P0 的真实协议证据是 M1 的硬依赖，R0 是 M1 编写核心生产代码前必须完成的离线校准。若 P0 排障连续达到 60 分钟仍未取得有效证据，先切换到 R0，并在 R0 到达 120 分钟硬上限前停止研究；之后返回 P0。不得因继续研究大型仓库而推迟 M1，也不得在完成 R0 独立实现边界记录前编写 M1 核心生产代码。

2. **M1 真实工具闭环前完成最低限度的符号链接逃逸检查。** 在任何真实模型能够执行本地工具前，先解析 workspace 的规范化真实路径；已有目标解析符号链接后检查其路径组件仍位于 workspace 内；新建目标解析最近存在的父目录后执行同样检查。包含关系使用 `Path.relative_to()` 或 `Path.is_relative_to()` 等按路径组件判断的方法，不使用字符串前缀比较。M2 只补充更完整的符号链接测试、Windows 目录联接、竞态和极端路径矩阵，不再推迟这项最低门槛。

3. **黄金 Eval 的失败调试和整组重跑累计上限为 90 分钟。** E2 整体仍受 2-3 小时预算约束。首次按冻结项目、提示、模型配置和成功判据完成三次独立运行；若少于两次成功，允许定位根因并从零重新执行完整三次，但根因调试与所有重跑合计不得超过 90 分钟。到点立即停止调参，保留最后一组完整结果并如实报告，不宣称稳定。若最后一组三次均未成功，改用事先冻结的备用演示任务；备用任务仍必须包含“读取代码 -> 修改代码 -> 运行初始失败测试 -> 验证测试通过”的真实闭环，不得退化为 Hello World。备用任务最多运行两次；若仍无成功，记为提交阻塞项并停止 Undo、额外 Eval、`/history` 等所有可选工作，不通过换断言、剪辑失败或隐瞒原黄金 Eval 结果制造成功。

4. **M3 核心完成门槛不包含文件审计。** M3 核心门槛是四层错误分流、上下文保护、tool call/result 配对、循环终止、Ctrl-C 行为和必要 CLI 能力通过测试；`.code-operator/audit.jsonl` 是核心通过后的限时增强。审计最多投入 1-1.5 小时；若两个烟雾测试不能稳定通过，删除文件审计入口，只保留脱敏的运行期内存摘要和普通终端轨迹，M3 核心状态不因此回退。

## 协议契约

2026-08-27 的 P0 真实探针已确认以下契约：

- API 根地址为 `https://api.moonshot.cn/v1`，客户端固定拼接得到 `https://api.moonshot.cn/v1/chat/completions`。
- 模型 ID 为 `kimi-k3`；[Kimi 开放平台官网](https://platform.kimi.com/)在 2026-08-27 标明其上下文窗口为 1M tokens。该数值来自供应商文档，不由探针请求反推；后续仍允许用户通过配置采用更保守的限制。
- 使用 OpenAI 兼容 Chat Completions 原生 `tools/tool_calls`，所有请求显式发送 `n=1`；输出上限字段为 `max_tokens`。短文本请求设置 `max_tokens=8` 时 HTTP 200、`completion_tokens=8`、`finish_reason=length`，证明请求上限被接受并生效。
- `kimi-k3` 默认 thinking 模式拒绝命名工具选择，返回 `400 invalid_request_error`，说明 `tool_choice 'specified' is incompatible with thinking enabled`。探针显式发送 `thinking={"type":"disabled"}` 后，命名 `tool_choice={"type":"function","function":{"name":"return_probe_token"}}` 成功返回指定原生工具调用；使用原 `tool_call_id` 回传工具结果后，模型以 `finish_reason=stop` 继续生成。
- 三次成功响应均包含 usage。响应顶层包含 `id/model/choices/usage` 等字段；HTTP 响应未提供 `x-request-id`，因此以响应体 `id` 作为内部请求证据，但不把它回放进 `messages`。

脱敏后的最小真实样本位于 `tests/fixtures/provider_tool_call.json`。探针只记录结构摘要、状态和字段名，不记录 Authorization、API Key、完整 content、reasoning 或原始响应。

## 消息回放边界

P0 的基础工具链显式关闭 thinking。该模式下工具调用 assistant 消息只包含 `role/content/tool_calls`，因此当前供应商扩展回放白名单为空；下一轮只回放这三个标准字段以及匹配原 ID 的 `role=tool` 消息。`finish_reason`、usage、响应体 `id`、模型名和未知字段仅作内部元数据，不进入下一轮 `messages`。

未关闭 thinking 的短文本响应出现了 `reasoning_content`，但本次没有验证 thinking 模式下的完整多步工具回放，因此基础版不得启用该模式或把 `reasoning_content` 宣称为已支持。若未来启用，必须重新进行独立协议探针和回放测试。

## 安全边界

M1 已由代码强制工作区真实路径、敏感文件拒绝、符号链接/Windows 目录联接逃逸检查、命令参数数组、固定工作目录、人工审批、超时、输出上限和子进程凭据净化；M2 在六个工具入口统一复用 `ExecutionPolicy`，并补充读取状态与哈希覆盖保护、排他新建、统一递归脱敏以及 Windows 进程树快速终止。提示词不能替代这些边界。工作区约束与命令审批不构成操作系统沙箱，获批的 Python 或测试代码仍可能访问网络或工作区外资源。

## 终止条件

M1 已实现有效自然完成、空白最终响应、输出截断、内容过滤、模型轮次上限、工具调用上限和供应商协议/HTTP 错误的稳定状态；工具执行期间收到中止时会终止活动子进程并停止循环。连续失败、重复调用、完整上下文裁剪和模型请求/审批/工具全链路的 Ctrl-C 行为仍属于 M3，不作为当前已完成能力。

## M1 最小安全闭环验证

M1 的配置默认值为 32,000 token 保守上下文窗口、8,000 输出上限、16 个模型轮次和 32 个工具调用；CLI 参数优先于环境变量，环境变量优先于默认值。32,000 是为 M3 请求前上下文保护预留的保守配置，不代表 `kimi-k3` 的真实上限，M1 尚未据此裁剪消息；供应商页面记录的 1M 窗口仍可由用户通过 `CODE_OPERATOR_CONTEXT_WINDOW` 显式配置。M1 每次请求固定发送 P0 已验证的 `n=1`、`max_tokens` 和 `thinking={"type":"disabled"}`。

2026-08-28 的空临时工作区真实验收使用 `kimi-k3`：模型先调用 `write_file` 创建 `hello.py`，再请求 `run_command`；程序显示规范化参数数组 `['python', 'hello.py']` 和固定工作目录，经人工仅批准本次后执行。共发生 3 轮模型请求、2 次工具调用，命令退出码 0，stdout 为 `Hello, code-operator!`，最终状态为 `COMPLETED`，模型总结与独立复跑一致。

M1 交付时只为 `read_file`、`write_file` 和 `run_command` 配置执行器；注册表虽已向模型提供六个冻结 Schema，`list_dir`、`grep` 和 `edit_file` 当时会返回稳定的 `TOOL_NOT_AVAILABLE`。该历史限制已由 M2 消除。当前上下文只做保守估算，尚未实现 M3 的完整回合裁剪。

## M2 六工具与本地安全边界验证

M2 已为六个冻结 Schema 全部配置执行器：`list_dir` 对深度和条目数设硬上限；`read_file` 分页并显式返回 `complete`；`grep` 使用区分大小写的字面量搜索和规范化相对路径 glob；`write_file` 采用排他新建，并只允许在完整读取且内容哈希未变化后覆盖；`edit_file` 只接受唯一匹配并返回受限长度的 unified diff；`run_command` 保留参数数组、固定 cwd、审批、超时与头尾截断契约。

六工具集成测试在隔离测试仓库完成 `grep -> read_file -> edit_file -> run_command pytest`，并验证工作区外路径、`.env` 与 `.code-operator` 均被拒绝。统一 `redact()` 会递归处理终端文本、工具结果、异常和 JSONL 待写对象中的当前 API Key、Authorization Bearer 值及常见凭据变量。

2026-08-28 的 Windows 手工进程树探针让父进程启动持续写输出的子进程；2 秒工具超时后总耗时 2.19 秒，父进程退出，子进程也已停止且无需额外清理。该结果只证明当前 Windows/Python 3.11 场景；跨平台复杂进程树、并发替换与路径竞态压力测试尚未验证。M2 全量离线测试共 166 项通过。

## 开源经验校准与独立实现边界

R0 的固定来源、commit、许可证、阅读文件、采用/拒绝决策和自有测试映射见 [`REFERENCES.md`](REFERENCES.md)。参考项目只帮助识别失败模式，不是运行时依赖或生产代码来源。

本项目采用的原则限于：tool call/result 必须按合法唯一 ID 一一配对；工具输出先截断且上下文裁剪不拆完整回合；协议、参数与执行错误分层；循环具有硬上限和重复检测；审批与取消由代码状态强制。具体数据结构、算法、测试数据、断言、system prompt 和生产实现全部自行编写。

第三方生产代码复用为“无”。不得复制、翻译式移植或依赖 Codex、Gemini CLI、OpenCode、Claude Code 或其他 Agent 的核心源码、SDK、CLI 和运行时。当前不创建 `THIRD_PARTY_NOTICES.md`；只有实际触发计划第 2.3 节的非核心例外复用流程后才允许创建。

## 未选择方案

不实现文本 JSON 工具协议、任意供应商字段透传、通用 Shell、工具并行、子 Agent、服务端代码或文件工具、会话 Resume、影子 Git 仓库和模型生成的历史摘要；不依赖 Agent SDK、Agent 框架或第三方 Agent 运行时。核心逻辑与 system prompt 均独立实现。
