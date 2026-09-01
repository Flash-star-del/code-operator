# O1b 真实 Session 复现实验设计

**状态：** 已通过 `O1B-DESIGN-001` 项目作者人工审核（2026-09-01）

**目的：** 在保留 O1a 原始失败证据的前提下，修正评测器的构念混杂，用最多三个全部保留的真实 `kimi-k3` 样本分别回答“E4 Session 核心闭环能否完成”和“模型是否采用理想最短工具轨迹”。

## 1. 修订依据与研究边界

O1a 的原始报告保持不变。该样本的 raw 结论为 `FAIL / ACTIVE_SUBPROCESS`：第一回合达到 `COMPLETED`，但使用 5 次工具调用；外层 15 分钟隐藏 `Start-Process` 包装又引入一个 `conhost.exe` 直接子进程。离线诊断因此得到双层结论：`ACTIVE_SUBPROCESS` 受评测控制器混杂，5 次调用则独立不满足原预注册的精确三事件轨迹。

项目作者随后明确表示时间不必冻结，并认可继续尝试 O1。该决定仅重新开放 O1b 的设计、离线实现、最多三次 Moonshot 复现及其审核，不自动授权内部消融、三系统横评、外部工具安装、其他供应商调用、远端推送或改变最终提交边界。每次真实运行前仍需展示完整合成外发范围，并取得覆盖具体次数的新数据出境授权。

O1b 不删除、不覆盖、不重新分类 O1a；不把 O1a 排除出时间线，也不把 O1b 的后验设计冒充原始预注册。两者在文档和证据文件中始终分列。

## 2. 研究问题与双层判据

### 2.1 主要问题：Session 核心闭环

真实 `kimi-k3` 能否在同一个 `AgentSession` 中完成以下可由程序验证的行为：

1. 第一回合读取冻结 fixture，只修改 `greeting.py`，恰好执行一次成功的直接编辑和一次获批的冻结测试命令，独立后测通过。
2. 第二回合沿用既有消息历史，不修改文件，恰好执行一次获批的同一测试命令，独立后测再次通过；最终回答中出现精确返回值 `你好，小明！`，但报告只保存布尔结果，不保存回答正文。
3. `undo()` 不调用模型，将 `greeting.py` 恢复至初始 SHA-256，并将 Undo 深度恢复为 0。
4. `reset()` 后只保留 system 消息，完整读取哈希、Journal 与待通知事件均为空。
5. `close()` 可重复调用，自有客户端实际只关闭一次；运行结束时没有相对进程基线新出现且仍存活的直接子进程，也没有 transcript、session 或 checkpoint 文件。
6. 报告不保存 prompt、模型回答、reasoning、工具原始 payload、PID、进程名、命令行、绝对临时路径或凭据。

第一回合允许额外的只读观察工具 `read_file`、`grep` 和 `list_dir`；第二回合同样允许额外只读观察。任何额外写入、第二回合写入、额外或非冻结命令、失败的必需编辑/命令、范围外文件变化、链接/重解析点或协议错误都使主要判据失败。这样检验的是 Session 的语义正确性与安全边界，而不是把模型探索次数误当成 Session 缺陷。

### 2.2 次要问题：理想工具效率

理想轨迹仍按 O1a 原定义单独计算：

- 第一回合恰好为 `read_file -> edit_file -> run_command`，三次均成功。
- 第二回合恰好为一次成功的 `run_command`。

报告分别记录两个回合和整体的 `ideal_trace` 布尔值、模型轮次、工具调用数与供应商 usage。理想轨迹不再导致 runner 提前返回，也不单独否定主要 Session 闭环。主要结果和效率结果必须同时披露，不能只展示更好的一项。

## 3. 进程观测修正

评测器完成报告目录校验、配置加载和其他控制器初始化后，在创建 `AgentSession` 前读取一次当前评测进程的直接子进程 PID 集合，在 Session 关闭后读取第二次。主要资源判据只统计“结束集合减去基线集合”的新存活直接子进程数；外层控制器在基线时已经存在的 `conhost.exe` 因而不会进入差分。

