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

M1 已由代码强制工作区真实路径、敏感文件拒绝、符号链接/Windows 目录联接逃逸检查、命令参数数组、固定工作目录、人工审批、超时、输出上限和子进程凭据净化；M2 在六个工具入口统一复用 `ExecutionPolicy`，并补充读取状态与哈希覆盖保护、排他新建、统一递归脱敏以及 Windows 进程树快速终止。M3 在请求前强制上下文预算与完整回合裁剪，审计文件写入前重新验证内部目录和目标真实路径。提示词不能替代这些边界。工作区约束与命令审批不构成操作系统沙箱，获批的 Python 或测试代码仍可能访问网络或工作区外资源。

## 终止条件

M1 已实现有效自然完成、空白最终响应、输出截断、内容过滤、模型轮次上限、工具调用上限和供应商协议/HTTP 错误的稳定状态。M3 已补充连续五次工具失败、三次相同调用及结果、上下文超限和模型请求/审批/工具中止状态；自动化测试确认 `USER_ABORTED` 后不会再次请求模型。2026-08-28 的普通 PowerShell 手工验收也确认活动子进程被清理、AgentLoop 返回 `USER_ABORTED` 且模型只请求一次。

## M1 最小安全闭环验证

M1 的配置默认值为 32,000 token 保守上下文窗口、8,000 输出上限、16 个模型轮次和 32 个工具调用；CLI 参数优先于环境变量，环境变量优先于默认值。32,000 是为 M3 请求前上下文保护预留的保守配置，不代表 `kimi-k3` 的真实上限，M1 尚未据此裁剪消息；供应商页面记录的 1M 窗口仍可由用户通过 `CODE_OPERATOR_CONTEXT_WINDOW` 显式配置。M1 每次请求固定发送 P0 已验证的 `n=1`、`max_tokens` 和 `thinking={"type":"disabled"}`。

2026-08-28 的空临时工作区真实验收使用 `kimi-k3`：模型先调用 `write_file` 创建 `hello.py`，再请求 `run_command`；程序显示规范化参数数组 `['python', 'hello.py']` 和固定工作目录，经人工仅批准本次后执行。共发生 3 轮模型请求、2 次工具调用，命令退出码 0，stdout 为 `Hello, code-operator!`，最终状态为 `COMPLETED`，模型总结与独立复跑一致。

M1 交付时只为 `read_file`、`write_file` 和 `run_command` 配置执行器；注册表虽已向模型提供六个冻结 Schema，`list_dir`、`grep` 和 `edit_file` 当时会返回稳定的 `TOOL_NOT_AVAILABLE`。该历史限制已由 M2 消除。当前上下文只做保守估算，尚未实现 M3 的完整回合裁剪。

## M2 六工具与本地安全边界验证

M2 已为六个冻结 Schema 全部配置执行器：`list_dir` 对深度和条目数设硬上限；`read_file` 分页并显式返回 `complete`；`grep` 使用区分大小写的字面量搜索和规范化相对路径 glob；`write_file` 采用排他新建，并只允许在完整读取且内容哈希未变化后覆盖；`edit_file` 只接受唯一匹配并返回受限长度的 unified diff；`run_command` 保留参数数组、固定 cwd、审批、超时与头尾截断契约。

六工具集成测试在隔离测试仓库完成 `grep -> read_file -> edit_file -> run_command pytest`，并验证工作区外路径、`.env` 与 `.code-operator` 均被拒绝。统一 `redact()` 会递归处理终端文本、工具结果、异常和 JSONL 待写对象中的当前 API Key、Authorization Bearer 值及常见凭据变量。

2026-08-28 的 Windows 手工进程树探针让父进程启动持续写输出的子进程；2 秒工具超时后总耗时 2.19 秒，父进程退出，子进程也已停止且无需额外清理。该结果只证明当前 Windows/Python 3.11 场景；跨平台复杂进程树、并发替换与路径竞态压力测试尚未验证。M2 全量离线测试共 166 项通过。

