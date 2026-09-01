# O1b Session Replication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Approved by the project author as `O1B-PLAN-001` on 2026-09-01.

**Goal:** 将已审核的 O1b 设计落实为可重复、不可选择性覆盖的真实 Session 评测器，在最多三个固定 attempt 中分别报告主要 Session 闭环和理想工具轨迹。

**Architecture:** 保留 O1a 原始 JSON 和现有生产代码不变，只扩展 `evals/run_session_probe.py`、其专项测试与研究证据。CLI 在任何真实客户端创建前排他写入 reservation；每次结果使用独立固定路径，汇总从 reservation 与可选结果重建。runner 用主要语义策略继续完成两回合，用独立布尔值记录理想轨迹，并以开始/结束直接子进程集合差分替代绝对计数。

**Tech Stack:** Python 3.11、标准库、现有 httpx/pytest、现有 `AgentSession`/Eval 安全写入原语；不新增依赖，不修改 `code_operator/`。

---

### Task 0: 固定基线与文件边界

**Files:**
- Modify: `evals/run_session_probe.py`
- Modify: `tests/test_session_probe.py`
- Preserve byte-for-byte: `docs/evidence/e4-session-probe.json`
- Modify after implementation review: `docs/superpowers/plans/2026-09-01-e4-session-probe-implementation.md`
- Modify after implementation review: `REVIEW_LOG.md`
- Modify outside repository after implementation review: `../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md`
- Create only after explicit outbound authorization: `docs/evidence/o1b-session-probe-{01,02,03}.reservation.json`
- Create only after corresponding real attempt returns: `docs/evidence/o1b-session-probe-{01,02,03}.json`
- Create only after three attempts or an authorized stop: `docs/evidence/o1b-session-probe-summary.json`

实施期间禁止修改 fixture 和两段 prompt；它们继续使用：

```text
evals/session_probe/project/greeting.py
evals/session_probe/project/tests/test_greeting.py
evals/session_probe/turn1.txt
evals/session_probe/turn2.txt
```

每个任务只修改共享工作区中列出的文件。不得暂存、提交或改写 O1a 原始报告；每个任务完成规格复核和质量复核后才勾选。

- [ ] **Step 1: 记录 Git 与 O1a 原始证据基线**

Run:

```powershell
git status --short --branch
git log -4 --oneline
Get-FileHash -Algorithm SHA256 docs/evidence/e4-session-probe.json
```

Expected: 分支包含已审核设计提交 `259cf67`；O1a 报告哈希严格为 `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`；现有未提交 O1a 文件均被识别并保留。

- [ ] **Step 2: 运行 O1a 专项基线**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py
```

Expected: `96 passed, 5 skipped`；5 项仅为当前 Windows 权限不支持的符号链接测试，NTFS junction 测试实际运行。若基线不同，先查明原因，不进入 Task 1。

- [ ] **Step 3: 确认尚无 O1b 真实证据文件**

Run:

```powershell
Get-ChildItem docs/evidence/o1b-session-probe-* -ErrorAction SilentlyContinue
```

Expected: 无输出；离线实现阶段不得创建 reservation、结果或 summary。

### Task 1: 冻结 O1b 元数据、版本哈希和 reservation

**Files:**
- Modify: `tests/test_session_probe.py`
- Modify: `evals/run_session_probe.py`

- [ ] **Step 1: 写 reservation 与冻结元数据失败测试**

在 `tests/test_session_probe.py` 新增覆盖以下行为的独立测试：

```python
def test_o1b_reservation_is_created_exclusively_before_run_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StopAfterRunProbe(RuntimeError):
        pass

    events: list[str] = []
    reservation = tmp_path / "o1b-session-probe-01.reservation.json"

    def stop_after_reservation(**_kwargs: object) -> None:
        assert reservation.is_file()
        events.append("run_probe")
        raise StopAfterRunProbe

    monkeypatch.setattr(run_session_probe, "run_probe", stop_after_reservation)

    with pytest.raises(StopAfterRunProbe):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            reservation_path=reservation,
            report_path=tmp_path / "o1b-session-probe-01.json",
            config=_probe_config(),
        )

    assert reservation.is_file()
    assert events == ["run_probe"]
    payload = json.loads(reservation.read_text(encoding="utf-8"))
    assert payload["attempt_index"] == 1
    assert "api_key" not in json.dumps(payload).lower()
