# E4 Real Session Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一次隔离、可复现、可脱敏的真实 `kimi-k3` 连续会话探针验证 E4 的两回合上下文、文件 Undo、Session Reset 和资源关闭边界，并保持该结果与正式横评统计完全分离。

**Architecture:** 新增以公开 `AgentSession` 接口执行会话动作的 Eval runner。离线测试用 `FakeModelClient` 驱动完全相同的状态机；真实模式才创建 `ModelClient`。为验证研究设计已经批准、但公开接口无法完整观察的 Reset/Close 不变量，runner 还使用下述受限研究仪器边界。runner 只保存哈希、计数、状态与脱敏错误分类，不保存 prompt 正文、模型回答、工具 payload、临时绝对路径或认证信息。

**Tech Stack:** Python 3.11、pytest、标准库 `argparse/hashlib/json/pathlib/shutil/tempfile/time`、现有 `AgentSession`、`ProviderConfig`、`FakeModelClient`、`run_process`、`fixture_hash`、`write_report_exclusive`。

### 2026-09-01 O1 研究仪器勘误（项目作者已批准）

本勘误修正原计划“只依赖公开接口”与研究设计 H0 的 Reset/Close 完整观测要求之间的冲突，优先级高于本文件后续旧表述：

- 所有会话动作仍只能通过 `AgentSession.run()`、`undo()`、`reset()` 和 `close()` 等公开接口执行；不得从 Eval runner 直接修改 Session、Loop、FileTools 或 Journal 状态。
- Eval runner 可以只读访问当前实现的 `_loop._messages`、`_file_tools._complete_read_hashes`、`_journal` 和 `_pending_events`，且只能记录元素数量，用于断言 Reset 后历史只剩 system、完整读取哈希、Journal 和待通知事件均已清空。不得保存、打印或写入消息正文、文件路径、哈希映射内容或事件正文。
- 真实模式可以在 `AgentSession` 成功构造后包装其 `_owned_client.close`，包装器只能递增本地计数并委托原方法，不得改变请求、重试、超时、认证或关闭语义。Session 构造尚未返回时，该观测明确记为 unavailable；不得据此虚构关闭次数。
- 上述私有观测只属于与生产代码分离的研究测量仪器，不是稳定公共 API。内部结构缺失、类型不符或读取失败必须 fail-closed 为 `INVALID_INFRA`，不得修改生产代码以增加专用于本实验的 debug/history accessor。
- runner 还以标准库只读扫描当前进程的活动子进程；扫描失败为 `INVALID_INFRA`，非零结果为 `FAIL`。报告只记录计数，不记录 PID、命令行或路径。
- `ProbeReport` 因此新增四个安全标量：`reset_history_message_count`、`reset_read_hash_count`、`owned_client_close_calls`、`active_subprocess_count`。这些字段与原 schema 一同接受脱敏、固定路径和排他写入门禁。
- 此勘误不授权联网、真实 API 调用、报告生成、生产代码修改、提交或推送；这些动作仍分别受 Task 4 数据出境门禁、人工审核和 v4.5 第 9.5 节约束。

---

## Scope and fixed probe contract

**Create:**

- `evals/session_probe/__init__.py`
- `evals/session_probe/project/greeting.py`
- `evals/session_probe/project/tests/test_greeting.py`
- `evals/session_probe/turn1.txt`
- `evals/session_probe/turn2.txt`
- `evals/run_session_probe.py`
- `tests/test_session_probe.py`

**Generated only after an authorized real run:**

- `docs/evidence/e4-session-probe.json`

Exact fixture:

```python
# evals/session_probe/project/greeting.py
def greeting(name: str) -> str:
    return f"Hello, {name}!"
```

```python
# evals/session_probe/project/tests/test_greeting.py
from greeting import greeting


def test_greeting_uses_chinese_salutation() -> None:
    assert greeting("小明") == "你好，小明！"
```

Exact prompts:

```text
# turn1.txt
请修复 greeting.py，使现有测试通过。只允许修改 greeting.py；先读取文件，再进行一次直接编辑，并运行现有测试确认结果。

# turn2.txt
基于刚才完成的修改，说明现在 greeting("小明") 的精确返回值，并运行同一个测试再次确认；不要修改任何文件。
```