## M3 错误、上下文、终止、CLI 与极简审计验证

M3 将错误分为有限重试的传输错误、立即停止的供应商协议错误、使用原调用 ID 回灌的参数/未知工具错误，以及可供模型纠正的本地执行错误。同一 assistant turn 无论包含成功、坏参数、拒绝、未知工具或调用预算耗尽，每个合法 ID 都生成且只生成一个结果。循环在默认 16 个模型轮次、32 个工具调用、连续五次失败或连续三次相同“工具名 + 规范化参数 + 结果”时停止。

`ContextManager` 用计划锁定的 `ceil(serialized_utf8_bytes / 3)` 对 `messages` 与六工具 Schema 作确定性粗估，默认 32k 窗口并为 `max_tokens=8000` 等额预留。该启发式触发值不等同于 `kimi-k3` tokenizer，也不是所有输入的 token 安全上界；供应商仍可能拒绝被低估的请求，届时按供应商错误停止。裁剪只移除最旧的完整 assistant tool-call/result 回合，始终保留 system、当前 user 任务和最新执行链；粗估下最低集合仍超限时在请求模型前返回 `CONTEXT_LIMIT`，不使用模型摘要。供应商 usage 只有在至少一轮成功解析且每轮均提供 total tokens 时才求和；本地估算在 CLI 中明确标注为非供应商真实用量。

CLI 已提供 `--workspace`、`--ask-all`、`--auto-approve-tests`、`--max-model-rounds` 和交互输入 `/exit`。自动批准严格限定为未被工作区同名可执行文件遮蔽的裸命令 `pytest` 与 `python -m pytest`；相对/绝对运行器路径、Python 脚本、安装命令和其他模块仍要求审批。该模式仍会执行测试项目代码，不构成沙箱。可选 `/history` 未注册，避免在当前单任务 CLI 上引入未经验证的会话语义。

极简审计写入 `.code-operator/audit.jsonl`，只记录 UTC 时间、工具名、最多 500 字符的脱敏参数摘要、`ok/error_code/exit_code`、usage 可用性和停止原因；文件正文参数会替换为长度摘要，非法或非对象 JSON 只记录形态，不记录原文，也不记录完整消息、工具输出或 API Key。日志写入失败不会使 AgentLoop 崩溃，内部目录联接逃逸会被拒绝。M3 当前 211 项离线测试通过；模型请求、审批、进程创建、控制中止退出码和 AgentLoop 停止均有自动化证据。普通 PowerShell 手工 Ctrl-C 探针返回 `status=USER_ABORTED`、`model_calls=1`、`child_alive_after_return=false`；其 13.08 秒总耗时包含看到提示后按键的人工作用时间，因此不作为中断延迟数据，自动化中断测试的返回耗时为 0.65 秒。

## M4 离线集成与真实修复验收

`tests/test_integration_fake_model.py` 通过实际 `run_task`、六工具注册表、AgentLoop 和脚本化供应商响应完成完整闭环：同一轮按顺序读取源码并搜索，第一次修改后运行 pytest 得到真实非零退出码，再修改剩余根因并得到退出码 0，最后才接受模型总结。每一次发给假模型的历史都重新核对 assistant 消息只包含 P0 允许的 `role/content/tool_calls`，`finish_reason/usage/request_id/reasoning_content` 均未回放，所有工具结果按原 ID、原顺序且恰好配对一次。

同一集成文件还覆盖未知工具、坏 arguments、策略拒绝、成功 HTTP 的坏 JSON 不重试、可重试 HTTP 错误恢复、真实命令超时、真实输出截断、只裁剪最旧完整回合和空最终响应。`tests/conftest.py` 会让主 pytest 进程的任何未模拟 socket 连接直接失败；HTTP 测试只使用 `httpx.MockTransport`。Windows/Python 3.11 上源码与测试字节码编译成功，218 项离线测试连续两次分别在 6.37 秒和 6.39 秒内通过。CI 保持 Windows/Python 3.11 为阻塞平台并增加编译步骤；Ubuntu 尚未验证，不宣称跨平台通过。

