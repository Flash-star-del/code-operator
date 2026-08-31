# E4 Real Session Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一次隔离、可复现、可脱敏的真实 `kimi-k3` 连续会话探针验证 E4 的两回合上下文、文件 Undo、Session Reset 和资源关闭边界，并保持该结果与正式横评统计完全分离。

**Architecture:** 新增只依赖公开 `AgentSession` 接口的 Eval runner。离线测试用 `FakeModelClient` 驱动完全相同的状态机；真实模式才创建 `ModelClient`。runner 只保存哈希、计数、状态与脱敏错误分类，不保存 prompt 正文、模型回答、工具 payload、临时绝对路径或认证信息。

**Tech Stack:** Python 3.11、pytest、标准库 `argparse/hashlib/json/pathlib/shutil/tempfile/time`、现有 `AgentSession`、`ProviderConfig`、`FakeModelClient`、`run_process`、`fixture_hash`、`write_report_exclusive`。

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

- [ ] **Step 1: Write the RED tests for fixture invariants**

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

- [ ] **Step 2: Confirm RED**

Run:

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: collection fails because `evals.run_session_probe` does not exist.

- [ ] **Step 3: Add the minimal immutable schema and fixture validation**

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
    close_idempotent: bool
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
            close_idempotent=False,
            session_artifact_count=0,
            elapsed_seconds=0.0,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

`validate_fixture()` must run `[sys.executable, "-m", "pytest", "-q"]` on a temporary copy, require return code `1`, combine the two prompt hashes without returning prompt text, and reject runtime artifacts in the source fixture.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: the schema/fixture tests pass.

## Task 2: Drive two turns, undo, reset, and close through public APIs

**Files:** Modify `tests/test_session_probe.py`; modify `evals/run_session_probe.py`.

- [ ] **Step 1: Add the RED offline state-machine test**

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
assert report.close_idempotent is True
assert report.session_artifact_count == 0
```

Also assert from `fake_client.calls` that the second model request contains both user turns and no orphan tool message. Do not add a production history/debug accessor only for this assertion.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_session_probe.py::test_offline_probe_covers_two_turns_undo_reset_and_close -q
```

Expected: `run_probe` is missing.

- [ ] **Step 3: Implement the minimal runner**

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
            approve=lambda _request: True,
            client=client,
            auto_approve_tests=True,
        ) as session:
            first = session.run(turn1)
            # hash, changed-path and independent pytest checks
            second = session.run(turn2)
            undo = session.undo()
            after_undo_hash = sha256_file(workspace / "greeting.py")
            session.reset()
            # capture public depth/event properties
            session.close()
            session.close()
        # scan only names/counts for forbidden session artifacts
```

Capture `elapsed_seconds` with `time.monotonic()`. Changed paths are determined by comparing a pre-run file-hash map to post-run hashes, excluding `.git`, `.code-operator`, `.pytest_cache`, and `__pycache__`. Treat any non-allowed change, failed independent test, non-`COMPLETED` turn, undo mismatch, or artifact as `FAIL`; infrastructure exceptions become an explicit code such as `MODEL_UNAVAILABLE`, `AUTH_FAILURE`, or `PROCESS_FAILURE`, never a traceback dump in the report.

- [ ] **Step 4: Confirm GREEN and existing E4 invariants**

```powershell
python -m pytest tests/test_session_probe.py tests/test_session.py tests/test_loop.py tests/test_cli.py -q
```

Expected: all pass.

## Task 3: Add a safe CLI and exclusive evidence writing

**Files:** Modify `tests/test_session_probe.py`; modify `evals/run_session_probe.py`.

- [ ] **Step 1: Write RED CLI tests**

Cover all of these cases:

```text
--validate-fixture performs no model call and emits one JSON summary
--real requires --report
--real refuses to overwrite an existing report
--real uses load_provider_config() and never accepts --api-key
--report must be inside docs/evidence when it is repository-managed
serialization rejects the current API key, Bearer tokens and private-key headers
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_session_probe.py -q
```

Expected: parser/exclusive-write assertions fail.

- [ ] **Step 3: Implement the minimal CLI**

Expose:

```text
python -m evals.run_session_probe --validate-fixture
python -m evals.run_session_probe --real --report docs/evidence/e4-session-probe.json
```

Use `load_provider_config()` only in `--real`; pass `client=None` so `AgentSession` owns the real client; call `write_report_exclusive(args.report, report.to_dict(), api_key=config.api_key)`. The console may print only `outcome`, `failure_code`, counters and report path relative to the repository. It must not print prompts, model final text, stdout/stderr, Key, absolute temporary paths, or request headers.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_session_probe.py -q
python -m evals.run_session_probe --validate-fixture
```

Expected: tests pass; fixture validation reports initial return code `1` and no network call.

## Task 4: Review the exact outbound scope, then run one real probe

**Files:** Generate `docs/evidence/e4-session-probe.json`; update `REVIEW_LOG.md` only after results are reviewed.

- [ ] **Step 1: Show the preflight package without making a request**

Display to the project author:

- the exact contents of `greeting.py`, its one test, `turn1.txt`, and `turn2.txt`;
- provider host `api.moonshot.cn`, model `kimi-k3`, maximum model rounds/tool calls and 15-minute outer timeout;
- explicit statement that provider requests may contain code-operator's system prompt/tool schemas, the two synthetic user prompts, synthetic file/test contents returned by tools, synthetic diff/test output, and prior assistant/tool messages needed for the second turn; no user project file is copied into the workspace;
- report schema and non-retention boundaries;
- current Git status and credential/artifact scan counts.

- [ ] **Step 2: Stop for a fresh, exact data-transfer authorization**

Required user authorization must clearly allow the complete synthetic request scope above—not just the two task strings—to be sent to Moonshot. `RESEARCH-DESIGN-002`, earlier API permissions, and any push permission do not satisfy this gate. The local report does not retain full prompts/responses, but this does not make a claim about Moonshot's own service-side processing or retention.

- [ ] **Step 3: Run exactly once after authorization**

```powershell
python -m evals.run_session_probe --real --report docs/evidence/e4-session-probe.json
```

Do not retry a model-quality failure. If the report is `INVALID_INFRA`, investigate for at most 60 minutes without weakening the task; any rerun needs its own justification and data authorization.

- [ ] **Step 4: Verify and classify**

```powershell
python -m pytest tests/test_session_probe.py tests/test_session.py tests/test_loop.py tests/test_cli.py -q
python -m pytest -q
python -m compileall -q code_operator evals tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
```

Check the JSON parses, hashes are 64 hex characters, no prompt text or absolute path is stored, and exact current Key/Bearer/private-key/local-user-path counts are zero.

## Task 5: Production-defect branch or evidence-only checkpoint

- [ ] If the probe exposes a reproducible production defect, stop this plan. Create a separate design note and TDD fix: first reproduce with an offline failing test, then minimal production repair, full suite, human review, and a distinct semantic `fix` commit named after the verified root cause. Do not edit production code inside the probe evidence commit.
- [ ] If no reproducible production defect exists, prepare only fixture, runner, tests, evidence, root-plan status, and `REVIEW_LOG.md` for `O1-001` human review.
- [ ] After `O1-001` approval, create local commit `test(research): add real session probe evidence`.
- [ ] Do not push. Present the complete v4.5 section 9.5 gate and obtain a new exact push authorization first.

## Completion evidence

This plan is complete only when the offline runner tests are green and the real probe has either one valid `PASS`/`FAIL` record or a truthful `INVALID_INFRA` record. A single `PASS` supports integration feasibility only; it is not a reliability rate and is never added to Track A/B totals.
