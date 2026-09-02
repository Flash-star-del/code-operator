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

<a id="m3-002"></a>

## M3-002：脱敏执行摘要审计

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(audit): add redacted execution summaries`
- 基线提交：`25ca7dd feat(runtime): add error layers context and termination guards`
- 审核范围：默认 `.code-operator/audit.jsonl` 接线、工具与运行停止摘要、参数上限与递归脱敏、文件正文省略、非法/非对象参数形态摘要、写入失败降级、内部目录联接逃逸防护、对应文档和 6 项审计测试
- TDD 证据：初始审计测试先因模块或行为缺失失败后最小实现转绿；提交前自审发现短文件正文会原样进入参数摘要，新增测试确认泄漏后改为长度摘要；随后新增非法/非对象 JSON 原文测试确认失败，再改为只记录形态，两条追加红—绿闭环均有失败输出
- 离线验证：`python -m pytest tests/test_audit.py -q` 为 6 项通过；`python -m pytest -q` 为 211 项通过；`python -m compileall -q code_operator tests` 与暂存差异格式检查通过；`README.txt` 为 715 字符
- 审计边界：只记录 UTC 时间、工具名、最多 500 字符的脱敏参数摘要、`ok/error_code/exit_code`、usage 是否可用和停止原因；不记录完整消息、文件正文、stdout/stderr、原始非法参数或 API Key
- 可靠性与路径：审计写入失败仅设置内部失败标志，不使 AgentLoop 崩溃；写入前验证 `.code-operator` 真实父目录和目标仍在工作区内，目录联接逃逸测试通过
- 独立复核：只读审查完整覆盖审计实现与测试，未提出审计相关 Critical/Important；核心审查发现的两项 Important 已在 M3-001 处理并复核解除
- 安全扫描：暂存差异中实际 API Key 与形似真实 Key 命中数均为 0；审计日志、PID 和临时探针未进入仓库暂存范围
- 依赖与开源边界：未增加依赖；审计协议、脱敏摘要和 AgentLoop 接线均独立实现，未使用 Agent SDK/框架或第三方 Agent 源码
- 已知限制：路径验证与最终打开之间仍有理论竞态；审计是尽力写入而非强一致持久化；日志没有轮转机制，基础版只通过字段和单条摘要上限控制内容
- 审核结论：通过；确认极简审计实现、红—绿证据、全量验证、隐私边界、安全扫描、文档和已知限制可以纳入目标提交
- 审核时间：2026-08-28 18:11:23 +08:00
- 审核依据：完整暂存补丁 `M3-002-review.patch`（SHA-256 `323DD9384BC9BD65952A5D3489D7097C51760536C757DB597D3A75C8F64C139D`）、专项/全量测试、编译/格式检查、凭据扫描和独立代码审查

<a id="m4-001"></a>

## M4-001：离线集成套件与真实任务证据

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test: add offline integration suite and real task evidence`
- 基线提交：`a2a359c feat(audit): add redacted execution summaries`
- 审核范围：脚本化假模型完整修复链、逐轮 assistant 回放白名单和 tool ID 配对、坏 JSON/重试/拒绝/超时/截断/裁剪/空响应集成场景、离线 socket 护栏、Windows CI 编译步骤、`kimi-k3` 隔离真实修复的脱敏结构化证据，以及 README、DESIGN 和执行计划中的已验证声明与限制
- 测试证据：`tests/test_integration_fake_model.py` 7 项通过；`python -m compileall -q code_operator tests` 通过；Windows/Python 3.11 全量 218 项离线测试连续两次为 6.37 秒和 6.39 秒通过，审核前最终复核为 `218 passed in 6.55s`
- 集成闭环：实际 `run_task` 与六工具按顺序完成读取、搜索、第一次修改、pytest 非零、第二次修改、pytest 为 0 和最终总结；每轮出站历史只含 P0 允许字段，所有工具结果按原 ID、原顺序且恰好配对一次
- 真实验收：全新仓库外 buggy Python 项目使用 `kimi-k3`；独立初始测试为 `2 failed, 1 passed`，Agent 只修改生产文件，最终独立复跑为 `3 passed`；状态 `COMPLETED`，6 轮、6 次工具调用、供应商 usage 合计 10,890 tokens
- 脱敏证据：`docs/evidence/m4-real-task.json` 保存平台、文件最终哈希、前后测试摘要、工具序列、轮次、停止原因、usage 及来源；不保存请求 ID、供应商响应正文、原始参数正文或工具输出
- 独立复核：规格复核发现真实 usage 未随证据保存后已补齐结构化摘要并复核关闭；代码质量复核提出 MockTransport 客户端生命周期和 README 审批例外两项 Minor，修正后再次复核，无未解决 Critical/Important/Minor
- 安全扫描：实际 API Key 在当前树、暂存差异和 Git 历史中的命中数均为 0；暂存形似真实 Key、凭据、审计日志、缓存、PID 和临时文件名命中数均为 0；历史唯一形似 Key 命中已确认是 `tests/test_client.py` 的合成断言值
- 依赖与开源边界：未修改生产代码或增加依赖；运行依赖仍仅 `httpx`，开发依赖仍仅 `pytest`；未使用 Agent SDK/框架、第三方 Agent 源码或翻译式移植
- 已知限制：远端 Windows CI 尚待推送后验证；Ubuntu 未验证；测试网络护栏覆盖 TCP `connect/connect_ex` 而非所有 UDP 路径；获批测试代码仍不构成操作系统沙箱；`v0.1.0` 标签必须等待推送和当前平台 CI 成功
- 审核结论：通过；确认 M4 本地集成测试、真实任务证据、脱敏边界、文档声明和已知限制可以纳入目标提交；本结论不授权推送或打标签
- 审核时间：2026-08-28 19:30:34 +08:00
- 审核依据：完整暂存补丁 `M4-001-review.patch`（SHA-256 `029E337F0B5A6E0AEB9143611AB48B6C0393566CF91DB2DE6782E569B4A1C777`）、全量离线测试与编译结果、真实任务独立前后测试、脱敏审计和结构化证据、凭据扫描以及规格/代码质量两阶段复核

<a id="e1-001"></a>

