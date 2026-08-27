# 开源经验校准与独立实现记录

本文件只记录影响 code-operator 设计与自有测试的公开参考。R0 于 2026-08-27 21:52:26 +08:00 开始，目标 90 分钟、绝对上限 120 分钟；只主动打开计划第 2.3 节限定的官方 README/LICENSE、核心文件和直接相关测试。搜索结果页曾被动返回范围外的 issue、UI、插件等摘要，但未继续打开、未采用、未摘录为来源。2026-08-28 00:19:11 +08:00 恢复任务时墙钟已越过绝对停止点（包含任务暂停等待），因此立即停止外部阅读；后续只做本地核对，不新增来源，并如实保留该时间偏差。

## 来源与决策映射

| 项目 | 仓库 URL | 访问日期 | commit | 许可证 | 阅读文件 | 观察 | 本项目决策 | 差异 | 自有测试证据 |
|---|---|---|---|---|---|---|---|---|---|
| OpenAI Codex | [openai/codex](https://github.com/openai/codex) | 2026-08-27 | [`694edc23b22b4696400dc47663ecacd437623870`](https://github.com/openai/codex/tree/694edc23b22b4696400dc47663ecacd437623870)（当日 `HEAD`） | [Apache-2.0](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/LICENSE) | [`context_manager/history.rs`](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/core/src/context_manager/history.rs)<br>[`tools/router.rs`](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/core/src/tools/router.rs)<br>[`protocol.rs`](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/protocol/src/protocol.rs) | 发送模型前补齐缺失的调用输出、移除孤立输出；工具输出在历史入口截断；路由始终携带原 `call_id`；审批是显式协议状态。 | 每个合法 ID 恰有一个结果；无合法 ID 的供应商错误立即停止；工具输出统一头尾截断；审批由 policy 返回明确结果。 | 不采用 Rust SQ/EQ、Responses API、动态工具、MCP 或 Codex 运行时；基础版使用自写 Python、Chat Completions 和顺序工具执行。 | `test_missing_tool_id_stops_without_fabricated_result`、`test_duplicate_tool_ids_stop_as_protocol_error`、`test_tool_output_keeps_head_tail_and_length`、`test_context_trim_keeps_complete_round` |
| Google Gemini CLI | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | 2026-08-27 | [`3c311beac2e78336816dd4a123db39743f9fbf85`](https://github.com/google-gemini/gemini-cli/tree/3c311beac2e78336816dd4a123db39743f9fbf85)（当日 `HEAD`） | [Apache-2.0](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/LICENSE) | [`core/client.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/client.ts)<br>[`core/client.test.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/client.test.ts)<br>[`services/loopDetectionService.test.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/services/loopDetectionService.test.ts)<br>[`core/turn.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/turn.ts)<br>[`core/turn.test.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/turn.test.ts)<br>[`tools/tools.ts`](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/tools/tools.ts) | 有会话轮次与单次递归硬界；相同和周期工具调用都有检测测试；取消信号贯穿模型与工具，AbortError 转为用户取消事件；工具先验证、审批后执行。 | 固定模型轮次和工具总数；连续三次相同工具、参数、结果即停止；Ctrl-C 终止活动工具且禁止后续模型请求；验证与执行分层。 | 不移植 TypeScript 状态机、流式事件、恢复生成或自动压缩；基础版只实现题目需要的确定性终止状态。 | `test_repeated_call_stops_after_third_equal_result`、`test_ctrl_c_kills_tool_and_never_calls_model_again`、`test_model_round_limit_is_terminal`、`test_invalid_arguments_return_matched_failure` |
| OpenCode | [anomalyco/opencode](https://github.com/anomalyco/opencode) | 2026-08-27 | [`5f5ea53afb2630227ead917f1a0ddf784c33150c`](https://github.com/anomalyco/opencode/tree/5f5ea53afb2630227ead917f1a0ddf784c33150c)（当日 `HEAD`） | [MIT](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/LICENSE) | [`session/processor.ts`](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/packages/opencode/src/session/processor.ts)<br>[`tool/registry.ts`](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/packages/opencode/src/tool/registry.ts)<br>[`packages/core/src/permission.ts`](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/packages/core/src/permission.ts)<br>[官方 README](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/README.md) | session 以调用 ID 更新 pending/running/completed/error 状态；相同工具输入三次触发 doom-loop 审批；权限将 deny、ask、allow 分开；注册与权限边界独立。 | 交叉确认 ID 配对、三次重复门槛和 deny/ask/allow 三态；基础版拒绝项直接返回稳定错误码，不让模型绕过。 | 只作交叉验证；不采用 Effect、插件工具、session 持久化、子 Agent、复杂规则合并或 OpenCode SDK。 | `test_each_multi_call_gets_one_result_even_when_denied`、`test_policy_denial_is_matched_tool_failure`、`test_repeated_call_stops_after_third_equal_result` |
| Claude Code | [anthropics/claude-code](https://github.com/anthropics/claude-code) | 2026-08-27 | [`cad6304e85e2767eac20044e752b010fff1bb4c3`](https://github.com/anthropics/claude-code/tree/cad6304e85e2767eac20044e752b010fff1bb4c3)（当日 `HEAD`） | [Anthropic 保留全部权利](https://github.com/anthropics/claude-code/blob/cad6304e85e2767eac20044e752b010fff1bb4c3/LICENSE.md) | [官方 README](https://github.com/anthropics/claude-code/blob/cad6304e85e2767eac20044e752b010fff1bb4c3/README.md)<br>[权限文档](https://code.claude.com/docs/en/permissions)<br>[交互与中止文档](https://code.claude.com/docs/en/interactive-mode) | 产品行为区分 allow/ask/deny，危险写入和命令需要审批；符号链接同时检查链接与目标；`Ctrl-C` 取消当前输入或生成。公开仓库不含可供核验的完整核心 CLI 实现。 | 只把权限分层、符号链接检查和立即中止作为产品行为对照；实现与测试全部自写。 | 不复制保留权利代码，不使用 Agent SDK，不根据不可见实现臆测内部算法，也不把 Claude Code 行为写成源码依据。 | `test_symlink_escape_is_denied_before_tool_runs`、`test_ctrl_c_kills_tool_and_never_calls_model_again`、`test_policy_denial_is_matched_tool_failure` |

commit 由 2026-08-27 对各官方仓库执行只读 `git ls-remote <repo> HEAD` 固定；表中观察只来自列出的官方文件。仓库此后发生变化时，本记录仍指向当日快照。

## 五个核心问题的限长结论

### 1. 循环继续条件

仅当响应含合法工具调用且未触发轮次、调用数、连续失败、重复调用或取消上限时继续；三次相同工具、参数和结果即终止，不实现成熟项目的恢复式续跑。来源：[Gemini client](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/client.ts)、[OpenCode processor](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/packages/opencode/src/session/processor.ts)。

### 2. tool call/result 配对

先验证本轮 ID 非空且唯一；每个合法调用无论成功、参数错误、拒绝或超限都生成一个同 ID 结果。缺失或重复 ID 属供应商协议错误，停止且不伪造结果。来源：[Codex history](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/core/src/context_manager/history.rs)、[Codex router](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/core/src/tools/router.rs)。

### 3. 四层错误边界

传输错误按状态有限重试；响应整体错误立即停止；已有合法 ID 的参数错误和本地执行错误转为匹配的失败结果。只有工具边界错误可回灌，禁止用假 ID 维持循环。来源：[Gemini turn](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/turn.ts)、[OpenCode processor](https://github.com/anomalyco/opencode/blob/5f5ea53afb2630227ead917f1a0ddf784c33150c/packages/opencode/src/session/processor.ts)。

### 4. 上下文裁剪单元

工具输出入历史前先头尾截断；超预算时从最旧完整回合裁剪，assistant 工具调用及全部对应结果不可拆。裁完仍超限则停止，不采用模型摘要掩盖信息损失。来源：[Codex history](https://github.com/openai/codex/blob/694edc23b22b4696400dc47663ecacd437623870/codex-rs/core/src/context_manager/history.rs)、[Gemini client](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/core/client.ts)。

### 5. 审批与取消

审批由 policy 产生 allow/ask/deny，提示词不能越权；拒绝仍形成匹配工具结果。Ctrl-C 取消模型和活动子进程、记录 USER_ABORTED，并在退出前保证不再请求模型。来源：[Gemini tools](https://github.com/google-gemini/gemini-cli/blob/3c311beac2e78336816dd4a123db39743f9fbf85/packages/core/src/tools/tools.ts)、[Claude Code 权限文档](https://code.claude.com/docs/en/permissions)。

## 转化为本项目失败测试的场景

以下均是 code-operator 自有测试设计，不复制参考仓库测试数据、断言或实现；M1-M3 必须按 TDD 先观察失败，再完成最小生产实现。

| 编号 | 计划测试名 | 初始输入/故障 | 必须固定的结果 | 来源启发 |
|---|---|---|---|---|
| R0-T01 | `test_missing_tool_id_stops_without_fabricated_result` | 模型返回函数名和参数但 ID 为空 | `PROVIDER_PROTOCOL_ERROR`；工具不执行；历史中没有伪造 `tool` 消息 | Codex 配对规范化 |
| R0-T02 | `test_duplicate_tool_ids_stop_as_protocol_error` | 同一 assistant turn 两个调用使用相同 ID | 整轮停止；两个工具都不执行；不尝试靠覆盖映射继续 | Codex 调用路由 |
| R0-T03 | `test_invalid_arguments_return_matched_failure` | ID 合法，`arguments` 为坏 JSON 或字段不合法 | 生成恰好一个同 ID 的 `INVALID_ARGUMENTS` 结果并允许模型纠正 | Gemini 验证/执行分层 |
| R0-T04 | `test_each_multi_call_gets_one_result_even_when_denied` | 同轮含成功、参数错误和 policy 拒绝三个调用 | 按请求顺序生成三个且仅三个对应结果；拒绝项不执行 | Codex 配对、OpenCode 权限 |
| R0-T05 | `test_tool_output_keeps_head_tail_and_length` | 工具输出超过 12,000 字符 | 历史只保留头尾、原始长度和明确省略标记；终端/审计不得冒充完整输出 | Codex 输出截断 |
| R0-T06 | `test_context_trim_keeps_complete_round` | 最旧回合包含一次 assistant 多工具调用及多个结果 | 要么整组保留，要么整组删除；系统提示和当前任务仍存在 | Codex 历史规范化 |
| R0-T07 | `test_repeated_call_stops_after_third_equal_result` | 连续三次工具名、规范化参数和结果完全一致 | 第三次后返回 `REPEATED_CALL`，不发起下一轮模型请求 | Gemini 循环检测、OpenCode doom loop |
| R0-T08 | `test_ctrl_c_kills_tool_and_never_calls_model_again` | 工具子进程运行时触发 KeyboardInterrupt | 子进程终止，结果为 `USER_ABORTED`，fake model 调用数不再增加 | Gemini/Claude Code 取消 |
| R0-T09 | `test_transport_retry_never_retries_auth_or_protocol_error` | 分别注入 429、500、401 和合法 HTTP 下的坏响应 | 仅 429/5xx 有界重试；401 和协议错误立即停止 | Gemini 显式错误事件 |
| R0-T10 | `test_truncated_or_empty_final_response_is_not_completed` | 无工具但 `finish_reason=length` 或最终文本空白 | 不返回 `COMPLETED`，分别产生稳定截断或协议终止状态 | Gemini InvalidStream 分类 |

## 独立实现声明

- 第三方生产代码复用：**无**。
- AgentLoop、ModelClient、协议解析与回放、工具注册和执行、安全策略、上下文管理、终止条件、错误处理、system prompt 及全部生产工具均由本项目独立设计和实现。
- 不复制、不翻译式移植、不改写参考仓库核心逻辑；不依赖或通过子进程封装任何 Agent SDK、Agent 框架、第三方 Agent 运行时或参考项目 CLI。
- 参考内容只进入设计原则、测试场景和答辩对比。测试名称可以描述同类风险，但测试数据、断言结构、fixture 和实现均自行编写。
- 当前没有许可证例外复用，因此不创建空的 `THIRD_PARTY_NOTICES.md`。若未来确需复用非核心通用代码，必须先暂停并执行计划第 2.3 节的来源 commit、许可证、代码旁标注和 notices 流程。

## 拒绝引入的成熟项目复杂性

- Codex：拒绝 SQ/EQ、多 Agent、MCP、动态工具、服务端协议和 Rust 运行时；基础版只保留 ID 配对、输出截断和审批分层原则。
- Gemini CLI：拒绝流式事件总线、恢复式循环、模型路由和自动摘要；只保留硬上限、重复检测、取消传播和工具验证场景。
- OpenCode：拒绝插件、Effect 运行时、session 服务和复杂权限合并；只用来交叉确认 registry/session/policy 分层。
- Claude Code：因核心实现不公开且许可证保留全部权利，只参考官方可观察行为，不作源码实现依据。

每个“采用”项已映射到上方自有测试或 `DESIGN.md` 的现有边界；其余观察均删除，不作为摘抄保留。R0 结束后关闭参考源码，M1 不再边实现边翻阅这些仓库。