```

测试文件的显式导入列表同时加入 `AttemptReservation`、`FrozenProbeMetadata` 与 `O1B_PROTOCOL_VERSION`；不得通过星号导入隐藏 schema 漂移。

再新增并分别断言：

- 已有 reservation 时在 client factory 前失败，文件字节不变；
- attempt `02` 没有 `01` reservation 时失败；
- attempt、reservation 固定路径和 report 固定路径不匹配时失败；
- reservation 成功而结果缺失时编号仍不可复用；
- `production_tree_sha256` 对 `code_operator/**/*.py` 或 `requirements.txt` 任一字节变化敏感；
- `evaluator_protocol_sha256` 对 `evals/run_session_probe.py`、`evals/run_golden.py`、fixture、prompt 或设计文件任一字节变化敏感；
- 哈希输入包含排序后的仓库相对路径，因此同内容改名会改变哈希；
- 配置快照含 base URL、模型、四个 token/round/tool 限制、四个 HTTP timeout、测试命令/超时、审批策略、Python/平台、httpx/pytest 版本，但不含 Key。

- [ ] **Step 2: 运行定向测试并确认 RED**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py -k "reservation or production_tree or evaluator_protocol or config_snapshot"
```

Expected: FAIL，因为 reservation dataclass、冻结哈希、配置快照或 `reserve_then_run_real_attempt()` 尚不存在；失败不得来自拼写或 fixture 损坏。

- [ ] **Step 3: 最小实现冻结元数据和 reservation**

在 `evals/run_session_probe.py` 增加聚焦的数据结构和纯函数：

```python
O1B_PROTOCOL_VERSION = "o1b-v1"
O1B_PLANNED_ATTEMPTS = 3
O1B_REPORT_PATHS = {
    index: Path("docs/evidence") / f"o1b-session-probe-{index:02d}.json"
    for index in range(1, O1B_PLANNED_ATTEMPTS + 1)
}
O1B_RESERVATION_PATHS = {
    index: path.with_suffix(".reservation.json")
    for index, path in O1B_REPORT_PATHS.items()
}

@dataclass(frozen=True)
class FrozenProbeMetadata:
    fixture_sha256: str
    prompt_sha256: str
    target_initial_sha256: str
    production_tree_sha256: str
    evaluator_protocol_sha256: str
    config: dict[str, object]

@dataclass(frozen=True)
class AttemptReservation:
    protocol_version: str
    attempt_index: int
    created_at: str
    metadata: FrozenProbeMetadata
```

实现 `_manifest_sha256(paths)`，对每个真实普通文件写入 `relative.as_posix() + "\0" + file_sha256 + "\n"` 后计算整体 SHA-256；拒绝链接、重解析点、重复路径、范围外路径和缺失文件。生产与 evaluator 清单严格按设计文件列举，fixture 递归项按相对路径排序且排除运行缓存。

配置快照从 `ProviderConfig`、冻结常量和已安装模块版本生成，不读取或复制 `config.api_key`。实现 `_write_json_exclusive_verified()` 复用现有路径身份、脱敏和排他写入门禁；reservation 写入并重新读取校验成功后，才调用 `run_probe(client=None, ...)`。真实客户端仍由 `AgentSession` 拥有和关闭，现有 owned-client close 计数不得丢失。`evidence_root` 只作为注入式单元测试根目录；CLI 永远传入仓库固定 `docs/evidence`。任何已有 reservation、编号空洞或固定路径不匹配均在 `run_probe()` 之前返回固定本地错误，因此不会创建真实客户端或发送请求。