The fixture intentionally begins with one failing test. The runner must reject any change outside `greeting.py` and must never copy this repository's source into the probe workspace.

## Task 1: Freeze the fixture and report schema

**Files:** Create the fixture files above; create `tests/test_session_probe.py`; create `evals/run_session_probe.py`.

- [x] **Step 1: Write the RED tests for fixture invariants**

Add tests that import these exact public names:

```python
from evals.run_session_probe import (
    ALLOWED_CHANGED_PATHS,
    PROBE_ID,
    ProbeReport,
    validate_fixture,
)


def test_probe_identity_and_scope_are_frozen() -> None:
    assert PROBE_ID == "e4-session-probe-2026-09-01"
    assert ALLOWED_CHANGED_PATHS == frozenset({"greeting.py"})


def test_validate_fixture_requires_initial_red_and_exact_prompt_files() -> None:
    summary = validate_fixture()
    assert summary.initial_test_returncode == 1
    assert summary.target_relative_path == "greeting.py"
    assert len(summary.fixture_sha256) == 64
    assert len(summary.prompt_sha256) == 64


def test_probe_report_contains_no_raw_prompt_or_absolute_workspace() -> None:
    report = ProbeReport.invalid_infra("offline", "AUTH_NOT_RUN").to_dict()
    serialized = json.dumps(report, ensure_ascii=False)
    assert "workspace" not in report
    assert "prompt" not in report
    assert str(Path.cwd()) not in serialized
```

- [x] **Step 2: Confirm RED**

Run:

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: collection fails because `evals.run_session_probe` does not exist.

- [x] **Step 3: Add the minimal immutable schema and fixture validation**

Implement these concrete types in `evals/run_session_probe.py`:

```python
PROBE_ID = "e4-session-probe-2026-09-01"
ALLOWED_CHANGED_PATHS = frozenset({"greeting.py"})


@dataclass(frozen=True)
class FixtureSummary:
    fixture_sha256: str
    prompt_sha256: str
    initial_test_returncode: int
    target_relative_path: str
    target_initial_sha256: str


@dataclass(frozen=True)
class ProbeReport:
    schema_version: int
    probe_id: str
    mode: str
    outcome: str
    failure_code: str | None
    fixture_sha256: str | None
    prompt_sha256: str | None
    target_initial_sha256: str | None
    target_after_turn1_sha256: str | None
    target_after_undo_sha256: str | None
    turn_statuses: tuple[str, ...]
    model_rounds: tuple[int, ...]
    tool_calls: tuple[int, ...]
    provider_total_tokens: tuple[int | None, ...]
    changed_paths_after_turn1: tuple[str, ...]
    tests_after_turn1_returncode: int | None
    tests_after_turn2_returncode: int | None
    undo_ok: bool
    undo_depth_after: int
    reset_undo_depth: int
    reset_pending_events: int
    reset_history_message_count: int | None
    reset_read_hash_count: int | None
    close_idempotent: bool
    owned_client_close_calls: int | None
    active_subprocess_count: int | None
    session_artifact_count: int
    elapsed_seconds: float

    @classmethod
    def invalid_infra(cls, mode: str, failure_code: str) -> "ProbeReport":
        return cls(
            schema_version=1,
            probe_id=PROBE_ID,
            mode=mode,
            outcome="INVALID_INFRA",
            failure_code=failure_code,
            fixture_sha256=None,
            prompt_sha256=None,
            target_initial_sha256=None,
            target_after_turn1_sha256=None,
            target_after_undo_sha256=None,
            turn_statuses=(),
            model_rounds=(),
            tool_calls=(),
            provider_total_tokens=(),
            changed_paths_after_turn1=(),
            tests_after_turn1_returncode=None,
            tests_after_turn2_returncode=None,
            undo_ok=False,
            undo_depth_after=0,
            reset_undo_depth=0,
            reset_pending_events=0,
            reset_history_message_count=None,
            reset_read_hash_count=None,
            close_idempotent=False,
            owned_client_close_calls=None,
            active_subprocess_count=None,
            session_artifact_count=0,
            elapsed_seconds=0.0,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

`validate_fixture()` must run `[sys.executable, "-m", "pytest", "-q"]` on a temporary copy, require return code `1`, combine the two prompt hashes without returning prompt text, and reject runtime artifacts in the source fixture.

- [x] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: the schema/fixture tests pass.

**Observed evidence:** collection first failed with `ModuleNotFoundError` because the runner did not exist. After the minimal fixture/schema implementation and review-driven exact-tree, reparse-point and exception-redaction hardening, the focused Task 1 suite passed (`20 passed, 2 skipped` at that checkpoint).

## Task 2: Drive two turns, undo, reset, and close through public APIs

**Files:** Modify `tests/test_session_probe.py`; modify `evals/run_session_probe.py`.

- [x] **Step 1: Add the RED offline state-machine test**

Build a `FakeModelClient` sequence that performs exactly one `edit_file`, one test command, a completed first turn, one test command in the second turn, and a completed second turn. Assert:

```python
report = run_probe(
    fixture_root=FIXTURE_ROOT,
    turn1=TURN1_PATH.read_text(encoding="utf-8"),
    turn2=TURN2_PATH.read_text(encoding="utf-8"),
    config=fake_config(),
    client=fake_client,
    mode="offline",
)
assert report.outcome == "PASS"
assert report.turn_statuses == ("COMPLETED", "COMPLETED")
assert report.changed_paths_after_turn1 == ("greeting.py",)
assert report.tests_after_turn1_returncode == 0
assert report.tests_after_turn2_returncode == 0
assert report.target_after_undo_sha256 == report.target_initial_sha256
assert report.undo_ok is True
assert report.undo_depth_after == 0
assert report.reset_undo_depth == 0
assert report.reset_pending_events == 0
assert report.reset_history_message_count == 1
assert report.reset_read_hash_count == 0
assert report.close_idempotent is True
assert report.owned_client_close_calls in (None, 1)  # injected offline / owned real
assert report.active_subprocess_count == 0
assert report.session_artifact_count == 0
```

Also assert from `fake_client.calls` that the second model request contains both user turns and no orphan tool message. Do not add a production history/debug accessor only for this assertion. The author-approved research-instrument correction above permits only the separate count-only, fail-closed private observations needed after Reset and for owned-client close measurement.

- [x] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_session_probe.py::test_offline_probe_covers_two_turns_undo_reset_and_close -q
```