2026-08-28 的 M4 真实任务在仓库外全新隔离项目上使用 `kimi-k3`。独立前置 pytest 得到 `2 failed, 1 passed`；审计记录的工具序列为 `list_dir -> read_file -> read_file -> run_command(exit 1) -> edit_file -> run_command(exit 0)`，模型没有写入或编辑测试文件。AgentLoop 返回 `COMPLETED`，共 6 轮、6 次工具调用，供应商每轮 usage 均可用并合计 10,890 tokens；随后独立复跑得到 `3 passed`。7 条审计记录中未出现当前 API Key、Authorization 或 Bearer；提交内的脱敏结构化摘要保存于 [`docs/evidence/m4-real-task.json`](docs/evidence/m4-real-task.json)，不含请求 ID、供应商响应正文、工具参数正文或输出。该验收只证明当前 Windows/Python 3.11、单个隔离任务和本次供应商响应；获批测试代码仍不受操作系统沙箱约束。提交 `1c592a3` 后续普通推送，GitHub Actions `offline-tests` 运行 `33170575939` 结论为 `success`；远端 annotated tag `v0.1.0` 解引用到该提交并注明实际验证平台，Ubuntu 仍未验证。

## E2 黄金 Eval 验证

E2 使用冻结的订单价格流水线任务：`pricing.py` 负责折扣取整，`invoice.py` 负责折后计税。评测器在每次运行前复制同一 fixture 和提示到全新临时目录，以 Git tree 建立仅用于审计的基线，独立执行初始与最终 pytest，并强制测试文件组合 SHA-256 不变。成功运行只允许变更 `pricing.py` 和 `invoice.py`，且必须有非空完整 diff。

2026-08-29 的最终正式评测固定使用 `kimi-k3`，三个新工作区全部成功，因而该固定任务三次中成功三次，达到至少 2/3 的视频候选门槛。每次初始测试退出码为 1，最终为 0；AgentLoop 均为 `COMPLETED`，三次分别使用 6、7、6 轮模型调用，均为 8 次工具调用。三次供应商 `total_tokens` 分别为 18,508、22,006 和 19,332，每轮 usage 均完整；这些数字只是本固定任务的三次样本，不代表整体成功率或性能上界。

脱敏结构化报告保存在 [`docs/evidence/e2-golden-eval.json`](docs/evidence/e2-golden-eval.json)。报告保留三个连续 index、平台与配置限制、fixture/提示哈希、初后测摘要、测试哈希、变更路径和完整 diff；不保存 API Key、Authorization、请求 ID、供应商响应正文、reasoning、完整消息历史或工具原始参数/输出。测试项目代码仍由当前用户权限执行，自动批准 pytest 不构成操作系统沙箱。

首次完整补丁审查发现 harness 的 pytest/Git 子进程未显式净化父进程环境。回归测试先复现合成 Key 可见，再复用 `sanitized_subprocess_environment()` 修复；修复前报告扫描未发现当前 API Key 或禁止字段，但不计入最终结论，原样保留于 [`docs/evidence/e2-golden-eval-pre-env-sanitization.json`](docs/evidence/e2-golden-eval-pre-env-sanitization.json)。修复后所有三次计数从零重新开始。

## E3 普通终端执行轨迹验证

E3 的终端轨迹由独立的 `TerminalTrace` 提供，和写入 `.code-operator/audit.jsonl` 的 `JsonlAudit` 分开注入；CLI 默认启用前者，库调用方可以通过 `trace` 参数选择性提供实现。`AgentLoop` 在三个事件点调用它：模型轮次（轮次、tool call 数和 usage 可用性）、工具执行（工具名、参数摘要、结果状态和错误码）以及整次运行结束（停止原因和 usage 可用性）。轨迹 sink 抛出异常时只标记输出失败并静默后续输出，不改变 AgentLoop 的控制流或结果。