## E1-001：设计答辩材料与提交物预检

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs: add design notes and submission preflight`
- 基线提交：`1c592a3 test: add offline integration suite and real task evidence`
- 审核范围：`DESIGN.md` 的提示分工、顺序执行、工具契约和拒绝方案，`DEFENSE.md` 的架构陈述与 14 个追问，`REFERENCES.md` 的当前测试映射，README 状态，M4 远端 CI 证据同步，以及提交 ZIP 预检脚本和测试
- TDD 证据：预检模块最初缺失导致测试收集失败；200 MB 恰好允许/超过 1 字节拒绝先暴露错误边界后修复；独立复核提出的超大 README 预解压、NaN 时长、损坏 Deflate、损坏 LZMA 和截断成员均先观察预期失败，再增加最小异常归一化并转绿
- 本地验证：`tests/test_submission_preflight.py` 共 22 项通过；`python -m pytest -q` 共 240 项通过；`python -m compileall -q code_operator scripts tests`、暂存差异格式检查和 M4 证据 JSON 解析通过；`README.txt` 为 820 个 Unicode 字符
- 预检契约：要求显式本人姓名，ZIP 严格命名为 `<姓名>.zip`，根层恰有 UTF-8 `README.txt` 和一个小写 `.mp4`，拒绝目录、隐藏、重复和加密成员；README 不超过 1000 个 Unicode 字符，视频不超过 200 MiB；有 ffprobe 时检查不超过 120 秒，否则输出人工确认警告
- 文档与开源边界：四个固定来源的 URL、40 位 commit、许可证和当前自有测试证据已复核；第三方生产代码复用仍为“无”，未创建 `THIRD_PARTY_NOTICES.md`，未新增依赖
- 独立复核：规格复核确认 E1 文档与题目边界一致；代码质量复核发现并推动关闭 ZIP 损坏成员和 README 解压上限问题，最终无未解决 Critical/Important/Minor
- 安全扫描：当前 API Key 在完整暂存补丁中的精确命中数为 0，形似凭据命中数为 0；禁止提交的凭据、审计日志、缓存、PID 和临时文件名命中数为 0
- 已知限制：当前环境没有 ffprobe，最终视频必须人工或在具备 ffprobe 的环境确认时长；最终 MP4 和姓名 ZIP 尚未生成；Ubuntu 未验证；E2/E3 尚未执行；10-15 分钟模拟答辩由项目作者于 2026-08-29 明确延期至最终交付
- 审核结论：通过；确认上述文档、预检实现、TDD 证据、验证结果、开源边界和已知限制可以纳入目标提交；本结论不授权远端推送
- 审核时间：2026-08-29 00:48:32 +08:00
- 审核依据：完整暂存补丁 `E1-001-review.patch`（SHA-256 `5D92B6FBDC5E65478859E2093F9BE63BD1A0617016FB5EBFC8534EBE0677A717`）、全量测试与编译结果、错误提交物真实非零 JSON 探针、README 字符计数、凭据/临时文件扫描和两阶段独立复核

<a id="e2-design-001"></a>

## E2-DESIGN-001：可复现黄金 Eval 设计规格

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(eval): design reproducible golden coding task`
- 基线提交：`d0a9afd docs: add design notes and submission preflight`
- 审核范围：`docs/superpowers/specs/2026-08-29-golden-eval-design.md` 的目标、冻结订单价格流水线、运行器数据流、成功判据、报告隐私、错误处理、测试策略和交付边界
- 核心决策：选择跨 `pricing.py` 与 `invoice.py` 的整数金额数据流缺陷；测试文件哈希不变是强制成功条件；正式入口固定三个全新临时工作区，至少 2/3 成功才成为视频候选；失败运行必须保留
- 安全与证据边界：真实 Eval 只复用现有 `run_task()`，测试自动审批仅限冻结 pytest；报告不得保存 API Key、请求 ID、reasoning、响应正文或工具原始内容；临时 Git 仅用于 fixture 基线 diff，不扩展 Agent 核心能力
- 文档自检：共 112 行；占位符、TODO 和未决项命中数为 0；目标、组件、数据流、错误处理、成功判据和测试范围之间未发现矛盾
- 依赖与开源边界：设计不新增依赖，不使用 Agent SDK/框架或第三方 Agent 源码，不改变既有第三方生产代码复用为“无”的结论
- 已知限制：本提交只冻结设计，不实现黄金项目、运行器或真实三次结果；E3、最终录像、姓名 ZIP 和延期模拟答辩仍在后续阶段
- 审核结论：通过；确认书面设计可以纳入本地提交并用于生成实施计划；本结论不授权实现提交或远端推送
- 审核时间：2026-08-29 01:17:20 +08:00
- 审核依据：完整暂存补丁 `E2-DESIGN-001-review.patch`（SHA-256 `2F2974B5A66F0D81B3A067CB3112AEE5DD2E42AC6EBAEC7509A50414C6A07745`）和项目作者对三段设计及最终书面规格的逐次批准

<a id="e2-001"></a>

## E2-001：可复现黄金编码任务与真实三次 Eval

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test(eval): add reproducible golden coding task`
- 基线提交：`3cbb898 docs(eval): design reproducible golden coding task`
- 审核范围：冻结订单价格流水线 fixture 与提示、三次评测 runner、临时 Git tree 基线、初后测与测试哈希门禁、Windows Job Object 进程树终止、子进程凭据环境净化、排他原子报告、真实 AgentLoop 假模型集成、CLI、README/DESIGN、实施计划及修复前后两组脱敏证据
- TDD 证据：模块缺失、非法税率、后代进程残留、运行缓存污染哈希、报告半文件、测试目录删除、异常正文泄露、Git 暂存区绕过、非 `RunResult` 返回及子进程可见合成 API Key 均先观察预期失败，再完成最小修复并转绿
- 离线验证：`tests/test_golden_eval.py` 29 项通过；`python -m pytest -q` 最终 269 项通过；`python -m compileall -q code_operator evals tests` 和暂存差异格式检查通过；脚本化假模型通过真实 `run_task()` 完成 4 轮、5 次工具调用的离线修复闭环
- 正式结果：环境净化修复后使用 `kimi-k3` 从零重跑三个全新工作区，该固定任务 3/3 成功；初测退出码均为 1，后测均为 0，状态均为 `COMPLETED`，轮次为 6/7/6，工具调用均为 8，tokens 为 18,508/22,006/19,332，usage 均完整
- 成功判据：三次测试 SHA-256 前后均一致，变更路径仅为 `invoice.py` 和 `pricing.py`，完整 diff 均非空且准确修复折扣四舍五入与折后计税两个根因；三个 index 连续，未删除或重排样本
- 审查修正：完整暂存补丁首轮审查发现 harness 的 pytest/Git 子进程继承 Provider 环境；追加红—绿测试后复用 `sanitized_subprocess_environment()` 修复。修复前 3/3 报告扫描无实际凭据泄露，但不计入最终结论，已原样保留；最终完整暂存差异复审无未解决 Critical/Important/阻塞性 Minor
- 安全扫描：当前 API Key 在工作树、Git 历史、完整审核补丁和两份报告中的精确命中数均为 0；暂存范围无私钥、Bearer、`.env`、audit、缓存、PID 或临时运行文件
- 依赖与开源边界：未修改 `code_operator/` 核心或依赖文件；runner 只复用已有 `run_task()` 和脱敏原语；未引入 Agent SDK/框架、第三方 Agent 源码或翻译式移植
- 已知限制：结果只覆盖 Windows/Python 3.11 与一个冻结任务的三次样本，不代表整体成功率；测试代码仍以当前用户权限执行，不构成 OS 沙箱；Ubuntu 和远程 CI 尚待推送后验证
- 审核结论：通过；确认 E2 fixture、runner、严格判据、TDD 证据、修复后真实三次结果、脱敏边界、文档和已知限制可以纳入目标提交；本结论不授权远程推送
- 审核时间：2026-08-29 07:34:13 +08:00
- 审核依据：完整暂存补丁 `E2-001-review.patch`（SHA-256 `C1A5A07996F36A945628EAC27657F949308C37353D4E590F30106625A6E18AD5`）、29 项专项测试、269 项全量测试、编译与差异检查、修复前后两组真实报告、凭据/临时文件扫描、任务级规格/质量复核与最终完整暂存差异审查

<a id="e3-design-001"></a>

## E3-DESIGN-001：最小普通终端执行轨迹设计规格

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(cli): design minimal terminal execution trace`
- 基线提交：`0bd986b test(eval): add reproducible golden coding task`
- 审核范围：`docs/superpowers/specs/2026-08-29-terminal-trace-design.md` 的目标与非目标、独立 `TerminalTrace` 架构、默认启用数据流、普通文本输出契约、按工具类型展示、脱敏与有界输出、异常隔离、TDD 验证和交付门禁
- 核心决策：终端展示与 `JsonlAudit` 分离；CLI 默认启用而库调用保持可选；模型、工具和结束事件采用稳定纯文本；写入工具展示有限 diff，命令展示退出码及 stdout/stderr 头尾，读取类工具不重复正文；ASK 明确输出 ALLOW/DENY
- 安全与失败边界：轨迹不落盘，不展示 reasoning、原始模型消息、请求 ID、认证信息或读取类结果正文；参数、diff、stdout/stderr 再次脱敏并以有标记头尾截取；任何 trace 输出异常不得改变 AgentLoop、工具结果、审计或停止原因
- 文档自检：共 109 行；暂存差异格式检查通过；目标、事件接口、展示契约、错误处理、测试策略和第 9.5 节门禁之间未发现矛盾
- 依赖与开源边界：设计不新增依赖，不引入 Rich、Agent SDK/框架或第三方 Agent 源码，不改变核心独立实现边界
- 已知限制：本提交只冻结 E3 设计，不实现终端轨迹或宣称测试通过；具体字符预算将在实施计划中锁定并以边界测试验证；最终录像、姓名 ZIP 和延期模拟答辩仍在后续阶段
- 审核结论：通过；确认书面设计可以纳入本地提交并用于生成实施计划；本结论不授权实现提交或远端推送
- 审核时间：2026-08-29 20:37:25 +08:00
- 审核依据：项目作者对架构、输出契约、异常/验证设计的逐段批准，以及书面规格文件 SHA-256 `A6AC68BFF65D4CA58E20D3764999FB235E37249B172DB131576B1F5D0E89B3B0` 的最终人工审核确认