报告只保存 `baseline_direct_subprocess_count` 和 `new_residual_direct_subprocess_count` 两个非负计数，不保存集合内容。任一次平台扫描不可用或结果结构非法时记为 `INVALID_INFRA / SUBPROCESS_SCAN_FAILED`，不得假定为 0。Linux 继续使用 `/proc` 的父 PID 关系，Windows 继续使用 Toolhelp 快照；不增加第三方依赖。

该差分只能证明运行结束时没有相对基线新出现且仍存活的直接子进程，不能证明运行期间从未创建进程，也不能覆盖已经重新托管或脱离父进程的进程。仅凭父子关系还不能区分 Agent、评测器或外层控制器在基线后创建的进程；出现非零差分时必须先离线归因，不能直接写成 Agent 泄漏。现有命令工具的 Job Object/进程树测试继续承担终止语义证据，O1b 不夸大自己的观测能力。

## 4. 样本、停止规则与结果解释

O1b 最多允许三个真实 outbound attempt，编号固定为 `01`、`02`、`03`。每个 attempt 使用同一冻结 fixture、两段相同 prompt、`kimi-k3`、相同配置和全新临时工作区；每次都写入独立、排他创建的 reservation 与脱敏结果 JSON，任何已存在文件均拒绝覆盖。

CLI 必须在创建客户端或发送请求前，先用排他创建和落盘校验写入该编号的 reservation。reservation 一旦创建，该编号即保守地视为已占用，即使请求尚未发出、进程崩溃或结果写入失败也不得复用。reservation 只保存 attempt 编号、协议版本、非敏感配置、冻结文件哈希和创建时间，不保存 Key、prompt 正文、绝对路径或任何供应商内容。结果报告使用另一个固定文件排他写入；reservation 存在而结果缺失时，汇总将其记为 `RESERVED_NO_RESULT / INVALID_INFRA`，不得补造单次报告。

获得覆盖三次的明确授权且未触发停止条件时，三个 attempt 全部执行，不因第一次成功而提前停止，也不为得到成功选择性追加第四次。触发以下任一条件时停止剩余真实运行：

- 怀疑凭据、用户数据或未授权内容外泄；
- 可复现的 code-operator 生产安全、协议、Undo、Reset、Close 或资源缺陷；
- 供应商认证、限流、网络或费用边界使继续运行不安全；
- 项目作者撤回授权。

结果汇总读取三个固定编号的 reservation，以及与已占用编号对应的可选结果文件。reservation 必须从 `01` 开始连续，出现编号空洞、结果无 reservation、哈希或配置不一致都使汇总失败关闭。汇总记录 `planned_attempts=3`、`attempted_count`、`valid_attempts`、`invalid_infra_count`、未执行编号和固定 `stop_reason`；提前停止不为未执行编号伪造单次结果。`stop_reason` 仅允许 `COMPLETED_PLANNED_ATTEMPTS`、`SECURITY_STOP`、`PRODUCTION_DEFECT_STOP`、`PROVIDER_BOUNDARY_STOP`、`AUTHORIZATION_WITHDRAWN` 或 `EVALUATOR_PROTOCOL_STOP`。

汇总只报告 `primary_passes / valid_attempts`、`invalid_infra_count / attempted_count` 与 `ideal_trace_passes / valid_attempts` 的原始计数，不给小样本置信区间，不泛化为总体成功率。`valid_attempts` 只计单次结果为 `PASS` 或 `FAIL` 的 attempt；`INVALID_INFRA` 和 `RESERVED_NO_RESULT` 不进入主要判据或理想轨迹分母：

