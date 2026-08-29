# E3 最小普通终端执行轨迹设计规格

- 状态：项目作者已于 2026-08-29 分节批准架构、输出契约和异常/验证设计
- 适用范围：`code_operator/trace.py`、`code_operator/loop.py`、`code_operator/__main__.py` 及对应离线测试
- 交付提交：`feat(cli): clarify terminal execution trace`

## 目标与非目标

目标是在普通终端中默认展示足以判断一次 Agent 运行过程的稳定文本轨迹：成功解析的模型轮次、工具名称、脱敏参数摘要、人工 ASK 决策、文件 diff、测试命令退出码、usage 可用性和最终停止原因。轨迹面向当前操作者，必须与持久化 JSONL 审计职责分离，且任何展示失败都不能改变 AgentLoop 的真实结果。

本阶段不引入 Rich 或其他第三方依赖，不实现全屏 TUI、颜色/ANSI 控制、动画、鼠标交互、终端宽度探测、流式 tool-call 拼接或新的历史命令；不改变工具权限、安全审批、上下文裁剪、Provider 协议、JSONL 审计结构或最终进程退出码。轨迹不是完整消息日志，不保存或展示 reasoning、原始模型消息、请求 ID、认证信息以及读取类工具返回的文件正文。

## 方案选择

采用独立 `TerminalTrace` 观察器，而不是把终端展示并入 `JsonlAudit`，也不在任务结束后回放审计文件。

- 独立观察器使终端表现、持久化审计和 AgentLoop 结果拥有各自的失败边界。
- 实时事件可以准确呈现每轮模型响应和每次工具完成，不需要读取 `.code-operator/audit.jsonl`。
- 测试和库调用方可以不传观察器，保持现有非 CLI 调用兼容。
- JSONL 审计继续独立记录原有 `tool` 与 `run` 事件，其 schema 和写入策略不因 E3 改变。

## 架构与数据流

新增 `code_operator/trace.py`，提供普通文本 `TerminalTrace`。构造时注入现有 `Redactor` 和可替换的文本输出函数；生产 CLI 使用标准输出，测试使用内存 sink。该对象只消费已经结构化的运行事件，不持有模型客户端、工具注册表或工作区写权限。

AgentLoop 增加一个可选的轻量 `TraceLike` 协议，包含三个事件：

1. `record_model_round(round_number, tool_call_count, usage_available)`：Provider 响应成功解析并计入轮次后触发；`usage_available` 仅表示该轮 `total_tokens` 是否存在。
2. `record_tool(call, result)`：工具结果已形成并将被回放给模型时触发；调用顺序与模型返回的 tool calls 一致。
3. `record_run(result)`：最终 `RunResult` 建立时触发且每次运行恰好一次。

AgentLoop 对每个追踪事件单独进行异常隔离。追踪器抛出的任何异常都被忽略，不改变消息配对、工具失败计数、循环停止条件、审计写入或返回的 `RunResult`。即使模型在首个有效响应前失败，也仍通过 `record_run` 展示真实停止原因。

`run_task()` 新增可选的 `trace` 参数，并把它传给 AgentLoop；默认值为 `None`，现有测试、Eval 和库调用不会自动产生终端输出。CLI 的 `main()` 在配置加载成功后使用同一 API Key 构造 `Redactor`，再创建 `TerminalTrace` 传入 `run_task()`，因此普通 `python -m code_operator` 执行默认开启轨迹且不增加 `--trace` 开关。

人工命令审批仍由 `_interactive_approval()` 完成。它保留现有 argv、cwd 和一次性询问，在用户输入被解释后额外输出稳定的 `[审批] ALLOW` 或 `[审批] DENY`。默认回车和无法识别的回答仍为拒绝；这项展示不扩大 ASK/AUTO/DENY 策略。

## 普通文本输出契约

稳定前缀和字段如下：

```text
[模型 1] tool_calls=2 usage=available
[工具] edit_file 参数={"new_text":"<omitted; chars=91>","old_text":"<omitted; chars=84>","path":"src/pricing.py"}
[结果] edit_file ok=true error_code=-
  --- a/src/pricing.py
  +++ b/src/pricing.py
  @@ ...
[工具] run_command 参数={"argv":["pytest","-q"],"timeout_seconds":30}
[审批] ALLOW
[结果] run_command ok=false error_code=COMMAND_FAILED
[命令] exit_code=1 timed_out=false
  stdout:
  ...
  stderr:
  ...
[结束] stop_reason=COMPLETED usage=available
```

规则如下：