<a id="e3-001"></a>

## E3-001：最小普通终端执行轨迹实现

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(cli): clarify terminal execution trace`
- 基线提交：`68e470b docs(cli): design minimal terminal execution trace`
- 审核范围：独立 `TerminalTrace`、AgentLoop 三事件观察接口、CLI 默认接入与 ASK 决策、按工具类型的有界展示、二次脱敏、终端控制字符安全编码、README/DESIGN、实施记录及 trace/CLI/loop 自动化测试
- TDD 证据：formatter 模块缺失、AgentLoop/CLI 接口缺失、审批 marker 缺失、ASK 参数凭据泄漏均先观察预期失败；最终质量审查发现终端控制字符可伪造事件后，先得到 `9 failed, 67 passed`，补充 U+2028/U+2029 边界又得到 `1 failed`，再以“脱敏、终端安全编码、截断”的最小实现转绿
- 本地验证：`tests/test_trace.py` 58 项通过；trace/CLI/loop 定向组合 105 项通过；`python -m pytest -q` 最终 348 项通过；`python -m compileall -q code_operator evals scripts tests`、暂存差异格式检查均通过
- 展示与安全边界：读取/搜索/目录工具不展示结果 payload；写入工具展示受限 diff，命令展示退出码、超时及受限 stdout/stderr；元数据保持单行，多行详情只保留普通 LF，C0/C1、DEL、Unicode 控制/格式字符和行段分隔符均转成可见转义；trace sink 失败不改变 AgentLoop 结果
- 独立复核：终端安全修复规格复核为 `FIX_SPEC_APPROVED`，完整组合质量复核为 `FINAL_QUALITY_APPROVED`，均无未解决 Critical/Important 问题
- 安全扫描：当前 API Key 值、私钥头、Authorization Bearer 值和禁止的凭据/审计/缓存/PID/临时文件名在完整暂存范围中的命中数均为 0；依赖差异为空
- 依赖与开源边界：未引入 Rich、Agent SDK/框架、第三方 Agent 源码或新依赖；未修改 JSONL audit schema、核心工具、安全策略、上下文管理或 Provider client
- 已知限制：100/40 字符宽度只是不落盘的 UTF-8 合成预览，尚未验证真实窄终端的中文双宽、具体编码和实际换行；终端历史、重定向或录屏仍可能保存输出，未识别的 diff/stdout/stderr 敏感内容仍需人工检查；Ubuntu 尚未验证；最终录像、姓名 ZIP 和延期模拟答辩仍在后续阶段
- 审核结论：通过；确认上述实现、测试、TDD 证据、文档、安全边界和已知限制可以纳入目标提交；本结论不授权远端推送
- 审核时间：2026-08-30 12:12:44 +08:00
- 审核依据：完整暂存补丁 `E3-001-review.patch`（SHA-256 `A90F5F3B37D9A96D51961960E5531B9CC489E19E8392B09B05B02E1670C5FE34`）、58 项 formatter 测试、105 项定向测试、348 项全量测试、编译与差异检查、凭据/临时文件扫描及两阶段复核

<a id="final-doc-001"></a>

## FINAL-DOC-001：最终提交 README 事实刷新

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs: refresh final submission readme`
- 基线提交：`4c65438 feat(cli): clarify terminal execution trace`
- 审核范围：`README.txt` 已验证状态刷新、最终 README 刷新实施计划，以及仓库外 v4.5 根计划中的 ZIP 预检命令校正；不修改生产代码、测试、依赖或预检脚本
- 红绿证据：初始事实探针因缺少 348 项、三个全新工作区 3/3 和普通终端轨迹声明而以 `AssertionError`、退出码 1 结束；只替换 README 第四节后探针转绿，前三节与基线完全一致，Python 字符数为 933
- 本地验证：提交预检专项 22 项通过；完整离线测试 348 项通过；`python -m compileall -q code_operator evals scripts tests`、UTF-8 预检帮助和暂存差异格式检查通过
- 公开声明边界：README 只增加现有证据支持的 E2/E3 状态，明确固定任务三次样本不代表整体成功率并保留 Ubuntu 未验证；不写姓名、真实凭据、本地绝对路径、视频/ZIP 完成状态或可选 E4/E5
- 预检命令：根计划改为在已生成 ZIP 上运行 `python -X utf8 scripts/preflight_submission.py --expected-name "<本人中文姓名>" "<本人中文姓名>.zip"`，与实际 argparse 接口一致；姓名继续保留占位符，最终提交复选框未提前完成
- 独立复核：Task 1 与 Task 2 均通过规格/质量复核；最终组合规格为 `FINAL_DOC_SPEC_APPROVED`，最终组合质量为 `FINAL_DOC_QUALITY_APPROVED`，无未解决 Critical/Important 问题
- 安全扫描：当前 API Key 精确值、私钥头、Authorization Bearer 值、本地绝对路径和依赖差异命中均为 0；姓名占位符仅出现在预期流程文本
- 已知限制：尚未执行最终真实 API 演示、完整 Git 历史扫描、模拟答辩、视频录制、ZIP 生成/解压复核或报名系统上传；本审核不将这些事项标记为完成
- 审核结论：通过；确认上述 README 事实更新、执行记录、验证证据、隐私边界和仓库外预检命令校正可以纳入目标提交；本结论不授权远端推送
- 审核时间：2026-08-30 15:00:34 +08:00
- 审核依据：完整暂存补丁 `FINAL-DOC-001-review.patch`（SHA-256 `62D27C92F96175749568357DDC9B9AE25BE4C8E2C679723D9A9D670758724341`）、根计划 SHA-256 `8891970B65961FC190998E09F1071D92718791B70A7193D1CEEF21255A9414D7`、933 字符计数、22 项专项测试、348 项全量测试、编译/差异/隐私扫描及多轮独立复核

<a id="e4-design-001"></a>

