# 人工审核台账

本台账记录项目作者对提交的人工审核。记录中的“通过”表示项目作者已经检查所列范围与证据；自动测试、真实探针和签名提交分别作为补充证据。

<a id="p0-001"></a>

## P0-001：真实协议契约与可见审核机制

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(protocol): record verified model contract`
- 基线提交：`000d53c docs: complete code-operator rename`
- 审核范围：P0 的配置校验、真实协议探针、脱敏 fixture、测试、协议文档、README，以及本次新增的审核制度、台账和提交模板
- 自动测试：`python -m pytest -q`，8 项通过
- 编译检查：`python -m compileall -q code_operator scripts`，通过
- 真实探针：`kimi-k3` 三次请求均为 HTTP 200；文本请求接受 `max_tokens=8`；命名工具调用成功；原 `tool_call_id` 回传后以 `finish_reason=stop` 结束
- 安全扫描：实际 API Key、形似真实 Key、原始供应商响应 ID 和禁止提交的临时文件命中数均为 0
- 依赖边界：运行依赖仅 `httpx`，开发依赖仅 `pytest`；未引入 Agent SDK、Agent 框架或第三方 Agent 源码
- 已知限制：基础版显式关闭 thinking；thinking 模式多步工具回放尚未验证；正式 AgentLoop 尚未实现
- 审核结论：通过；确认 P0 实现、文档、测试、真实探针证据、安全扫描和已知限制可以纳入目标提交
- 审核时间：2026-08-27 21:49:44 +08:00
- 审核依据：最终暂存差异、测试与真实探针摘要、凭据扫描和设计一致性结论

本记录只覆盖上述审核范围，不扩大功能实现或远端操作权限。

<a id="r0-001"></a>

## R0-001：开源经验校准与独立实现边界

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs: record open-source reference decisions`
- 基线提交：`ad5bb78 docs(protocol): record verified model contract`
- 审核范围：`REFERENCES.md` 的四个官方来源快照、五项核心决策、十个自有失败测试场景和独立实现声明，`DESIGN.md` 的边界链接，以及按项目作者当次要求调整的 `README.md`、`REVIEWING.md` 和本台账审核措辞
- 来源固定：四个官方仓库均记录 2026-08-27 的 `HEAD` commit 和许可证；Claude Code 只作公开产品行为参考
- 研究边界：只主动打开计划第 2.3 节允许的 README/LICENSE、核心文件和直接相关测试；搜索结果被动返回的范围外摘要未继续打开、未采用、未摘录；未复用 UI、插件、MCP、多 Agent、IDE 集成和发布工程
- 依赖边界：第三方生产代码复用为“无”；未新增 Agent SDK、Agent 框架、第三方 Agent 源码或运行时依赖
- 自有证据：十个失败场景已映射到 M1-M3 的 TDD 测试名称和稳定预期，未复制来源测试数据、断言或实现
- 本地验证：来源表 4 行；核心结论 5 条，正文分别为 70/76/78/76/97 字且每条 2 个来源；失败测试场景 10 个；`README.txt` 641 字；`python -m pytest -q` 为 8 项通过
- 安全扫描：实际 API Key、形似真实 Key、原始响应 ID、禁止提交的凭据/日志/临时文件名和 Git 历史敏感模式命中数均为 0
- 已知限制：R0 只校准设计和失败场景，不证明尚未实现的 M1-M3 功能；外部仓库后续变化不影响已固定 commit 的记录；恢复任务时墙钟已越过 120 分钟停止点，已停止外部阅读并公开记录该偏差
- 审核结论：通过；确认上述来源记录、独立实现边界、测试映射、验证证据、已知限制和审核措辞可以纳入目标提交
- 审核时间：2026-08-28 00:36:28 +08:00
- 审核依据：完整暂存差异、来源/许可证核对、决策到测试映射、依赖和凭据扫描

<a id="m1-001"></a>