终端参数摘要统一二次脱敏，并按工具类型限制内容：`read_file`、`grep`、`list_dir` 仍显示工具名、脱敏的有界参数摘要、`ok/error_code`，但不显示读取/搜索/目录结果 payload（正文、匹配项、条目）；`write_file`/`edit_file` 仅显示成功后的受限 unified diff；`run_command` 显示 `exit_code`、超时标志以及受限 stdout/stderr。所有不可信展示文本遵循“脱敏、终端安全编码、再按预算截断”的顺序：C0/C1、DEL、Unicode 控制/格式字符及行段分隔符显示为可见转义，只有 diff、stdout/stderr 和最终回答保留普通 LF，工具名、状态、错误、参数摘要和审批元数据保持单行，因此 ANSI/OSC、回车、退格或双向格式字符不能伪造顶层事件和审批标记。过长的有效 JSON 对象参数摘要、diff 和命令流保留头尾并带 `original_chars` 标记；非法或非对象参数只显示形态和长度。进入人工 ASK 的命令由交互回调显示 `ALLOW` 或 `DENY`；自动放行或策略直接拒绝的命令不经过此决策标记。已识别的当前 API Key、Bearer 值和常见凭据会脱敏，但 diff/stdout/stderr 仍需人工检查后再公开；CLI 仍打印最终模型回答。程序不主动持久化 trace，且 trace 与 JSONL audit 相互独立；终端历史、重定向或录屏仍可能保存输出。trace 不展示 reasoning、完整原始消息、request id、已识别凭据的明文或完整日志。

自动化覆盖了三事件点、按工具类型的 payload 省略、二次脱敏、长文本头尾标记、命令退出码/超时/stdout/stderr、usage 不可用、停止原因、sink 失败隔离、CLI 默认传入 trace，以及工具名、状态、参数、diff、命令流、审批信息、最终回答和错误路径中的终端控制字符回归；loop 测试还确认 trace 失败不改变成功执行。2026-08-30 人工预览使用不落盘的 UTF-8 合成文本，并按 100/40 字符宽度用 Python `textwrap` 查看：包含模型事件、长中文路径、超过 4000 字符且保留 `HEAD`/`TAIL` 与 `original_chars=10065` 的 edit diff、`run_command` 的 `exit_code=1`、stdout `3 failed, 2 passed`、中文 stderr，以及通过 `_interactive_approval` 和合成 `patch` 输入得到的真实 `[审批] ALLOW`；argv/cwd 合成 secret 未泄露，结束行保留 `usage=unavailable` 与 `stop_reason=COMPLETED`。未模拟中文双宽显示单元、真实终端编码或实际换行，仅确认关键字段在该合成预览中可按顺序复原。

该界面明确保持普通纯文本边界：无 Rich、ANSI、全屏 TUI、动画、鼠标交互、流式 tool-call 或完整日志展示。上述结果只证明当前 Windows/Python 3.11 的实现与这些自动化/人工样例，不泛化为所有终端兼容性。

## E4 有界交互会话与文件 Undo 验证

E4 在保留一次性入口的前提下新增同一进程内的多任务会话。带位置任务的 `python -m code_operator --workspace <WORKSPACE> "<TASK>"` 仍执行一次并退出：`COMPLETED`、`USER_ABORTED`、其他运行终态、参数或配置错误分别返回 0、130、1、2。省略位置任务才进入交互循环；配置和网络客户端延迟到首个普通任务创建，因此在未配置 Key 时仍可先执行 `/exit`。单次任务失败只输出该结果并返回提示符，Session 本身不因此结束。

`AgentSession` 是 E4 的资源所有者：同一实例持有一个 ModelClient、可重入 AgentLoop、六工具 ToolRegistry、WorkspacePolicy、CommandPolicy、FileTools、ChangeJournal、JsonlAudit 和可选 TerminalTrace。调用方注入的 client 不由 Session 关闭；Session 自建 client 在退出时关闭。AgentLoop 在多次 `run()` 间保存协议有效的 messages，但每次 run 都重置模型轮次、工具调用数、连续失败、重复调用和 usage 计数。`/new` 调用 loop reset，并清除完整读取哈希、ChangeJournal 和待注入本地事件；它不恢复磁盘文件、不清除既有 audit、不重新加载配置。