## E4-DESIGN-001：有界交互会话与文件 Undo 设计规格

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(session): design bounded interactive sessions and undo`
- 基线提交：`1d56878 docs: refresh final submission readme`
- 审核范围：`docs/superpowers/specs/2026-08-30-interactive-session-undo-design.md` 的排期偏离、目标与非目标、方案选择、双模式 CLI、可重入 AgentLoop、AgentSession、多用户上下文、Ctrl-C 配对、有界 ChangeJournal、`/undo`/`/new`/`/exit`、TDD 顺序、兼容门禁和回退规则
- 核心决策：带任务参数时保持一次性模式；无任务参数时保留同进程完整对话与工具历史；每条用户输入独立重置运行预算；Undo 只恢复本会话成功 `write_file/edit_file` 的直接文件修改；不实现跨进程 Resume、模型摘要或命令副作用恢复
- 安全与失败边界：修改后哈希不匹配、路径/类型变化或链接逃逸均拒绝撤销；中止 assistant turn 必须让已完成、被中止和未执行调用按原 ID 恰好配对；会话、checkpoint 和 Undo 栈不落盘；新真实 `kimi-k3` 会话探针需另行取得数据出境授权
- 文档自检：共 289 行；TBD、TODO、FIXME、待定项命中数为 0；暂存差异格式检查通过；五部分批准内容与书面规格的架构、消息、恢复、错误和验证边界未发现矛盾
- 依赖与开源边界：设计不新增依赖；OpenAI 与 Claude Code 官方文档只用于公开产品行为参考；核心会话、上下文、工具中止和 Undo 仍要求独立实现，不使用 Agent SDK/框架或第三方 Agent 源码
- 已知限制：本提交只冻结设计，不实现交互会话或 Undo；第一次完整录像仍按项目作者决定延期且不标记完成；正式视频、最终 ZIP、上传和延期模拟答辩仍待后续执行
- 审核结论：通过；确认书面设计可以纳入本地提交并用于生成实施计划；本结论不授权实现提交或远端推送
- 审核时间：2026-08-30 21:08:00 +08:00
- 审核依据：项目作者对五部分设计逐段批准并明确回复 `E4-DESIGN-001 人工审核通过`；书面规格 SHA-256 `6FCA086D1DA8F15B930215A243812A187B9248B265B773E2DFC8FCA0626C1529`；完整暂存范围、占位符、依赖与凭据形态检查

<a id="e4-001"></a>

## E4-001：有界交互会话与文件 Undo 实现

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`feat(session): add bounded interactive session and file undo`
- 基线提交：`b6d273e docs(session): design bounded interactive sessions and undo`
- 审核范围：有界内存 `ChangeJournal`、`write_file`/`edit_file` 变更记录和受保护 Undo、多 user turn 上下文与两级完整分组裁剪、顺序工具中止配对、可重入 `AgentLoop`、`AgentSession` 资源所有权与一次性本地事件、双模式 CLI 及 `/undo`/`/new`/`/exit`、对应自动化测试、README/DESIGN/REFERENCES、实施计划和仓库外 v4.5 计划证据
- TDD 证据：Journal 模块缺失、文件工具未接日志、旧上下文拒绝第二个 user turn、循环不保留历史及中止调用缺结果、Session 模块缺失、旧 CLI 缺持续循环和懒初始化、E4 文档事实缺失均先观察预期失败；独立规格和质量审查继续以失败测试复现并修复内部链接重定向、中止记账、返回式 `USER_ABORTED` 后续误执行、深层 JSON/不可序列化结果污染历史、撤销事件重复、关闭异常覆盖主异常、CLI 环境 Key 回显和异常退出码误分类
- 最终离线验证：主进程重新运行 `python -m pytest -q` 得到 `471 passed in 47.19s`；协议、客户端、注册表、假模型集成、Golden Eval 和提交预检兼容组得到 `115 passed in 42.18s`；`python -m compileall -q code_operator evals scripts tests` 与 `git diff --check` 退出码均为 0；提交预检专项 22 项通过；README.txt 为 874 个 Unicode 字符
- 兼容与安全结论：六个模型工具 schema、P0 回放字段、一次性退出码、Eval harness 和 JSONL audit schema保持兼容；Undo 不写 audit；依赖差异为空；当前环境 API Key 在完整候选中的精确命中数为 0，私钥头和 `sk-` 类模式为 0，两处 Bearer 模式均为明确的合成脱敏测试；候选中无 `.env`、audit、session/transcript/checkpoint、PID、临时探针或缓存产物
- 独立实现边界：核心会话、循环、协议处理、工具、安全策略、上下文和 Undo 均为本项目独立实现；OpenAI Responses 与 Claude Code 官方资料只作公开产品行为参考，不使用 Agent SDK/框架、第三方 Agent 生产代码或翻译式移植
- 独立复核：Task1 至 Task7 均完成规格和质量双审；最终完整候选审查结论为 `FINAL REVIEW APPROVED`，未发现未解决的 Critical、Important 或 Minor 问题
- 已知限制：无跨进程 Resume 或持久化会话历史；不回滚 `run_command` 或外部副作用；未执行新的 E4 真实 API 探针；Ubuntu、真实窄终端中文行为和第一次完整录像仍未验证
- 审核结论：通过；确认上述实现、测试、TDD 证据、文档、安全扫描、独立实现边界和已知限制可以纳入单一本地实现提交；本结论不授权远端推送
- 审核时间：2026-08-31 19:35:37 +08:00
- 审核依据：项目作者明确回复 `E4-001 人工审核通过`；完整 E4-001 人工审核包；主进程最终全量与兼容复验；任务级规格/质量复核；最终完整候选只读审查

本记录只覆盖上述审核范围，不扩大功能实现、远端推送或标签权限。

<a id="research-design-002"></a>

## RESEARCH-DESIGN-002：可靠性研究与 Coding Agent 双轨横评设计规格

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(research): design reliability and agent comparison study`
- 基线提交：`ca064ce feat(session): add bounded interactive session and file undo`
- 审核范围：`docs/superpowers/specs/2026-08-31-reliability-research-benchmark-design.md` 的 RQ0–RQ4、预注册假设、E4 真实 Session 前置探针、内部可靠性消融、code-operator/Claude Code/Kimi Code 必做双轨横评、三个冻结任务、隐藏评分、18 次正式运行、数据与凭据边界、失败分类、条件性 Ubuntu 验证、18:00 冻结和回退规则
- 核心研究决策：以实证方法和系统可靠性为主线；端到端产品比较用于外部定位，同模型配对只在精确模型可匹配时运行；外部结果不用于单因素因果归因；小样本只报告逐题 `0/2` 和总计 `x/6`，不冒充官方 SWE-bench 成绩或总体成功率
- 原优化路线合并：O1 恢复为独立真实 Session 探针；O2 扩展为三系统三任务科研横评；O3 只修复实验暴露的可复现根因；O4 仅在主体研究 16:00 前完成时限时 60 分钟验证 Ubuntu；O5 `/history` 等体验功能明确取消
- 审核过程：项目作者先回复 `RESEARCH-DESIGN-001 人工审核通过` 批准初稿，随后明确要求把 O1–O5 原优化路线与科研方案重新整合；修订稿完成范围和时间自审后，项目作者再次明确回复 `RESEARCH-DESIGN-002 人工审核通过`
- 文档自检：516 行、32,579 字节；最终 SHA-256 `5FF3E83CA03092A9488368F7A7B41E28C350608712B4F1F073CC49C5BAE62D41`；TBD、TODO、FIXME、模糊占位符和尾随空格命中数为 0；RQ 顺序、必做系统、18 次运行、16:00 条件门槛与 18:00 冻结口径一致
- 数据与安全边界：设计不授权安装 Kimi Code、调用 Moonshot/Anthropic/OpenAI/DeepSeek、发送任务数据或保存外部会话；任何真实探针和正式横评都必须先展示精确数据范围并取得新的明确数据出境授权；Key 仍只通过环境变量或官方安全登录提供
- 依赖与开源边界：设计不新增依赖，不使用 Agent SDK/框架或第三方 Agent 源码；SWE-bench、OpenAI、Claude Code、Kimi Code 和 Deep Code 公开资料只用于方法与产品行为参考，生产核心继续独立实现
- 已知限制：本提交只冻结研究设计；尚未制定详细实施计划、安装 Kimi Code、确认 Claude/Kimi 登录、创建三个任务、运行 O1、Pilot、18 次正式横评、内部消融或 Ubuntu 验证；正式录像、姓名 ZIP 和最终上传仍待完成
- 审核结论：通过；确认修订后的书面设计可以纳入本地设计提交并用于生成 TDD 实施计划；本结论不授权实现、外部数据发送或远端推送
- 审核时间：2026-09-01 00:12:18 +08:00
- 审核依据：项目作者对研究方向、双轨横评、任务评分、内部消融、时间门槛和修订合并方案的逐段批准，以及最终明确回复 `RESEARCH-DESIGN-002 人工审核通过`

本记录只覆盖上述研究设计，不扩大外部安装、真实 API、实现提交、远端推送或最终提交权限。

<a id="research-plan-001"></a>