- 有效 attempt 至少 2 个，且其中至少 2 个通过主要判据：`O1B_SUPPORTED`；若第三个是 `INVALID_INFRA`，必须同时披露，不能写成 2/3 成功。
- 有效 attempt 至少 2 个，但仅 1 个通过：`O1B_MIXED`，只能证明在一个样本中可完成，不能宣称稳定。
- 有效 attempt 至少 2 个且全部失败：`O1B_NOT_SUPPORTED`。
- 有效 attempt 少于 2 个：`O1B_INCONCLUSIVE`，无论其中是否出现成功。

`ideal_trace` 只作为效率指标另报 `x/N`。O1a 始终作为更早的独立失败样本列在 O1b 之前，不进入 O1b 的分母。

## 5. 失败处置

- **评测基础设施缺陷：** 保留 reservation 和已经生成的 `INVALID_INFRA` 报告；若进程在结果前终止，汇总保留 `RESERVED_NO_RESULT`。先以失败测试复现并最小修复；任何会改变实验协议或 evaluator hash 的修复都停止当前 O1b 队列，后续使用新协议队列，不能混入原队列。
- **code-operator 生产缺陷：** 立即停止 O1b 剩余 attempt；先写失败测试、完成最小修复和全量回归。修复后必须新建 O1c 版本队列，不能把不同生产版本混入 O1b。
- **模型行为失败：** 保留原结果并继续尚未使用的固定 attempt；不修改 prompt、成功判据或模型参数迎合已经观察到的输出。
- **供应商或网络失败：** 记为 `INVALID_INFRA`；是否继续只依据剩余授权、安全和费用边界，不把它算作功能失败或成功。
- **三次后仍未支持：** 停止重跑，如实报告 `MIXED`、`NOT_SUPPORTED` 或 `INCONCLUSIVE`。这不删除 567 项离线回归证据，但 O1b 不能作为真实 Session 稳定性正证据。

## 6. 报告与文件口径

O1a 原始文件继续为 `docs/evidence/e4-session-probe.json`。O1b 仅新增以下固定文件，不复用旧路径：

- `docs/evidence/o1b-session-probe-01.reservation.json`
- `docs/evidence/o1b-session-probe-02.reservation.json`
- `docs/evidence/o1b-session-probe-03.reservation.json`
- `docs/evidence/o1b-session-probe-01.json`
- `docs/evidence/o1b-session-probe-02.json`
- `docs/evidence/o1b-session-probe-03.json`
- 三次完成或停止后生成的 `docs/evidence/o1b-session-probe-summary.json`

O1b 使用独立 schema v2，不同时保留两套结果字段。单次报告中的 `outcome` 就是主要 Session 结果，枚举严格为 `PASS`、`FAIL`、`INVALID_INFRA`；`failure_code` 当且仅当 `PASS` 时为 null。汇总中的 valid 由 `outcome in {PASS, FAIL}` 唯一推导。

单次报告在安全 schema 上记录：`protocol_version`、`attempt_index`、`production_tree_sha256`、`evaluator_protocol_sha256`、非敏感配置快照、`outcome`、`failure_code`、`turn1_ideal_trace`、`turn2_ideal_trace`、`ideal_trace_overall`、`turn2_exact_value_observed`、`baseline_direct_subprocess_count` 与 `new_residual_direct_subprocess_count`。某回合的 `AgentSession.run()` 返回后才为该回合填写 ideal-trace 布尔值；未返回或未执行时为 null。`ideal_trace_overall` 仅在两个回合字段均非 null 时取二者逻辑与，否则为 null。`turn2_exact_value_observed` 仅在第二回合返回 `RunResult` 后填写；算法固定为仅对 `RunResult.final_text` 执行未经 Unicode、空白、引号或标点规范化的原始子串检查 `"你好，小明！" in final_text`，未返回或未执行时为 null。任何回答正文均不写入 reservation、结果或汇总。