Expected: `run_probe` is missing.

- [x] **Step 3: Implement the minimal runner**

Use this exact flow:

```python
def run_probe(
    *,
    fixture_root: Path,
    turn1: str,
    turn2: str,
    config: ProviderConfig,
    client: ModelLike | None,
    mode: str,
) -> ProbeReport:
    with tempfile.TemporaryDirectory(prefix="code-operator-session-probe-") as raw:
        workspace = Path(raw) / "project"
        shutil.copytree(fixture_root, workspace)
        initial_hash = sha256_file(workspace / "greeting.py")
        with AgentSession(
            config,
            workspace=workspace,
            approve=lambda argv, cwd: approved_probe_command(argv, cwd, workspace),
            client=client,
            ask_all=True,
            auto_approve_tests=False,
        ) as session:
            first = session.run(turn1)
            # hash, changed-path and independent pytest checks
            second = session.run(turn2)
            undo = session.undo()
            after_undo_hash = sha256_file(workspace / "greeting.py")
            session.reset()
            # capture public depth/event properties plus the authorized,
            # count-only private Reset observations; fail closed if unavailable
            session.close()
            session.close()
        # scan only names/counts for forbidden session artifacts
```

Capture `elapsed_seconds` with `time.monotonic()`. Changed paths are determined by comparing a pre-run file-hash map to post-run hashes, excluding `.git`, `.code-operator`, `.pytest_cache`, and `__pycache__`. The first turn's complete successful tool trace must be exactly `read_file -> edit_file -> run_command`; the second turn's complete successful trace must be exactly one `run_command`. Treat any extra tool, non-allowed change, failed independent test, non-`COMPLETED` turn, undo mismatch, Reset observation mismatch, owned-client close count other than one in real mode, active subprocess, or artifact as `FAIL`; infrastructure and observation exceptions become explicit stable codes, never traceback or PID/path dumps in the report. Every Session-successfully-constructed exit path must finish idempotent close and subprocess observation before freezing its immutable report.

- [x] **Step 4: Confirm GREEN and existing E4 invariants**