## RESEARCH-PLAN-001：可靠性研究与三系统横评 TDD 实施计划

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(research): plan reliability and agent comparison study`
- 基线提交：`3e8542c docs(research): design reliability and agent comparison study`
- 审核范围：研究执行总控计划，以及 O1 E4 真实 Session 探针、48 行确定性可靠性消融、code-operator/Claude Code/Kimi Code 三系统横评、研究综合与条件性 Ubuntu 四份 TDD 子计划；同时覆盖仓库外 v4.5 根计划新增 E6 里程碑和原 E5 合并口径
- 固定执行顺序：`O1 -> 内部消融 -> 三系统 Pilot -> 18 个正式 Track A 单元 -> 只修复可复现根因 -> 自动报告 -> 条件性 Ubuntu -> 18:00 冻结`；原 O5 `/history`、持久化 Session 和跨进程 Resume 取消
- 研究冻结内容：O1 精确合成 fixture 与两回合提示；6 个上下文、9 个中止、9 个错误场景的 48 行两臂结果；T1/T2/T3 初始代码、可见测试、精确任务文字和隐藏评分语义；固定种子 `20260901`、15 分钟单元超时、3 个 Pilot、18 个正式单元及条件性 T2 Track B 四单元
- TDD 与失败处理：所有 runner、schema、fixture、隔离、grader、adapter 和分析器均按“先写失败测试并确认 RED、最小实现、确认 GREEN、全量回归”拆分；真实实验只保留冻结结果，只有 harness/fixture/grader/隔离缺陷才能废弃完整受影响平衡区组，不允许选择性重跑低分单元
- 数据与权限边界：计划不授权安装或登录 Kimi Code，不授权向 Moonshot/Anthropic/OpenAI/DeepSeek 发送数据；O1、Pilot、正式 18 单元、Track B 和可选系统均需分别展示完整合成外发范围并取得新的明确授权；本地不保留完整 prompt、响应、reasoning、stdout/stderr、凭据或临时绝对路径
- 时间与降级门槛：14:00 后只修复提交阻塞级安全/凭据/协议/核心执行缺陷；16:00 前主体研究与报告预览未完成则不启动 Ubuntu；Ubuntu 最多 60 分钟并于 17:00 停止；18:00 无条件冻结功能和研究变更；可选 Deep Code/DeepSeek、Codex、真实错误回灌和 Ubuntu 按固定顺序降级，不削减三套必做系统或 18 个正式单元
- 文档自检：五份计划共 1,681 行、75,989 字节；相对链接均存在，代码围栏成对，必需 Implementation Plan 头、Goal/Architecture/Tech Stack 和复选框结构齐全；TBD、TODO、FIXME、未定义函数体、尾随空白及不存在的生产测试命令命中数为 0；`git diff --check` 退出码 0
- 文件校验：总控计划 SHA-256 `472DEDFB9505D621388771745E4688F42FC3745C167416CCE1B0B9CE172064AD`；O1 计划 `49448CFE95097351813852914D30D44279FA7106C7D9A821D4CF1D3580F06917`；消融计划 `05F99C90E880D84743ACB268491E1687C44CB817AEAB9664ABC59B67E602B97D`；横评计划 `795FC5588E7C6ECECECDE473CC7506068A08BC4A16A284393DBC346E6EDBD0C6`；综合计划 `EB1916A625D9EEE66CD1AA9AC462D2A826DF295A248F3685D29AD97FEB462CA9`
- 基线验证：最终暂存候选重新运行 `python -m pytest -q` 得到 `471 passed in 48.62s`；提交预检专项 22 项通过；`python -m compileall -q code_operator evals scripts tests` 退出码 0；通用 API Key/Bearer/私钥模式在五份计划中的命中文件数为 0
- 依赖与开源边界：本里程碑不修改生产代码、测试或依赖；外部产品只通过公开 CLI 做系统级黑盒比较，不读取、复制或翻译移植其 Agent 源码，不引入 Agent SDK/框架；生产核心继续保持独立实现
- 已知限制：本审核只批准书面执行计划；尚未创建研究 fixture/runner/grader、安装 Kimi Code、确认 Claude/Kimi 安全非交互配置、调用真实模型、运行 O1/Pilot/正式横评/Track B/Ubuntu 或生成研究结果；录像、ZIP 和最终上传仍未完成
- 审核结论：通过；确认五份计划及根计划 E6 口径可以纳入本地计划提交并作为后续逐项执行依据；本结论不授权研究实现、外部安装/登录、数据出境或远端推送
- 审核时间：2026-09-01 01:20:03 +08:00
- 审核依据：项目作者明确回复 `RESEARCH-PLAN-001 人工审核通过`；五份计划完整内容、结构/路径/时间/证据命名自检、471 项全量离线测试、22 项提交预检、编译/差异/凭据形态检查

本记录只覆盖上述研究实施计划，不扩大实现、外部安装、真实 API、远端推送或最终提交权限。

<a id="o1-001"></a>

## O1-001：E4 真实 Session 探针实现与单次结果

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test(research): add real session probe evidence`
- 基线提交：`cc2ab85 docs(research): plan reliability and agent comparison study`
- 审核范围：冻结 greeting fixture 与两段提示、`evals/run_session_probe.py`、对应离线测试、获批研究仪器勘误、原始真实报告、根 v4.5 计划状态和本台账条目；不修改生产代码或依赖
- 研究仪器边界：所有动作仍经公开 `AgentSession` 接口；Eval runner 只读取 Reset 私有状态的元素数量并对真实自有客户端 `close()` 做委托计数，不保存消息、路径、哈希映射或事件正文；内部结构漂移和进程扫描失败均 fail-closed。项目作者已明确回复 `批准修订 O1 计划，允许评测器只读观测私有状态并包装 close 计数`
- TDD 证据：runner 缺失、双轮状态机缺失、CLI/安全写入缺失均先观察 RED；规格和质量复核继续用失败测试关闭 blanket approval、`write_file` 替代 `edit_file`、额外只读工具、同内容写入、运行时条目、损坏 junction、Toolhelp 不完整枚举、argparse Key 回显、UNC/设备路径、报告目录竞态与误删、未知异常泄漏，以及生命周期报告早于 close 冻结等问题
- 离线验证：O1 专项 `96 passed, 5 skipped`；Session/Loop/CLI 组合 `218 passed, 5 skipped`；全量 `567 passed, 5 skipped`；提交预检 `22 passed`；compileall、fixture `VALID` 和 `git diff --check` 通过。5 项跳过均为当前 Windows 权限不支持的符号链接场景，真实 NTFS junction 回归已执行
- 数据授权与单次运行：项目作者在收到完整 fixture、prompt、system/tool/history 可能外发范围、Moonshot 保留边界、配置、超时和扫描结果后，明确回复 `允许将上述完整合成数据发送到 Moonshot API，并运行一次 kimi-k3 O1 真实 Session 探针`；只运行一次，未重试
- 原始真实结果：`docs/evidence/e4-session-probe.json` 为 `FAIL / ACTIVE_SUBPROCESS`；第一回合 `COMPLETED`，5 轮、5 次工具调用、供应商 usage 6,908 tokens，owned-client close 次数 1，活动子进程计数 1，总耗时 37.984 秒；未进入第二回合、Undo 或 Reset。原始报告保持不变，SHA-256 为 `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`
- 审查后分类：隐藏 `Start-Process` 外层超时包装可重复地产生一个 `conhost.exe` 直接子进程，直接启动计数为 0，因此 raw `ACTIVE_SUBPROCESS` 不作为 Agent 工具进程泄漏证据；但首轮 5 次调用必然不满足冻结的 `read_file -> edit_file -> run_command` 精确三事件轨迹，样本仍为 `FAIL_MODEL_BEHAVIOR_WITH_OUTER_WRAPPER_CONFOUNDER`。该结果不并入三系统横评统计，也不触发生产缺陷修复分支
- 安全扫描：报告 JSON 有效，三个非空哈希均为 64 位小写十六进制；当前 API Key、Bearer、Authorization、私钥头、完整 prompt、本地绝对路径在报告中的命中均为 0；当前 Key 在完整候选中的精确命中为 0；报告临时文件、诊断临时文件和 Session fixture 缓存均为 0
- 独立复核：Task 1–3 均完成实现、规格与质量复核；计划勘误复核为 `FINAL REVIEW APPROVED`；真实结果双层分类复核为 `RESULT REVIEW APPROVED`，确认不修改原始报告、不重跑、不把外层混杂误称为生产资源泄漏
- 已知限制：本次样本没有完成第二回合、Undo 和 Reset，不能支持 H0 成功，也不是成功率；raw failure code 受外层 Windows 控制台宿主混杂。报告创建于 `2026-09-01 18:14:36 +08:00`，晚于预注册的 18:00 无条件冻结；项目作者后来的精确数据授权只作为这一次 O1 例外，不代表 O1 按原时间表完成，也不授权冻结后继续消融、Pilot、正式横评或重试。Ubuntu、三系统 Pilot/正式横评、研究报告和完整录像仍未执行
- 审核结论：通过；认可 O1a 实现、原始失败结果、外层包装混杂诊断、验证证据与已知限制可作为 evidence-only 检查点纳入本地提交。该通过不把 O1a 解释为 Session 成功，不授权重跑、其他研究或远端 push
- 审核时间：2026-09-02 01:13:04 +08:00
- 审核依据：项目作者在收到 O1a + O1b 统一审核包后明确回复 `O1-001、O1B-IMPLEMENTATION-001、O1B-RESULT-001 人工审核通过`