- [ ] **Step 4: 运行定向测试并确认 GREEN**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py -k "reservation or production_tree or evaluator_protocol or config_snapshot"
```

Expected: PASS；测试确认进入会创建真实 `AgentSession` 的 `run_probe()` 严格晚于 reservation 落盘。

- [ ] **Step 5: 规格复核与质量复核**

规格复核必须检查：reservation 一经创建永久占号；缺失结果不会允许复用；哈希闭包覆盖 `evals/run_golden.py`；配置不含 Key。质量复核必须检查：没有把测试目录或运行缓存纳入不稳定哈希；所有安全失败均 fail-closed 且不回显路径/Key。

### Task 2: 分离主要 Session 判据与理想轨迹

**Files:**
- Modify: `tests/test_session_probe.py`
- Modify: `evals/run_session_probe.py`

- [ ] **Step 1: 写双层判据和 null 语义失败测试**

先增加统一运行 helper，避免后续测试重复设置进程快照；它必须调用真实 `run_probe()`：

```python
def _run_probe_with_client(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeModelClient,
) -> ProbeReport:
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: frozenset(),
        raising=False,
    )
    return run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=client,
        mode="offline",
        attempt_index=1,
        frozen_metadata=_offline_frozen_metadata(),
    )
```

测试 helper 使用固定 64 位合成哈希和不含 Key 的配置快照，只供测试构造 schema；生产路径必须调用真实哈希函数：

```python
def _offline_frozen_metadata() -> FrozenProbeMetadata:
    return FrozenProbeMetadata(
        fixture_sha256="a" * 64,
        prompt_sha256="b" * 64,
        target_initial_sha256="c" * 64,
        production_tree_sha256="d" * 64,
        evaluator_protocol_sha256="e" * 64,
        config={
            "base_url": "https://probe.invalid/v1",
            "model": "synthetic-probe-model",
            "context_window": 32_000,
            "max_output_tokens": 8_000,
            "max_model_rounds": 16,
            "max_tool_calls": 32,
            "http_timeout_seconds": {
                "connect": 10.0, "read": 60.0, "write": 30.0, "pool": 10.0,
            },
            "test_command": ["python", "-m", "pytest", "-q"],
            "test_timeout_seconds": 60,
            "ask_all": True,
            "auto_approve_tests": False,
            "python_implementation": "CPython",
            "python_version": "3.11.0",
            "platform": "test-platform",
            "httpx_version": "test-httpx",
            "pytest_version": "test-pytest",
        },
    )

def _offline_reservation(*, attempt_index: int) -> AttemptReservation:
    return AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=attempt_index,
        created_at="2026-09-01T00:00:00+08:00",
        metadata=_offline_frozen_metadata(),
    )