## M1-001：模型连通与最小安全循环

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(agent): add verified model client and minimal safe loop`
- 基线提交：`1b20a4b docs: record open-source reference decisions`
- 审核范围：配置契约、公共数据模型、非流式 ModelClient、六工具注册表与运行时参数校验、最小 system prompt、工作区/命令/凭据策略、`read_file/write_file/run_command`、最小 AgentLoop、CLI、Windows Python 3.11 离线 CI、相关测试和已验证文档
- TDD 证据：以下场景均已完成“先观察失败、再最小修复、最后转绿”的闭环：配置新增测试最初出现 13 个失败；注册表、客户端、提示词、安全策略、循环和 CLI 装配最初因模块缺失或行为缺口失败；超大文件预检查、Windows 超时快速终止、`tool_call.type` 校验、拒绝分支参数脱敏、Git 只读白名单逃逸和进程创建期中止均有独立失败记录，当前相关测试全部通过
- 离线验证：`python -m pytest -q` 共 134 项通过，网络请求数为 0；`python -m compileall -q code_operator scripts` 通过；Windows 目录联接逃逸、真实超时终止、进程创建期中止、子进程 API Key 隔离、拒绝分支参数脱敏和 Git 危险参数分类测试均执行且无跳过
- 真实闭环：`kimi-k3` 在空临时工作区用 3 轮模型请求和 2 次工具调用创建 `hello.py`；仅人工批准一次 `['python', 'hello.py']`，固定 cwd、退出码 0、stdout 为 `Hello, code-operator!`，最终状态 `COMPLETED`，独立复跑一致
- 安全边界：真实路径和链接逃逸检查、敏感文件拒绝、`shell=False`、固定 cwd、超时与终止兜底、输入/输出上限、子进程最小环境和当前 Key 精确脱敏均由代码强制；不构成操作系统沙箱
- 依赖与开源边界：运行依赖仍仅 `httpx`，开发依赖仍仅 `pytest`；未使用 Agent SDK/框架、第三方 Agent 源码或生产代码复用
- 已知限制：M1 只配置 `read_file/write_file/run_command` 执行器；`list_dir/grep/edit_file` 待 M2；完整上下文裁剪、连续失败/重复调用、全链路 Ctrl-C 和复杂跨平台进程树验证待 M3；Windows CI 只有推送后才能取得远端运行结论
- 审核结论：通过；确认 M1 实现、TDD 红—绿证据、全量测试、真实模型闭环、安全扫描、文档和已知限制可以纳入目标提交
- 审核时间：2026-08-28 01:19:59 +08:00
- 审核依据：完整暂存差异、红—绿测试记录、全量测试与编译结果、真实模型闭环、依赖/凭据/临时文件扫描和实现—文档一致性审查

<a id="m2-001"></a>

## M2-001：六工具、本地安全边界与凭据隔离

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(tools): add workspace guardrails and local tools`
- 基线提交：`d991bc1 feat(agent): add verified model client and minimal safe loop`
- 审核范围：`ExecutionPolicy.resolve_workspace_path()`、六工具 CLI 装配、目录列表、分页读取、字面量搜索、安全写入、唯一替换、命令输出契约与 Windows 进程树终止、统一递归脱敏、集成测试及已验证文档
- TDD 证据：路径、文件工具、搜索、六工具装配、命令契约、统一脱敏、六工具集成与 Windows 超时句柄场景均先观察到预期失败，再完成最小实现并转绿；最终自审又发现二进制候选未计入搜索文件上限，追加测试确认失败后修复；当前相关测试全部通过
- 离线验证：`python -m pytest -q` 共 166 项通过，网络请求数为 0；`python -m compileall -q code_operator scripts` 通过；`tests/test_policy.py` 40 项、`tests/test_filesystem_tools.py` 11 项、`tests/test_search_tool.py` 7 项、`tests/test_command_tool.py` 8 项通过
- 集成证据：隔离测试仓库完成 `grep -> read_file -> edit_file -> run_command pytest`，pytest 退出码为 0 且 1 项测试通过；工作区外路径、`.env` 和 `.code-operator` 访问均被拒绝
- Windows 手工探针：父进程启动持续输出的子进程，2 秒工具超时后总耗时 2.19 秒；返回 `COMMAND_TIMEOUT/timed_out=true`，父进程退出，子进程停止且无需额外清理
- 安全边界：六工具入口均复用真实路径策略；覆盖写入要求完整读取且哈希未变化；新建采用排他模式；命令保持 `shell=False`、固定 cwd、审批和最小子进程环境；终端、工具结果、异常与 JSONL 待写对象统一递归脱敏；这些措施不构成操作系统沙箱
- 依赖与开源边界：运行依赖仍仅 `httpx`，开发依赖仍仅 `pytest`；核心工具、安全策略和进程处理均独立实现，未使用 Agent SDK/框架、第三方 Agent 源码或生产代码复用
- 已知限制：M3 的完整上下文裁剪、连续失败/重复调用和模型请求/审批/工具全链路 Ctrl-C 尚未实现；当前进程树证据只覆盖 Windows/Python 3.11；跨平台复杂进程树、并发替换与路径竞态压力测试尚未验证
- 审核结论：通过；确认 M2 六工具实现、TDD 红—绿证据、全量测试、集成验收、Windows 进程树探针、安全扫描、文档和已知限制可以纳入目标提交
- 审核时间：2026-08-28 01:46:23 +08:00
- 审核依据：完整暂存差异、红—绿测试记录、全量测试与编译结果、六工具集成验收、Windows 进程树探针、依赖/凭据/临时文件扫描和实现—文档一致性审查