多 user turn 上下文仍使用原来的确定性字节估算和输出预留。消息先按 user turn 分组，每个 turn 内再按完整 assistant/tool group 分组：超预算时先移除最旧的完整历史 turn；只剩当前 turn 后，再移除其中最旧的完整 assistant/tool group，同时至少保留当前 user 与最新执行链。任何 assistant tool-call 与对应 tool result 都不会被拆开；最小集合仍超限则在请求模型前返回 `CONTEXT_LIMIT`，不生成模型摘要。

顺序工具执行新增中止补齐规则。同一 assistant turn 中，已完成调用保留真实 ToolResult；正在执行的调用返回 `USER_ABORTED`；尚未开始的调用返回 `NOT_EXECUTED_AFTER_ABORT`。三类结果严格沿用原 tool call ID 和请求顺序，各一个且仅一个，随后立即停止本次模型请求链。工具 handler 主动返回 `USER_ABORTED` 与 Python `KeyboardInterrupt` 遵循相同配对规则；已发生的文件或命令副作用不会被伪装成未执行。

`ChangeJournal` 是后进先出的内存栈，只记录成功且确实改变内容的直接 `write_file`/`edit_file`。每条记录保存规范化相对路径、来源工具、修改前 UTF-8 内容或“原文件不存在”、修改前哈希和修改后哈希；不改变模型可见 ToolResult，也不记录 `run_command` 副作用。容量常量为 `MAX_JOURNAL_ENTRIES = 32` 和 `MAX_JOURNAL_SNAPSHOT_BYTES = 8 * 1024 * 1024`；越界时从最旧记录开始淘汰。

`/undo` 在本地执行，不调用模型，也不写入 JSONL audit。恢复前重新经过工作区与敏感路径策略，要求目标身份与记录一致、仍是普通 UTF-8 文件且当前哈希等于记录的修改后哈希；外部修改、链接/目录替换、解码失败或路径拒绝都会拒绝覆盖并保留栈顶记录。原先不存在的文件在验证后删除，原有文件通过同目录临时文件和原子替换恢复；成功后输出经过脱敏、终端安全处理和长度限制的反向 diff。成功撤销会把一条有界本地 user 事件排入下一次普通任务；事件中的路径经过脱敏、终端安全处理并限制为最多 500 字符，提醒模型文件状态已经变化且编辑前必须重新读取。该事件消费一次，若请求前即因上下文超限则重新排队。

交互控制只识别整行、忽略首尾空白及大小写、且不带参数的 `/undo`、`/new`、`/exit`。未知斜杠命令和带参数形式在本地拒绝，自然语言正文里的同名片段不触发。存在撤销记录时，`/new` 与 `/exit` 默认拒绝并要求显式确认；拒绝后继续会话。确认 `/new` 或退出会丢弃撤销能力，但文件保持当前状态；EOF 会先警告再退出。

E4 的安全与输出边界没有扩张：API Key 仍只从环境变量读取；所有本地展示继续经过脱敏、终端安全编码和有界截断，最终模型回答维持既有输出约定；Undo 不新增 audit schema。没有 Agent SDK、Agent 框架或第三方 Agent 生产代码复用。明确未实现跨进程 Resume、`/history`、session/transcript/checkpoint 持久化、命令或外部副作用回滚、流式输出、Rich/全屏 TUI，也未执行新的 E4 真实 Session API 探针。第一支完整录像、Ubuntu CI 和真实窄终端中文显示宽度/编码仍未验证。

## 提示、工具描述与顺序执行