```

保留现有严格三事件离线 PASS 测试，再增加一个含额外只读事件的两回合假模型测试：

```python
def test_extra_successful_reads_preserve_primary_pass_but_miss_ideal_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeModelClient(
        [
            _turn(None, ToolCall("ls", "list_dir", '{"path":"."}')),
            _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
            _turn(None, ToolCall("grep", "grep", '{"query":"Hello","path":"greeting.py"}')),
            _turn(None, ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}')),
            _turn(None, ToolCall("test1", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("fixed"),
            _turn(None, ToolCall("reread", "read_file", '{"path":"greeting.py"}')),
            _turn(None, ToolCall("test2", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn('精确返回值是“你好，小明！”。'),
        ]
    )
    report = _run_probe_with_client(monkeypatch, client)

    assert report.outcome == "PASS"
    assert report.turn1_ideal_trace is False
    assert report.turn2_ideal_trace is False
    assert report.ideal_trace_overall is False
    assert report.turn2_exact_value_observed is True
```

分别新增以下测试：

- 第一回合没有 `read_file`、有两个 edit、使用 `write_file`、有两个 command、command 失败、编辑发生在读取前或测试发生在编辑前时主要结果 FAIL；
- 第二回合有任何写工具、多个/失败/非冻结 command 时主要结果 FAIL；
- 额外成功 `read_file/grep/list_dir` 允许继续；任何其他工具名或失败只读工具使主要结果 FAIL；
- 第二回合 `RunResult.final_text` 只用未经规范化的 Unicode 原始子串 `"你好，小明！" in final_text` 判断；缺标点、全角空格替换或工具 payload 中出现但 final text 不出现均为 false；
- 第二回合精确值为 false 时主要结果固定为 `FAIL / TURN2_EXACT_VALUE_MISSING`；现有 `_successful_probe_client()` 和严格 PASS 测试的第二回合最终文本同步改为包含 `你好，小明！`；
- 回合未执行或 `AgentSession.run()` 未返回时，对应 ideal 和精确值字段为 null，不写成 false；
- 报告序列化不含 final text、prompt、reasoning 或工具 payload。

- [ ] **Step 2: 运行定向测试并确认 RED**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py -k "ideal_trace or exact_value or primary_tool_policy or extra_successful_reads"
```

Expected: FAIL；当前 runner 会以 `INVALID_TOOL_SEQUENCE` 在第一回合提前返回。

- [ ] **Step 3: 最小实现双层判据**

保留现有 `_first_tool_sequence_is_valid()` 与 `_second_tool_sequence_is_valid()` 作为 ideal 计算，新增主要策略：

```python
_READ_ONLY_PROBE_TOOLS = frozenset({"read_file", "grep", "list_dir"})

def _first_primary_tool_policy_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    names = [event.name for event in events]
    if not events or not all(event.ok for event in events):
        return False
    if any(name not in _READ_ONLY_PROBE_TOOLS | {"edit_file", "run_command"} for name in names):
        return False
    if names.count("edit_file") != 1 or names.count("run_command") != 1:
        return False
    edit_index = names.index("edit_file")
    command_index = names.index("run_command")
    return "read_file" in names[:edit_index] and edit_index < command_index

def _second_primary_tool_policy_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    names = [event.name for event in events]
    return (
        bool(events)
        and all(event.ok for event in events)
        and all(name in _READ_ONLY_PROBE_TOOLS | {"run_command"} for name in names)
        and names.count("run_command") == 1
    )
```

把 strict sequence 的提前失败改为记录 nullable `turn1_ideal_trace`、`turn2_ideal_trace` 和 `ideal_trace_overall`。主要策略失败使用稳定的 `PRIMARY_TOOL_POLICY_FAILED`；其他文件、测试、Undo、Reset、Close 门禁保持原先顺序。第二回合返回后立即以 `"你好，小明！" in second.final_text` 计算布尔值，false 时返回 `TURN2_EXACT_VALUE_MISSING`，随后不再保留正文引用。

- [ ] **Step 4: 运行定向与现有 O1 测试并确认 GREEN**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py
```

Expected: 全部 PASS；现有严格脚本样本仍同时主要 PASS 与 ideal PASS，额外只读样本主要 PASS 但 ideal FAIL。

- [ ] **Step 5: 规格复核与质量复核**

规格复核确认主要判据仍拒绝额外写入/命令/失败工具和协议错误，不能为追求成功放宽安全边界。质量复核检查错误优先级确定、nullable 字段不把“未观察”写成失败、模型正文没有进入报告。

### Task 3: 用基线差分观测结束时直接子进程

**Files:**
- Modify: `tests/test_session_probe.py`
- Modify: `evals/run_session_probe.py`

- [ ] **Step 1: 写进程差分失败测试**

新增顺序快照测试：

```python
def test_preexisting_controller_child_is_not_counted_as_new_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset({41}), frozenset({41})))
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: next(snapshots),
        raising=False,
    )
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert report.baseline_direct_subprocess_count == 1
    assert report.new_residual_direct_subprocess_count == 0
```

另测：基线 `{41}`、结束 `{41, 42}` 得 residual 1 并使主要结果 FAIL；基线或结束扫描抛错均为 `INVALID_INFRA / SUBPROCESS_SCAN_FAILED`；报告 JSON 不含 `41`、`42`、PID、进程名或命令行；不支持平台 fail-closed。

- [ ] **Step 2: 运行定向测试并确认 RED**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py -k "controller_child or residual_direct_subprocess or subprocess_scan"
```

Expected: FAIL，因为当前实现只返回绝对计数 `active_subprocess_count`。

- [ ] **Step 3: 最小实现 PID 集合差分**

将 `_active_subprocess_count()` 改为 `_direct_subprocess_pids() -> frozenset[int]`。Windows Toolhelp 与 Linux `/proc` 继续枚举直接子进程，但只在函数内部保留 PID；调用方在 `AgentSession` 创建前保存 baseline，在两次 `close()` 后保存 final，计算：

```python
baseline_direct_subprocess_count = len(baseline)
new_residual_direct_subprocess_count = len(final - baseline)
```

只把两个计数写入报告。差分非零使用 `NEW_RESIDUAL_DIRECT_SUBPROCESS`，并在结果说明中保留“需要离线归因，不能直接称 Agent 泄漏”的限制；扫描失败优先归为 INVALID_INFRA。

- [ ] **Step 4: 运行 O1 专项并确认 GREEN**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py
```

Expected: 全部 PASS，且原先绝对计数字段不再出现在 O1b schema 测试中。

- [ ] **Step 5: 规格复核与质量复核**

复核 PID 不进入异常消息、stdout、reservation、结果或汇总；基线采集发生在控制器初始化后、Session 创建前；结束采集严格发生在 close 后。

### Task 4: 固定结果路径、提前停止汇总和 CLI 门禁

**Files:**
- Modify: `tests/test_session_probe.py`
- Modify: `evals/run_session_probe.py`

- [ ] **Step 1: 写 CLI 与汇总失败测试**

测试文件同时增加 `from dataclasses import asdict`，并复用 Task 2 定义的 `_offline_reservation()`。

新增以下测试：

```python
def test_summary_keeps_reserved_attempt_without_result_as_invalid_infra(
    tmp_path: Path,
) -> None:
    reservation = _offline_reservation(attempt_index=1)
    path = tmp_path / "o1b-session-probe-01.reservation.json"
    path.write_text(
        json.dumps(asdict(reservation), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    summary = run_session_probe.build_o1b_summary(
        evidence_root=tmp_path,
        stop_reason="PROVIDER_BOUNDARY_STOP",
    )

    assert summary.planned_attempts == 3
    assert summary.attempted_count == 1
    assert summary.valid_attempts == 0
    assert summary.invalid_infra_count == 1
    assert summary.unexecuted_attempts == (2, 3)
    assert summary.classification == "O1B_INCONCLUSIVE"
```

再测：

- 结果没有 reservation、reservation 编号有空洞、协议/代码哈希/配置不一致均拒绝汇总；
- 两个主要 PASS + 一个 INVALID 为 `O1B_SUPPORTED`，且不得序列化为“2/3 成功”；
- 一个 PASS + 一个 FAIL 为 `O1B_MIXED`；两个 FAIL 为 `O1B_NOT_SUPPORTED`；少于两个 valid 为 `O1B_INCONCLUSIVE`；
- ideal true 只计明确 true；null 不计通过，分母仍是 valid attempts；
- CLI `--real` 缺 attempt/reservation/report、路径不固定、报告已存在或 reservation 已存在时，在 client 创建前退出 2；
- CLI `--summarize` 缺合法 stop reason 或 summary 已存在时退出 2；
- stdout 只含计数、分类和仓库相对证据路径，不含 Key、prompt、回答或绝对路径。

- [ ] **Step 2: 运行定向测试并确认 RED**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py -k "summary or fixed_attempt or stop_reason or reserved_attempt"
```

Expected: FAIL，因为 summary schema、分类器与新 CLI 模式尚不存在。

- [ ] **Step 3: 最小实现汇总和 CLI**

新增冻结分类器：

```python
def _classify_o1b(*, valid_attempts: int, primary_passes: int) -> str:
    if valid_attempts < 2:
        return "O1B_INCONCLUSIVE"
    if primary_passes >= 2:
        return "O1B_SUPPORTED"
    if primary_passes == 1:
        return "O1B_MIXED"
    return "O1B_NOT_SUPPORTED"
```

`build_o1b_summary()` 从固定 evidence root 读取连续 reservation 和可选结果；缺结果仅在汇总中产生 `RESERVED_NO_RESULT` 行，不创建或补写单次报告。保存 `planned_attempts`、`attempted_count`、`valid_attempts`、`primary_passes`、`invalid_infra_count`、`ideal_trace_passes`、`unexecuted_attempts`、`stop_reason`、classification 和每个输入文件 SHA-256。

扩展 parser 的互斥模式：

```text
--validate-fixture
--real --attempt {1,2,3} --reservation <fixed-path> --report <fixed-path>
--summarize --stop-reason <fixed-enum> --summary docs/evidence/o1b-session-probe-summary.json
```

真实模式的顺序固定为：纯本地校验 → reservation 排他落盘与复读 → 创建真实 client/Session → 执行 → 结果排他落盘。汇总模式完全离线，不加载 API Key。

- [ ] **Step 4: 运行专项、CLI 与安全测试并确认 GREEN**

Run:

```powershell
python -m pytest -q tests/test_session_probe.py tests/test_cli.py tests/test_submission_preflight.py
```

Expected: 全部 PASS；测试期间不联网，不创建正式 evidence 文件。

- [ ] **Step 5: 规格复核与质量复核**

规格复核检查 fixed path、连续编号、reservation/结果对应、提前停止、null 与分类穷尽。质量复核检查 parser 错误不回显敏感 argv、JSON 写入抗链接/目录替换/覆盖竞态、summary 不信任手工编辑的输入。

### Task 5: 离线总验证、文档收敛与真实运行门禁

**Files:**
- Modify: `docs/superpowers/plans/2026-09-01-e4-session-probe-implementation.md`
- Modify: `REVIEW_LOG.md`
- Modify outside repository: `../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md`
- Preserve: `docs/evidence/e4-session-probe.json`

- [ ] **Step 1: 运行离线验证**

依次运行：

```powershell
python -m pytest -q tests/test_session_probe.py
python -m pytest -q tests/test_session.py tests/test_loop.py tests/test_cli.py tests/test_session_probe.py
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
python -m pytest -q tests/test_submission_preflight.py
git diff --check
```

Expected: 所有可运行测试通过；符号链接权限型 skip 必须逐项说明，不能笼统忽略；编译和差异检查退出 0。

- [ ] **Step 2: 执行安全和证据不变量检查**

确认：

- `docs/evidence/e4-session-probe.json` SHA-256 仍为 `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`；
- 当前 API Key 精确值在全部候选文件中命中 0；
- API Key/Bearer/Authorization/私钥通用模式命中 0；
- 尚未授权真实运行时，O1b reservation、结果和 summary 文件数量均为 0；
- 报告临时文件、诊断残留和 `evals/session_probe/__pycache__` 数量均为 0；
- 生产目录 `code_operator/` 相对基线无改动。

- [ ] **Step 3: 更新事实文档并完成最终独立复核**

只记录实际观察到的 RED/GREEN、测试计数、哈希和限制。`O1-001` 保持待审核；新增 O1b 实现审核候选，但不得提前写“人工审核通过”。根计划只开放已批准的 O1b 范围，不自动开放消融、横评或 Ubuntu。

最终评审必须分别给出：规格一致性、代码质量、安全/隐私、研究有效性与原始 O1a 不变性结论。存在阻塞或重要问题时回到对应实现任务修复并复核。

- [ ] **Step 4: 展示完整 outbound 范围并等待授权**

向项目作者逐字展示：fixture 两个文件、两段 prompt、可能发送的 system prompt/tool schema/合成工具结果/前序 assistant-tool 历史、endpoint/model、三个固定 attempt、每次上限、停止规则、不会发送的用户数据、报告字段和费用/随机性风险。

未收到覆盖具体数据和最多三次调用的明确授权前：

- 不创建 reservation；
- 不创建真实 client；
- 不发送任何 Moonshot 请求；
- 不运行 summary；
- 不暂存/提交实现候选。

- [ ] **Step 5: 真实运行后分类、人工审核和提交门禁**

只有在授权后才按 `01 -> 02 -> 03` 串行运行；每个 request 已占号后无论结果如何都不重用。触发设计中的停止条件时立刻停止，并用固定 stop reason 生成 summary。原始 reservation、结果与 summary 一经生成不得改写。

向项目作者展示 O1a + O1b 全部结果、原始文件哈希、分类、测试和扫描证据，取得新的人工审核后才允许本地语义化提交。任何 push 仍需另行展示 v4.5 第 9.5 节六类证据并获得当次明确 `允许推送`。