```powershell
python -m pytest tests/test_session_probe.py tests/test_session.py tests/test_loop.py tests/test_cli.py -q
```

Expected: all pass.

**Observed evidence:** the initial state-machine test failed because `run_probe` was missing. Review-driven RED cases then exposed blanket approval, same-content writes, runtime entries, broken NTFS junctions, extra tools, `write_file` substitution, incomplete Toolhelp enumeration and lifecycle fields frozen before close. Each received a failing regression before the minimal repair. The final combined Session/Loop/CLI verification passed (`218 passed, 5 skipped`); the O1-only suite passed (`96 passed, 5 skipped`).

## Task 3: Add a safe CLI and exclusive evidence writing

**Files:** Modify `tests/test_session_probe.py`; modify `evals/run_session_probe.py`.

- [x] **Step 1: Write RED CLI tests**

Cover all of these cases:

```text
--validate-fixture performs no model call and emits one JSON summary
--real requires --report
--real refuses to overwrite an existing report
--real uses load_provider_config() and never accepts --api-key
--report must be inside docs/evidence when it is repository-managed
serialization rejects the current API key, Bearer tokens and private-key headers
```

- [x] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: parser/exclusive-write assertions fail.

- [x] **Step 3: Implement the minimal CLI**

Expose:

```text
python -m evals.run_session_probe --validate-fixture
python -m evals.run_session_probe --real --report docs/evidence/e4-session-probe.json
```

Use `load_provider_config()` only in `--real`; pass `client=None` so `AgentSession` owns the real client; validate and redact the report through `write_probe_report_exclusive()`, which delegates the final atomic exclusive creation to the shared `write_report_exclusive()`. The console may print only `outcome`, `failure_code`, counters and report path relative to the repository. It must not print prompts, model final text, stdout/stderr, Key, absolute temporary paths, PID or request headers.