本审核只认可上述 O1 evidence-only 检查点，不扩大真实 API、后续外部实验、生产修改、远端推送或标签权限。

<a id="o1b-design-001"></a>

## O1B-DESIGN-001：真实 Session 复现实验修订设计

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(research): design O1b session replication`
- 基线提交：`cc2ab85 docs(research): plan reliability and agent comparison study`
- 审核范围：`docs/superpowers/specs/2026-09-01-o1b-session-replication-design.md` 的 O1a/O1b 证据分离、主要 Session 判据、次要理想轨迹、进程基线差分、固定三次 reservation、版本/配置冻结、结果分类、失败处置、数据边界和 TDD 门禁
- 核心修订：O1a 原始失败报告保持不变；O1b 将 Session 语义正确性与理想最短轨迹分开报告；最多三个 outbound attempt 均需先排他持久化 reservation，编号不得复用，提前停止和缺失结果均如实进入汇总
- 研究有效性：生产 tree、Eval 执行闭包、fixture、prompt、模型参数、超时、Python/httpx/pytest 与平台均冻结并跨 attempt 校验；生产变化进入 O1c，评测协议变化进入新协议队列，不混合不同版本
- 进程边界：只报告相对基线新出现且结束时仍存活的直接子进程计数，不保存 PID/名称/命令行，不把非零差分未经归因写成 Agent 泄漏
- 结果口径：只报告原始计数；有效样本至少 2 个且至少 2 个主要 PASS 才为 `O1B_SUPPORTED`，一个主要 PASS 为 `MIXED`，全部失败为 `NOT_SUPPORTED`，有效样本少于 2 个为 `INCONCLUSIVE`；理想轨迹另报且不能选择性省略
- 权限边界：项目作者撤销 O1 的 18:00 时间冻结并批准本设计，但该审核不授权 Moonshot 数据出境、真实运行、其他研究阶段、外部安装、本地实现提交或远端推送
- 自检与复核：最终文档 124 行；无 TBD/TODO/FIXME 或尾随空白；`git diff --check` 通过；独立规格复核结论为 `O1B DESIGN REVIEW APPROVED`
- 审核结论：通过；可以把书面设计纳入本地设计提交，并据此制定详细 TDD 实施计划
- 审核时间：2026-09-01 18:57:34 +08:00
- 审核依据：项目作者明确回复 `O1B-DESIGN-001 人工审核通过`；书面设计、两轮独立规格复核及上述自检证据

本审核只覆盖 O1b 书面设计，不授权实现提交、真实 API、其他外部实验或远端推送。

<a id="o1b-plan-001"></a>

## O1B-PLAN-001：真实 Session 复现实验实施计划

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`docs(research): plan O1b session replication`
- 基线提交：`259cf67 docs(research): design O1b session replication`
- 审核范围：`docs/superpowers/plans/2026-09-01-o1b-session-replication-implementation.md` 的基线冻结、reservation/版本哈希、双层判据、进程差分、固定 attempt/提前停止汇总、CLI、安全扫描、真实运行门禁与人工审核步骤
- TDD 结构：Task 0 固定 O1a 基线；Task 1–4 每项明确失败测试、RED、最小实现、GREEN、规格复核和质量复核；Task 5 执行专项/组合/全量/编译/预检/扫描，并在外发授权处停止
- 关键顺序：真实模式先完成纯本地校验和 reservation 排他落盘复读，再进入 `run_probe(client=None)`；客户端继续由 `AgentSession` 拥有、幂等关闭和计数，不因评测外壳丢失生命周期证据
- 结果边界：主要 Session 判据与 ideal trace 分开；O1a 原始报告字节和哈希保持不变；固定三个 attempt 全部保留，提前停止不伪造未执行结果；不同生产或评测协议版本不混样
- 权限边界：计划审核只授权离线 Eval/TDD 实施和本地验证，不授权创建正式 reservation、调用 Moonshot、安装外部工具、开展其他研究、提交实现或远端推送
- 自检：最终计划 569 行、48 个代码围栏；无 TBD/TODO/FIXME、模糊占位符或尾随空白；`git diff --check` 通过；项目作者审核前的计划 SHA-256 为 `79C9A0C8503EB6603172EA8EB2A74C6C85190D03F698A59CAF8754EDCC2B5E6F`
- 独立评审限制：一次独立子 Agent 计划评审因当时额度用尽未返回，未伪造批准；主审随后发现并修正了外层 client factory 会破坏 owned-client 证据的问题，最终仍由项目作者完成书面审核
- 审核结论：通过；允许据此开始离线 TDD，完成后仍需独立规格/质量/总审与新的实现人工审核
- 审核时间：2026-09-01 19:18:09 +08:00
- 审核依据：项目作者明确回复 `O1B-PLAN-001 人工审核通过`；完整计划、自检证据和已批准的 `O1B-DESIGN-001`

本审核不授权真实 API、正式 O1b reservation、实现提交、其他研究阶段或远端 push。

<a id="o1b-implementation-001"></a>

## O1B-IMPLEMENTATION-001：O1b 离线实现与总验证候选

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test(research): add real session probe evidence`
- 基线提交：`7f41171 docs(research): plan O1b session replication`
- 审核范围：O1b Task 5 Step 1–3 的离线验证、证据不变量检查、事实计划收敛、根计划开放范围和本候选台账；不包含真实 Moonshot 运行、正式 reservation/result/summary、消融、横评、Track B、Ubuntu、提交或推送
- 离线验证：`python -m pytest -q tests/test_session_probe.py` 为 `181 passed, 7 skipped in 108.46s`；Session/Loop/CLI 组合为 `303 passed, 7 skipped in 110.81s`；`python -m pytest -q` 为 `652 passed, 7 skipped in 167.69s`；`python -m compileall -q code_operator evals scripts tests` 退出码 0；`tests/test_submission_preflight.py` 为 `22 passed in 0.47s`；`git diff --check` 退出码 0（仅有 LF/CRLF 转换提示）
- 跳过项：7 项均为 Windows `WinError 1314`，因当前权限不允许创建合成符号链接；分别覆盖 manifest 哈希链接拒绝、evidence 根目录链接、fixture 根链接、fixture 后代链接、workspace 链接和报告目录链接两项；真实 NTFS junction 回归执行且未因该权限跳过
- 证据不变量：O1a `docs/evidence/e4-session-probe.json` SHA-256 仍为 `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`；O1b reservation、单次结果和 summary 均不存在；清理验证后仓库内 `__pycache__`、`.pytest_cache`、`.pyc/.pyo`、`.tmp/.part` 均为 0，evidence 临时/诊断文件名为 0；`code_operator/` 与 `requirements.txt` 相对基线无改动
- 安全与隐私扫描：`CODE_OPERATOR_API_KEY` 仅作为不透明环境变量用于精确匹配，值未打印；在 94 个仓库候选文件及根 v4.5 计划中的精确命中为 0。O1b evidence 文件数为 0，故其中 API Key/Bearer/Authorization/私钥头、绝对路径和敏感字段命中均为 0。仓库通用模式扫描发现既有合成脱敏测试/计划文本的 Bearer 23、Authorization 10、私钥头 1，不能将仓库泛模式计数写成 0，也未发现真实凭据值
- 事实边界：本次结果只支持 O1b 离线 evaluator/协议实现和运行前门禁；不支持真实 Session 稳定性、成功率或任何横评结论。O1a 仍作为独立失败 evidence-only 候选，`O1-001` 保持待审核
- 最终独立评审：`O1B OFFLINE REVIEW APPROVED`；规格一致性、代码质量、安全/隐私、研究有效性和 O1a 原始不变性分别通过。研究有效性仅限离线 evaluator、协议实现和运行前门禁，安全结论保留既有合成模式命中限制；独立复跑产生的可再生缓存已安全清理。该独立结论不表示项目作者人工审核通过，不授权创建 O1b evidence、真实 API、其他研究阶段、提交或远端 push
- 审核结论：通过；认可 O1b 离线 evaluator/协议实现、TDD 证据、总验证和运行前门禁可与真实失败 evidence 一并纳入本地提交。该通过不表示真实 Session 成功，不授权其他研究或远端 push
- 审核时间：2026-09-02 01:13:04 +08:00
- 审核依据：O1b Task 5 Step 1–3 的新鲜命令输出、哈希/文件状态检查、缓存清理复核、事实文档差异、独立离线总审结论，以及项目作者明确回复 `O1-001、O1B-IMPLEMENTATION-001、O1B-RESULT-001 人工审核通过`