- 布尔值固定使用小写 `true`/`false`，缺失的错误码、退出码等结构化字段使用 `-`，便于测试和人工检索。
- 模型事件只展示本轮编号、本轮 tool call 数量及本轮 usage 是否为 `available`/`unavailable`；不展示 content、replay fields、finish reason、request ID 或 token 数值。
- 每次工具先展示 `[工具]`，再展示 `[结果]`。工具名和参数摘要必须经过 `Redactor`。
- 参数必须解析为 JSON 对象；非法 JSON 和非对象 JSON 只显示类型/字符数占位符。`content`、`old_text`、`new_text` 的值永远替换为 `<omitted; chars=N>` 或 `<omitted>`，不得展示正文。参数摘要有固定字符上限，超限时给出包含原始字符数的明确省略标记。
- 所有工具结果都展示 `ok` 和 `error_code`。失败时不直接打印可能包含敏感数据的原始 message 或 details。
- `write_file`、`edit_file` 成功且结果包含 diff 时，展示结果中已经脱敏的 unified diff；终端层再次进行有限头尾截取，并用包含原始字符数的标记说明省略。不得把文件正文参数当作 diff 输出。
- `run_command` 额外展示 `exit_code`、`timed_out`，并分别展示已经脱敏的 stdout/stderr。每个流在终端层使用有限头尾截取；空流明确显示 `<empty>`，超限必须标注原始字符数，使测试总结和失败尾部仍可判断。
- `read_file`、`grep`、`list_dir` 只展示工具名、脱敏参数、`ok` 和错误码，不展示 details 中的 content、text、matches、entries 或其他原始搜索/目录结果。
- 未识别的未来工具使用同一最小公共格式，不猜测或直接转储其 details。
- 最终事件只展示 `stop_reason` 与整次运行的 usage 可用性。CLI 现有 final text、状态/轮次/工具汇总、供应商 token 可用性和本地估算仍保留，避免 E3 悄悄改变已有用户契约。

轨迹使用纯文本和自然换行，不调用终端宽度 API，也不自行裁掉路径字段。常见宽度与窄终端的差异只应是终端自动折行；每条事件的关键字段和值仍完整存在。

## 脱敏与有界输出

`TerminalTrace` 对参数摘要和所有将输出的文本再次调用现有 `Redactor`。API Key、Authorization Bearer 值及常见凭据变量的值不得因出现在 argv、路径、diff、stdout、stderr、异常字符串或嵌套对象中而泄露。

终端层使用一个小于或等于现有工具输出上限的固定字符预算。截断函数保留头尾，并插入形如 `... <truncated; original_chars=N> ...` 的稳定标记。实现计划必须为参数摘要、diff、stdout 和 stderr 分别锁定常量并测试边界值；不得使用无标记切片。现有工具已经截断的内容可以再次受终端预算约束，但其已有截断标记不得被改写成“完整输出”。

轨迹不落盘，不增加新的缓存、日志或临时文件。JSONL 审计继续使用自身的脱敏和参数摘要，不依赖终端是否可写。

## 错误处理

- 输出函数抛出 `OSError`、`UnicodeError` 或其他异常：该次事件对 AgentLoop 为非致命，真实运行继续；后续是否静默由 `TerminalTrace` 内部封装，但不得反复抛错影响主循环。
- tool call 参数为非法 JSON：显示稳定占位符，不回显原始字符串。
- 结果 details 字段缺失或类型错误：显示 `-` 或跳过对应块，不抛出类型异常，也不直接 `repr()` 未知对象。
- diff/stdout/stderr 为空：显示 `<empty>` 或省略无意义 diff 块，不伪造内容。
- 人工审批返回拒绝：明确显示 `DENY`，工具仍按现有策略生成真实拒绝结果。
- Ctrl-C、Provider、协议、上下文、输出截断以及循环上限：只展示 AgentLoop 已决定的真实停止原因，不把追踪异常替换为新的业务状态。

## TDD 与验证策略

实现严格采用红—绿—重构，并保留可复核的红灯证据：

1. `TerminalTrace` 单元测试先覆盖模型轮次、usage 可用/不可用、稳定字段格式和结束原因。
2. 参数测试覆盖正常对象、非法 JSON、非对象 JSON、文件正文省略、长路径、中文、嵌套凭据、API Key 和有标记的边界截断。
3. 工具结果测试覆盖 `write_file`/`edit_file` diff、超长 diff、`run_command` 成功/失败/超时、stdout/stderr 头尾及空流，并断言读取/搜索/目录 details 不出现在终端。
4. AgentLoop 集成测试使用假观察器证明事件顺序、每轮计数、各种停止路径恰好一个结束事件，以及观察器抛错不改变 RunResult 或工具循环。
5. CLI 测试证明轨迹默认启用、无需新开关，人工允许/拒绝均有明确输出，并保持原有 final text、汇总和退出码。
6. 以合成秘密执行输出安全测试，检查参数、diff、stdout、stderr 和异常路径；测试不得读取真实 API Key。
7. 定向测试通过后运行完整离线测试与 `compileall`；测试环境继续禁止未模拟网络，E3 不需要真实 Moonshot 调用。
8. 人工生成包含长路径、中文、stderr 和长 diff 的固定样例，在常见宽度和窄宽度终端查看；允许自然换行，但必须仍能判断工具、审批、退出码、测试尾部和停止原因。人工结论进入 E3 审核证据。

## 文档、审核与交付边界

实现完成后，README/DESIGN 只记录已由测试和人工样例验证的普通文本能力，不宣传流式 UI、跨终端渲染保证或完整运行日志。依赖清单不得新增 Agent SDK、Agent 框架或终端 UI 库。

E3 的代码、测试、文档和人工样例必须形成完整暂存补丁，经项目作者对 `E3-001` 人工审核通过后才能提交 `feat(cli): clarify terminal execution trace`。提交之后仍需单独展示第 9.5 节的 Git 状态、待推送提交、完整变更审查、测试证据、凭据/临时文件扫描和文档边界结论；只有取得当次明确的“允许推送”或“可以 push”后才可普通推送。