`production_tree_sha256` 由排序后的 `code_operator/**/*.py` 与 `requirements.txt` 相对路径及文件 SHA-256 计算。`evaluator_protocol_sha256` 覆盖 runner 在 `evals/` 下直接或间接执行的全部本地模块，并额外覆盖 fixture、prompt 和本设计；当前冻结闭包明确为 `evals/run_session_probe.py`、`evals/run_golden.py`、`evals/session_probe/project/**`、`evals/session_probe/turn1.txt`、`evals/session_probe/turn2.txt` 与本文件。哈希输入同时包含排序后的仓库相对路径和各文件 SHA-256，禁止运行期间动态扩张或缩减清单；如果实现新增另一个本地 Eval 依赖，必须先把它加入冻结闭包并重新进入新协议队列。

非敏感配置固定记录并逐次比较：base URL `https://api.moonshot.cn/v1`、模型 `kimi-k3`、context window、max output tokens、max model rounds、max tool calls、HTTP connect/read/write/pool timeout、测试命令、测试超时、`ask_all`、自动审批策略、Python 实现/版本与平台，以及实际 `httpx`、`pytest` 版本。API Key 既不保存也不参与哈希。任一生产或 evaluator/protocol 哈希、配置字段在队列中变化都停止 O1b；生产变化进入 O1c，评测协议变化进入新的协议队列。

汇总校验每个 reservation 与对应结果的 attempt、协议版本、两个代码哈希、fixture/prompt/目标哈希和配置完全一致，并保存每个输入文件的 SHA-256 后生成计数；不读取或恢复任何原始模型内容。

CLI 仍提供独立 fixture 校验。真实模式必须显式给出 `--attempt {1,2,3}` 和对应固定 reservation/报告路径；索引、路径、前序 reservation 连续性或已有文件不匹配时在联网前失败关闭。汇总模式必须显式给出固定停止原因，并可在少于三个 attempt 时生成如实的提前停止汇总。O1b 不修改 `code_operator/` 生产接口，不增加 debug API，只修改 Eval、测试、研究计划和审核记录。

## 7. TDD 与审核门禁

实施按以下顺序进行：

1. 为双层判据写失败测试，确认额外只读工具不再阻断主要闭环，同时理想轨迹仍记为 false。
2. 为第二回合精确返回值布尔证据、禁止保存回答正文写失败测试。
3. 为进程基线差分、扫描失败关闭和不保存 PID/名称写失败测试。
4. 为固定 attempt 索引、联网前排他 reservation、结果排他写入、崩溃后编号不可复用、提前停止汇总和版本/配置一致性写失败测试。
5. 最小修改 Eval runner 与 schema；不得修改生产 AgentLoop、Session、协议、工具、安全策略或上下文管理。
6. 依次运行 O1b 专项、Session/Loop/CLI 组合、全量离线测试、编译、预检、差异格式与凭据/临时文件扫描。
7. 完成规格复核和质量复核，向项目作者展示完整外发数据、预计次数与费用风险。
8. 只有取得新的明确 Moonshot 数据出境授权后，才执行最多三个固定 attempt；原始 JSON 一经生成不得改写。
9. 结果完成独立分类与项目作者人工审核后才允许本地提交；远端 push 仍单独执行 v4.5 第 9.5 节。

## 8. 已知有效性威胁

- 该 fixture 很小，只验证一种两回合编辑场景，不代表一般 coding-agent 能力。
- 三次样本只能显示当前时间、当前 `kimi-k3` 服务和固定 prompt 下的观测，不是总体成功率。
- 将理想轨迹从主要判据分离是观察 O1a 后作出的构念修订，因此必须明确标为 O1b，不能宣称事前预注册。
- 最终回答中的精确值只做布尔匹配，可能漏掉语义等价但文本不同的答案；冻结值是中文常量，保守的精确检查可复现但不衡量回答质量。
- PID 集合差分可能受极短时进程退出或 PID 复用影响；它是结束时残留检查，不是全生命周期进程追踪，也不能排除评测器或外层控制器在基线后新建进程造成误归因。
- 真实 API 输出具有随机性；固定三个 attempt 和禁止追加第四次只能限制选择性报告，不能消除模型波动。