system prompt 负责跨工具、跨轮次的不变量：先检查再修改、依据真实工具结果继续、测试失败不得宣称完成、遵守审批和路径边界。每个工具的 `description` 只说明该工具的局部用途、参数和输出限制，帮助模型在当前轮选择正确函数。两者不能互相替代：把所有细节塞进 system prompt 会增加上下文并形成重复事实源，只依赖 description 又无法表达跨轮终止和诚实总结。无论哪一层都不是安全边界，路径、参数、审批和终止仍由代码验证。

同一 assistant turn 的工具调用严格按供应商给出的顺序执行并按原 ID 回传。这样，前一次读取可以授权下一次编辑，前一次修改可以决定下一次测试，审批提示和审计顺序也能稳定复现；代价是独立只读工具无法并行、延迟更高。基础版优先可解释的文件状态和确定性配对，不引入并行调度器及其写冲突、取消和部分失败语义。

M1 的三核心工具采用稳定结果契约：`read_file` 返回规范化相对路径、带行号内容、范围、总行数、截断与完整读取标志；`write_file/edit_file` 返回修改前后哈希、受限 unified diff 和截断标志；`run_command` 返回参数数组、退出码、stdout/stderr、超时和各自截断状态。错误也使用同一 `ToolResult` 外壳和原调用 ID，因此 AgentLoop 不需要从自然语言猜测结果类型。

## 明确拒绝的替代方案

- **文本 JSON 工具协议：** P0 已验证供应商原生 `tools/tool_calls`；再让模型在正文中拼 JSON 会增加解析歧义、伪造 ID 和提示注入面。
- **任意供应商字段透传：** 未经探针确认的字段可能改变下一轮语义；当前只回放 P0 白名单，把 usage、finish reason 和请求 ID 留作内部元数据。
- **未完整读取即覆盖整文件：** 会把模型的旧认知写回磁盘并覆盖用户并发修改；因此要求完整读取和内容哈希仍匹配。
- **通用 Shell：** Shell 字符串会引入转义、管道、重定向和平台差异；基础版只接受参数数组、`shell=False`、固定 cwd 和审批。
- **影子 Git：** 自动初始化、暂存或回滚会污染用户仓库并制造隐藏状态；基础版直接工作于用户指定目录，只把 Git 当普通受策略约束的命令。
- **模型自动摘要：** 摘要会额外调用模型、丢失可验证细节并产生不可确定历史；超预算时只裁剪最旧完整回合，最低集合仍超限则停止。
- **并行工具：** 并行写入、读取后编辑和审批顺序需要冲突合并语义；基础版顺序执行以保持状态可解释，接受延迟代价。
- **跨进程 Resume/会话恢复：** 持久会话需要版本化消息、工具副作用和凭据生命周期。E4 只在一个 CLI 进程中保留内存历史；进程结束后不能恢复，也没有 transcript/checkpoint 文件。

## 开源经验校准与独立实现边界

R0 的固定来源、commit、许可证、阅读文件、采用/拒绝决策和自有测试映射见 [`REFERENCES.md`](REFERENCES.md)。参考项目只帮助识别失败模式，不是运行时依赖或生产代码来源。

本项目采用的原则限于：tool call/result 必须按合法唯一 ID 一一配对；工具输出先截断且上下文裁剪不拆完整回合；协议、参数与执行错误分层；循环具有硬上限和重复检测；审批与取消由代码状态强制。具体数据结构、算法、测试数据、断言、system prompt 和生产实现全部自行编写。

第三方生产代码复用为“无”。不得复制、翻译式移植或依赖 Codex、Gemini CLI、OpenCode、Claude Code 或其他 Agent 的核心源码、SDK、CLI 和运行时。当前不创建 `THIRD_PARTY_NOTICES.md`；只有实际触发计划第 2.3 节的非核心例外复用流程后才允许创建。

## 未选择方案

不实现文本 JSON 工具协议、任意供应商字段透传、通用 Shell、工具并行、子 Agent、服务端代码或文件工具、跨进程 Resume、影子 Git 仓库和模型生成的历史摘要；不依赖 Agent SDK、Agent 框架或第三方 Agent 运行时。核心逻辑与 system prompt 均独立实现。