本审核只认可 O1b 离线实现与门禁纳入本地提交，不扩大真实 API、外部实验、生产修改、远端推送或标签权限。

<a id="o1b-result-001"></a>

## O1B-RESULT-001：O1b 三次真实 Session 结果候选

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test(research): add real session probe evidence`
- 基线提交：`7f41171 docs(research): plan O1b session replication`
- 授权边界：项目作者在收到完整 fixture、两段 prompt、system/tool/history 动态范围、endpoint/model、三次固定 attempt、逻辑请求与重试上界、参数、停止规则、不会发送的数据、报告字段及费用/随机性风险后，明确允许向 Moonshot `kimi-k3` 串行运行最多三个 O1b attempt；该授权不包含第四次、其他数据、其他模型、消融、横评、提交或 push
- 运行顺序：固定 `01 -> 02 -> 03` 串行完成；每次联网前先排他写入并复读 reservation，三个编号均未复用，实验 attempt 未重跑；生产哈希、evaluator/protocol 哈希和非敏感配置跨三次一致
- 单次结果：attempt 01/02/03 均为有效 `FAIL / UNEXPECTED_COMMAND`；模型轮次 `7/8/8`，工具调用 `7/8/8`，供应商 usage `11000/13147/13314` tokens，耗时约 `65.671/105.078/85.547` 秒。三次均在第一回合 `COMPLETED` 后因出现非冻结命令而停止，未进入第二回合、Undo 或 Reset；报告不保存命令 payload，不能臆测具体命令
- 资源证据：三次 `baseline_direct_subprocess_count=0`、`new_residual_direct_subprocess_count=0`、`close_idempotent=true`、`owned_client_close_calls=1`、`session_artifact_count=0`；未观察到 O1a 外层包装造成的进程计数混杂，也未暴露可复现生产资源缺陷
- 汇总结论：`planned=3`、`attempted=3`、`valid=3`、`primary_passes=0`、`invalid_infra=0`、`ideal_trace_passes=0`、未执行编号为空，固定停止原因为 `COMPLETED_PLANNED_ATTEMPTS`，分类为 `O1B_NOT_SUPPORTED`
- 研究解释：三个冻结样本均未完成 O1b 主要 Session 闭环，只支持“在该冻结任务、当前 kimi-k3 和当前配置下，本实验未获得支持证据”；不能泛化为 kimi-k3 或其他 coding agent 的总体能力，不能称总体成功率，也不删除、不覆盖或重新分类 O1a
- 原始文件哈希：01 reservation `0946FB2B3FC1A87CE2C741C601F2EB2E7AA0FC46076E37E709A91503D7C88670`，01 result `BD8E2394B90929A4C1021A115F4F0DC8DDA118552E4C3E229492C1C7104F3E50`；02 reservation `76A1868225761267EBCEE6C7FD7C4D405AC201C93264B516117F45FD64D71519`，02 result `141A84E2908633C102A66E856FFD15A633E948CD5F9E0DA056CDA4250E79EC2B`；03 reservation `24B46C8D4BEE45425D4F9293986A01E39CCF6AC9DBFF386E32D92EC9CA4CA88E`，03 result `19EC2C5F44E5EE2BBB4BB4A0C2EEEF2E4AFBDBE146FB170C31CBEC5168DFDAA7`；summary `E83F5197C1994C099FEFAE42A68B5987F10FAB73478EF4392AD80E762E17D0B2`
- 回归与扫描：真实运行后全量 `652 passed, 7 skipped in 146.98s`，提交预检 `22 passed in 0.43s`，compileall 与 `git diff --check` 通过；7 个 O1b JSON 中当前 Key 精确命中为 0，完整 prompt、fixture 正文、Bearer、Authorization、私钥头和 Windows 绝对路径命中均为 0
- O1a 不变性：`docs/evidence/e4-session-probe.json` SHA-256 保持 `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`，O1a 与 O1b 继续分列
- 独立结果复核：`O1B REAL RESULT REVIEW APPROVED`；三份 reservation/result、文件系统先后关系、summary 六个输入哈希与离线重建、七个 O1b 原始文件哈希、安全字段和事实文档均一致，定向 summary 复验为 `31 passed, 157 deselected`。审查同时保留无独立完成时间字段、无数字签名/外部可信哈希锚、进程差分观测范围有限和三样本不可泛化等限制
- 审核结论：通过；认可三个不可改写的 reservation/result、离线 summary、`O1B_NOT_SUPPORTED` 分类、原始哈希、回归证据与研究限制可纳入本地 evidence 提交。该通过不是对 Session 成功、kimi-k3 总体能力或成功率的肯定，也不授权第四次、协议修改、其他研究或远端 push
- 审核时间：2026-09-02 01:13:04 +08:00
- 审核依据：三个不可改写的 reservation、三个不可改写的单次结果、离线 summary、文件哈希、真实运行后回归、安全扫描、`O1B REAL RESULT REVIEW APPROVED`，以及项目作者明确回复 `O1-001、O1B-IMPLEMENTATION-001、O1B-RESULT-001 人工审核通过`

本审核认可 O1b 真实失败结果纳入本地提交，不授权重跑、追加样本、修改协议、开展其他研究或远端 push。

<a id="ci-fix-001"></a>

## CI-FIX-001：Windows 全新 checkout 冻结字节修复候选

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`fix(ci): preserve frozen files with LF`
- 基线提交：`8f51ee3 test(research): add real session probe evidence`
- 触发证据：首次普通 push 成功后，GitHub Actions `offline-tests` run `33539045156` 的 Windows Python 3.11 job 在 `Run offline tests` 失败；compile 与依赖安装均成功
- 根因复现：系统 Git 配置为 `core.autocrlf=true`，仓库没有 `.gitattributes`。从 `8f51ee3` 创建的全新 Windows clone 将已审核的 LF 文件转换为 CRLF，`tests/test_session_probe.py::test_fixture_and_prompts_match_approved_bytes` 在第 748 行稳定失败，差异为 `b'\r\n' != b'\n'`；这也改变生产 tree、evaluator 闭包和原始 evidence 的工作区 SHA-256
- 最小修复：仅新增 `.gitattributes`，对 `.gitattributes`、`*.py`、`*.md`、`*.txt`、`*.json` 固定 `text eol=lf`；不修改任何 Python 逻辑、测试判据、研究结论或原始 JSON 内容。独立审查指出 `*.yml/*.yaml` 虽无风险但不属于冻结闭包，最终候选已移除这两条
- GREEN 证据：把候选属性提交到临时复现仓库后再次全新 clone，抽查 O1a/O1b evidence、生产文件、requirements、Eval runner、fixture/prompt 和 O1b 设计的 SHA-256 均与原审核工作区一致；原失败测试 `1 passed`，与 CI 相同的 compile + 全量命令为 `652 passed, 7 skipped in 149.88s`。独立审查产生的 fixture 缓存被安全清理后，主工作区使用 CI 空环境变量再次全量通过 `652 passed, 7 skipped in 173.37s`
- 安全与范围：候选只新增 `.gitattributes` 和本审核记录；不改已推送历史，不 amend/rebase/squash，不重跑 O1/O1b，不联网调用模型。修复提交和再次 push 均须重新完成审核与第 9.5 节门禁
- 审核结论：通过；认可根因、最小 LF 属性范围、RED/GREEN 证据和原始 evidence 不变性，可形成普通追加提交。该审核不授权历史改写或再次 push，再次 push 仍须重新执行第 9.5 节并取得新授权
- 审核时间：2026-09-02 02:07:53 +08:00
- 审核依据：失败 CI job/step 状态、两个全新 Windows clone 的字节哈希对比、首个失败断言、候选全量回归、`CI-FIX SPEC/QUALITY APPROVED`，以及项目作者明确回复 `CI-FIX-001 人工审核通过`
- 独立复核：`CI-FIX SPEC/QUALITY APPROVED`；确认根因、哈希稳定性、文本扩展范围和审核口径成立，技术上可提交，流程上仍等待项目作者审核与新的第 9.5 节门禁

本审核认可 CI checkout 字节稳定性修复形成普通本地追加提交，不授权重新 push、历史改写或其他变更。

<a id="research-ablation-001"></a>

## RESEARCH-ABLATION-001：48 行确定性可靠性消融候选

- 审核状态：人工审核通过
- 审核人：项目作者（通过当前协作任务明确确认）
- 目标提交：`test(research): add deterministic reliability ablations`
- 基线提交：`eb32af2 fix(ci): preserve frozen files with LF`
- 审核范围：`evals/reliability/` 的 schema、上下文/中止/错误三类双臂实验，`evals/run_reliability_study.py`，`tests/test_reliability_study.py`，排他生成的 `docs/evidence/reliability-study.json`，两份研究计划的事实收敛和本记录；不修改 `code_operator/` 生产代码或依赖，不包含外部 Agent 安装、登录、Pilot、18 次正式横评、Track B、Ubuntu、提交或 push
- TDD RED：Task1 首次为 `ModuleNotFoundError: evals.reliability`；Task2 首次缺 context 模块，双层判定测试为 3 项失败，非法上下文分类测试为 1 项失败；Task3 首次缺 abort 模块，后续指标来源、精确 payload 和 baseline 计数测试分别先失败；Task4 首次缺 error 模块，归因谓词修订测试先因 `_attributable_failure` 缺失而失败；Task5 报告模块缺失导致 4 项失败，完整 manifest/路径测试新增后 8 项失败，actual retry manifest 测试 2 项失败，内嵌 manifest/context summary 测试 1 项失败，写入故障清理测试 1 项失败
- Task2 批准勘误：冻结矩阵未改；C1 预算 229、最低完整链 249、短缺 20，C2 预算 357、最低完整链 377、短缺 20，均为 `passed=false / CONTEXT_LIMIT / safe_stop=true / protocol_checked=false`。C3–C6 才进入实际协议检查，4 行均保留最新完整组且无配对违规；报告汇总固定为生产 6 行、实际检查 4 行、安全停止 2 行、已生成上下文配对违规 0 行、H1 全场景成功 false
- RQ2 结果：9 个冻结中止位置中，生产臂 9/9 从下一次公开模型请求捕获到每个原 ID 恰好一次且有序；中止前 payload、中止点 `USER_ABORTED` 和后续 `NOT_EXECUTED_AFTER_ABORT` 均精确匹配并可继续下一任务。立即停止弱基线 9/9 遗漏当前或后续结果。冻结矩阵只覆盖 handler 返回 `USER_ABORTED`，未覆盖更广的 `KeyboardInterrupt` 和结果序列化异常，不据此泛化
- RQ3 结果：九个场景中两臂记录相同的 same/corrected/unrelated 分类；模糊载荷 9/9 不可由稳定错误码归因，结构化载荷 9/9 可归因。该结果只比较可观察错误表示，不调用模型，不证明结构化反馈导致真实模型恢复
- 正式报告：共 48 行，context/abort/error 分别为 12/18/18；按 `(mechanism, scenario_id, arm)` 排序；嵌入脱敏的完整场景 manifest，actual retry 工具与参数摘要也受哈希承诺；manifest SHA-256 为 `96240d8b0f8be0ff6e06accb79154e0841f20a0df307d6ddac5fba137e93a4e7`，报告文件 SHA-256 为 `EF1C9EC453933854F6F4EDB4269E4C49B8642E9E0D19D82B95A10DA4E81F63D3`；无时间戳且内存生成字节稳定，目标存在时拒绝覆盖，写入中断时清理由本次调用创建的残缺文件
- GREEN 与回归：reliability 全集 `52 passed in 0.43s`；O1b 探针专项在清理本次 compileall 生成的两个 fixture `__pycache__` 后为 `181 passed, 7 skipped in 102.23s`；最终新鲜全量为 `704 passed, 7 skipped in 149.23s`；提交预检 `22 passed in 0.38s`；`python -m compileall -q code_operator evals tests`、`mypy evals/run_reliability_study.py evals/reliability --follow-imports=skip --ignore-missing-imports`（6 个新源文件零错误）和 `git diff --check` 均通过
- 失败运行处置：第一次全量得到 `656 passed, 6 skipped, 49 failed`，共同原因是此前 compileall 在冻结 O1b source fixture 内生成两个被忽略的 `__pycache__`，fixture 防污染校验按设计拒绝并导致级联；只删除这两个已验证位于 `evals/session_probe/project/` 内的可重建缓存后，受影响专项和最终全量均通过。未删除、覆盖或改写正式 evidence
- 安全与隐私：正式报告中的 API Key/Bearer/private-key 形态命中为 0；新增候选通用扫描只命中 `tests/test_reliability_study.py` 中 3 个明确合成扫描哨兵。报告递归拒绝禁止字段、Windows 盘符/UNC/POSIX 绝对路径和嵌套凭据形态；不保存 prompt、模型、供应商、用户名、环境值、请求头、reasoning、完整命令输出或临时绝对路径；fixture 缓存复核为 0
- 独立复核：Task1–Task5 均经过实现、规格和质量审查；期间按审查意见修复固定窗口验证、Task2 空验证美化、异常误分类、Task3 指标公式化和宽松 payload、Task4 归因/重试混淆、manifest 输入承诺、路径/凭据扫描及部分写入残留。最终 Task5 规格结论 `APPROVED`、质量结论 `QUALITY APPROVED`
- 开源与实现边界：全部弱基线只存在 `evals/reliability/`，不接入生产 CLI；未引入 Agent SDK/框架、第三方 Agent 源码或新增依赖；当前候选不修改 `code_operator/`，生产核心继续独立实现
- 已知限制：内部消融不是外部产品横评或成功率；H1 原“全部上下文场景成功”被实际结果否定；中止矩阵的异常类型覆盖有限；结构化错误实验不含真实模型；Windows 符号链接相关 7 项仍因当前权限跳过。Claude Code/Kimi Code Pilot、18 个 Track A 单元、研究综合、条件性 Ubuntu、录像、ZIP 和最终上传均未完成
- 审核结论：通过；认可 48 行离线确定性消融、正式 evidence、TDD/回归/扫描证据及其限制可以形成本地语义化提交。该通过不授权 push、外部安装/登录、数据出境、Pilot、正式横评或生产修复
- 审核时间：2026-09-02 12:10:32 +08:00
- 人工审核依据：项目作者在收到完整 `RESEARCH-ABLATION-001` 审核包后明确回复 `RESEARCH-ABLATION-001 人工审核通过`
- 候选形成时间：2026-09-02 12:01:17 +08:00
- 审核依据：完整候选 diff、上述 RED/GREEN 命令、正式报告/manifest 哈希、全量/预检/编译/类型/差异检查、安全扫描及独立规格/质量复核

本审核只覆盖离线确定性消融及其证据，不扩大外部研究、生产修复、远端推送或最终提交权限。