- [x] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_session_probe.py -q
python -m evals.run_session_probe --validate-fixture
```

Expected: tests pass; fixture validation reports initial return code `1` and no network call.

**Observed evidence:** CLI/schema/path tests first failed because `build_parser`、`main`、fixed-path resolution and the guarded writer were absent. Review-driven RED cases closed argparse secret echo, broken-junction preflight, report-directory replacement, unsafe cleanup, validate/runtime exception leakage and UNC/device-path omissions. Final O1 tests passed (`96 passed, 5 skipped`), and `--validate-fixture` emitted one safe JSON line with `outcome=VALID` and `initial_test_returncode=1` without loading provider configuration or making a model call.

## Task 4: Review the exact outbound scope, then run one real probe

**Files:** Generate `docs/evidence/e4-session-probe.json`; update `REVIEW_LOG.md` only after results are reviewed.

- [x] **Step 1: Show the preflight package without making a request**

Display to the project author:

- the exact contents of `greeting.py`, its one test, `turn1.txt`, and `turn2.txt`;
- provider host `api.moonshot.cn`, model `kimi-k3`, maximum model rounds/tool calls and 15-minute outer timeout;
- explicit statement that provider requests may contain code-operator's system prompt/tool schemas, the two synthetic user prompts, synthetic file/test contents returned by tools, synthetic diff/test output, and prior assistant/tool messages needed for the second turn; no user project file is copied into the workspace;
- report schema and non-retention boundaries;
- current Git status and credential/artifact scan counts.

**Observed preflight (2026-09-01):** the project author was shown the exact two fixture files and two prompts, configured host/model and budgets, 15-minute outer timeout, complete possible Moonshot request scope, report/non-retention boundary, Git status and scan counts. This step made no provider request and did not create the evidence report. Step 2 remains incomplete until the author gives a fresh authorization covering that complete scope.

- [x] **Step 2: Stop for a fresh, exact data-transfer authorization**

Required user authorization must clearly allow the complete synthetic request scope above—not just the two task strings—to be sent to Moonshot. `RESEARCH-DESIGN-002`, earlier API permissions, and any push permission do not satisfy this gate. The local report does not retain full prompts/responses, but this does not make a claim about Moonshot's own service-side processing or retention.

**Authorization evidence (2026-09-01):** after receiving the complete Step 1 preflight, the project author replied exactly `允许将上述完整合成数据发送到 Moonshot API，并运行一次 kimi-k3 O1 真实 Session 探针`. This authorizes one run only under the displayed scope and does not authorize a retry, commit or push.

- [x] **Step 3: Run exactly once after authorization**

```powershell
python -m evals.run_session_probe --real --report docs/evidence/e4-session-probe.json
```

Do not retry a model-quality failure. If the report is `INVALID_INFRA`, investigate for at most 60 minutes without weakening the task; any rerun needs its own justification and data authorization.

**Observed single run (2026-09-01):** the report was created at `2026-09-01 18:14:36 +08:00`, after the preregistered 18:00 unconditional research freeze. The project author's later exact outbound-data authorization is treated as a one-run O1 exception, not as a retroactive claim that O1 met its original schedule and not as authorization for ablation, Pilot, formal comparison or any retry after the freeze. This is therefore an evidence-only observation with a disclosed time-gate deviation.

The one authorized `kimi-k3` request sequence ran for 37.984 seconds and created the report exactly once. The raw report is `FAIL / ACTIVE_SUBPROCESS`: turn 1 reached `COMPLETED` after 5 model rounds and 5 tool calls, provider usage was 6,908 tokens, owned-client close count was 1, and the process scan reported one child. No second provider run was started. Offline root-cause isolation then showed direct launch count `0`, while the 15-minute hidden `Start-Process` wrapper count was reproducibly `1`; a Toolhelp name-only diagnostic identified that wrapper-owned child as `conhost.exe`. Independently, 5 first-turn tool calls cannot satisfy the frozen exact three-event trace, so this sample remains a model-behavior failure even after recognizing the outer-wrapper confounder. Preserve the raw report unchanged and do not treat its `ACTIVE_SUBPROCESS` code as evidence that code-operator leaked an Agent tool process.

- [x] **Step 4: Verify and classify**

```powershell
python -m pytest tests/test_session_probe.py tests/test_session.py tests/test_loop.py tests/test_cli.py -q
python -m pytest -q
python -m compileall -q code_operator evals tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
```

Check the JSON parses, hashes are 64 hex characters, no prompt text or absolute path is stored, and exact current Key/Bearer/private-key/local-user-path counts are zero.

**Observed verification (2026-09-01):** focused Session/Loop/CLI tests passed (`218 passed, 5 skipped`); the full suite passed (`567 passed, 5 skipped`); submission preflight passed (`22 passed`); compileall and `git diff --check` exited 0; the fixture remained `VALID` with initial test return code 1. The 1,088-byte report parsed as JSON, all three non-null hashes were 64 lowercase hex characters, and exact current Key, Bearer, Authorization, private-key header, frozen prompt and local absolute-path matches were all zero. Report temp files, diagnostic temp files and Session fixture caches were zero. Independent result review approved the dual classification `FAIL_MODEL_BEHAVIOR_WITH_OUTER_WRAPPER_CONFOUNDER`; raw report SHA-256 is `D8AE19956B3CF3A624F31D1DFC4A6AAA653110CCE7FDFF5AF9D5D2B0D09E75F0`.

## Task 5: Production-defect branch or evidence-only checkpoint

- [x] Production-defect decision: not selected. The observed five-call model trace is a sample-level behavior failure, while the one-child result was reproduced as the outer hidden wrapper's `conhost.exe`; neither is evidence of a reproducible production protocol, Session, Undo or resource-leak defect. No production code was modified.
- [x] Evidence-only checkpoint selected: prepare only fixture, runner, tests, the unchanged raw evidence, approved implementation-plan correction, root-plan status, and a pending `REVIEW_LOG.md` entry for `O1-001` human review.
- [ ] After `O1-001` approval, create local commit `test(research): add real session probe evidence`.
- [ ] Do not push. Present the complete v4.5 section 9.5 gate and obtain a new exact push authorization first.

**Human review status (2026-09-01):** `O1-001` remains pending project-author review. The O1b offline verification and evidence-only candidate do not approve O1-001, do not mark the full O1 success condition complete, and do not authorize a provider retry, later research, commit or push.

## Completion evidence

This plan is complete only when the offline runner tests are green and the real probe has either one valid `PASS`/`FAIL` record or a truthful `INVALID_INFRA` record. A single `PASS` supports integration feasibility only; it is not a reliability rate and is never added to Track A/B totals.