<a id="m3-001"></a>

## M3-001：错误分层、上下文保护与终止控制

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(runtime): add error layers context and termination guards`
- 基线提交：`67ab18d feat(tools): add workspace guardrails and local tools`
- 审核范围：四层错误分流、tool call/result 一一配对、模型轮次/工具调用/连续失败/重复结果终止、确定性完整回合上下文裁剪、usage 可用性、模型请求/审批/活动进程 Ctrl-C、CLI 审批选项、提示词与工具描述以及对应核心文档和测试；极简文件审计明确留给 M3-002
- TDD 证据：错误、配对、上下文、终止、CLI 和审批策略均先观察预期失败再最小实现转绿；活动进程中止测试最初在 `subprocess.communicate()` 阻塞约 10.09 秒后失败，改为可中断等待后约 0.65 秒通过；工作区同名 `pytest/python` 运行器的 3 个自动审批测试先失败后修复
- 离线验证：核心快照执行 `python -m pytest --ignore=tests/test_audit.py -q`，205 项通过；`python -m compileall -q code_operator tests` 与暂存差异格式检查通过
- 手工探针：普通 PowerShell 中活动子进程收到 Ctrl-C 后，AgentLoop 返回 `USER_ABORTED`，模型调用次数为 1，子进程返回后不存活；13.08 秒总耗时包含人工按键时间，不作为中断延迟
- 独立复核：只读审查最初提出工作区同名测试运行器遮蔽和 token 粗估表述两项 Important；前者增加代码约束与红—绿测试，后者依据 v4.5 保留 `/3` 公式并明确不是 tokenizer 安全上界；复核后无未解决 Critical/Important
- 安全扫描：暂存差异中实际 API Key 与形似真实 Key 命中数均为 0；禁止提交的凭据、审计日志、PID 和临时运行文件均未进入暂存范围
- 依赖与开源边界：运行依赖仍仅 `httpx`，开发依赖仍仅 `pytest`；AgentLoop、上下文、终止和审批策略均独立实现，未使用 Agent SDK/框架、第三方 Agent 源码或翻译式移植
- 已知限制：`ceil(serialized_utf8_bytes / 3)` 是计划锁定的粗估触发值，不是供应商 tokenizer 上界；自动测试信任模式仍信任用户当前 PATH 和测试代码；复杂跨平台进程树尚未验证；极简审计将在 M3-002 单独审核
- 审核结论：通过；确认 M3 核心实现、红—绿证据、自动与手工验证、安全扫描、独立复核、文档和已知限制可以纳入目标提交
- 审核时间：2026-08-28 18:08:25 +08:00
- 审核依据：完整暂存补丁 `M3-001-review.patch`（SHA-256 `24C5B23D445FFABCE4928D53AC54B237A33BE4F26CE0FFD80DAF3E986778B14F`）、核心与完整工作副本测试、编译/格式检查、凭据扫描、普通 PowerShell Ctrl-C 探针和独立代码审查
