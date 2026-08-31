# Three-System Agent Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个可复现、SWE-bench 风格但不冒充官方榜单的微基准，对 code-operator、Claude Code、Kimi Code 在三个冻结 Python 任务上各运行两次，统一评分并如实保留全部 18 个正式结果。

**Architecture:** 任务模板、隐藏评分器、系统适配器和运行结果四层分离。每次运行从同一模板创建全新 Git 工作区，适配器只执行冻结的 `argv`，进程树由现有跨平台终止器控制；Agent 结束后，工作区外的 grader 才复制隐藏测试并生成无原始会话内容的结构化结果。

**Tech Stack:** Python 3.11、pytest、Git、标准库 `argparse/dataclasses/hashlib/json/pathlib/random/shutil/subprocess/sys/tempfile/time`、现有 `evals.run_golden` 进程/哈希/报告工具。

---

## Fixed study constants and files

```python
STUDY_ID = "reliability-2026-09-01-preregistered"
TRACK = "A"
SEED = 20260901
SYSTEMS = ("code-operator", "claude-code", "kimi-code")
TASKS = ("T1", "T2", "T3")
REPLICATES = (1, 2)
TIMEOUT_SECONDS = 900
FORMAL_RUN_COUNT = 18
```

**Create:**

- `evals/agent_comparison/__init__.py`
- `evals/agent_comparison/schema.py`
- `evals/agent_comparison/manifest.py`
- `evals/agent_comparison/workspace.py`
- `evals/agent_comparison/adapters.py`
- `evals/agent_comparison/grader.py`
- `evals/agent_comparison/run_study.py`
- `evals/agent_comparison/tasks/T1/task.txt`
- `evals/agent_comparison/tasks/T1/project/ranges.py`
- `evals/agent_comparison/tasks/T1/project/tests/test_ranges.py`
- `evals/agent_comparison/tasks/T1/grader/test_hidden_ranges.py`
- `evals/agent_comparison/tasks/T2/task.txt`
- `evals/agent_comparison/tasks/T2/project/retry.py`
- `evals/agent_comparison/tasks/T2/project/tests/test_retry.py`
- `evals/agent_comparison/tasks/T2/grader/test_hidden_retry.py`
- `evals/agent_comparison/tasks/T3/task.txt`
- `evals/agent_comparison/tasks/T3/project/levels.py`
- `evals/agent_comparison/tasks/T3/project/events.py`
- `evals/agent_comparison/tasks/T3/project/tests/test_events.py`
- `evals/agent_comparison/tasks/T3/grader/test_hidden_events.py`
- `tests/test_agent_comparison.py`

**Generated after preflight:** `docs/evidence/agent-comparison-manifest.json`

**Generated after authorized runs:**

- `docs/evidence/agent-comparison-pilot.json`
- `docs/evidence/agent-comparison-track-a.json`
- `docs/evidence/agent-comparison-track-b.json` only when the exact same-model gate passes; otherwise the manifest records `NOT_RUN_MODEL_MISMATCH`.

## Task 1: Freeze three task templates before any product run

**Files:** Create all `tasks/` files above; modify `tests/test_agent_comparison.py`.

- [ ] **Step 1: Write RED integrity tests for all three absent task templates**

Do not create any `tasks/T1`, `tasks/T2`, or `tasks/T3` file yet. First add tests that assert every exact production file, visible test, task prompt, hidden semantic case, allowed path and reference-patch outcome described below.

T1 production fixture:

```python
# ranges.py
def chunk_ranges(total: int, size: int) -> list[tuple[int, int]]:
    """Return half-open ranges that cover [0, total) without empty chunks."""
    if total < 0:
        raise ValueError("total must be non-negative")
    if size <= 0:
        raise ValueError("size must be positive")
    result: list[tuple[int, int]] = []
    start = 0
    while start + size < total:  # frozen defect: exact final/full chunk is dropped
        result.append((start, min(start + size, total)))
        start += size
    return result
```

Visible test shows the symptom without enumerating all boundaries:

```python
def test_chunk_ranges_covers_remainder() -> None:
    assert chunk_ranges(5, 2) == [(0, 2), (2, 4), (4, 5)]
```

Hidden tests cover `total=0`, `total<size`, `total==size`, exact multiples, adjacent non-multiples and both validation errors. Task text: diagnose and fix the coverage defect, modify only `ranges.py`, preserve validation and run tests.

Freeze `T1/task.txt` exactly as:

```text
`chunk_ranges` 在某些输入下不能完整覆盖 `[0, total)`。请定位并修复根因。只允许修改 `ranges.py`，必须保留参数校验和半开区间语义，并运行现有测试确认结果；不要新增依赖或修改测试。
```

- [ ] **Step 2: Confirm RED before creating a fixture**

```powershell
python -m pytest tests/test_agent_comparison.py -k task_fixture -q
```

Expected: failures identify the missing T1/T2/T3 fixture paths; no participant system is run.

- [ ] **Step 3: Create the minimal T1 fixture and grader cases**

Create exactly the T1 production file, visible test, prompt and hidden boundary cases specified above. Do not add convenience metadata that exposes hidden expectations.

- [ ] **Step 4: Create the minimal T2 fixture and grader cases**

T2 fixture:

```python
# retry.py
def retry_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    """Return capped delay; attempt 1 waits base seconds."""
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    if base <= 0 or cap <= 0:
        raise ValueError("base and cap must be positive")
    return min(cap, base * (2 ** attempt))  # frozen defect: exponent is one too high
```

Existing visible tests cover validation and cap but omit attempt 1. Task text requires adding a meaningful regression test under `tests/` and fixing `retry.py`; no dependencies or unrelated changes.

Freeze the initial visible tests and task text exactly:

```python
import pytest

from retry import retry_delay


def test_retry_delay_reaches_cap() -> None:
    assert retry_delay(10) == 8.0


@pytest.mark.parametrize("attempt", [0, -1])
def test_retry_delay_rejects_invalid_attempt(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt"):
        retry_delay(attempt)
```

```text
`retry_delay` 的第一次重试等待时间不符合其文档约定。请先在 `tests/` 下新增一个能够捕获该根因的有意义回归测试，再修复 `retry.py`，并运行完整测试。不得删除或放宽现有测试，不得新增依赖或修改其他生产文件。
```

Hidden grading additionally performs the TDD artifact check:

1. discover the participant-added `test_*.py` bytes;
2. run those added tests against the original `retry.py` and require at least one failure attributable to attempt-1 semantics;
3. run them against the final patch and require pass;
4. run hidden boundaries for attempts 1–7, non-default base/cap and validation.

This verifies the final regression-test artifact, not the chronological red-before-green edit order.

- [ ] **Step 5: Create the minimal T3 fixture and grader cases**

T3 fixture:

```python
# levels.py
VALID_LEVELS = frozenset({"debug", "info", "warning", "error"})


def is_valid_level(value: str) -> bool:
    return value in VALID_LEVELS
```

```python
# events.py
from levels import is_valid_level


def format_event(message: str, level: str = "info") -> str:
    if not is_valid_level(level):
        raise ValueError(f"unsupported level: {level}")
    return f"[{level.upper()}] {message}"
```

Task text requires a compatible feature spanning exactly `levels.py` and `events.py`: add `normalize_level(value: str) -> str` that trims surrounding whitespace, lowercases, validates membership, raises `TypeError` for non-string and `ValueError` for unknown level; update `format_event` to use it while preserving the old signature and output. Visible tests cover `" Warning "` and existing `"info"`; hidden tests cover every valid level, empty/unknown/non-string inputs and `is_valid_level` backward compatibility.

Freeze the visible tests and task text exactly:

```python
from events import format_event


def test_format_event_preserves_existing_default() -> None:
    assert format_event("ready") == "[INFO] ready"


def test_format_event_accepts_human_level_input() -> None:
    assert format_event("disk", " Warning ") == "[WARNING] disk"
```

```text
为日志级别增加兼容的规范化能力：在 `levels.py` 新增 `normalize_level(value: str) -> str`，去除首尾空白、转为小写并验证支持的级别；非字符串抛出 `TypeError`，未知或空级别抛出 `ValueError`。在 `events.py` 中复用该函数，使 `format_event` 接受例如 `" Warning "` 的输入，同时保持原有函数签名、默认行为和输出格式。只允许修改 `levels.py`、`events.py`，并运行现有测试；不要新增依赖或修改测试。
```

- [ ] **Step 6: Implement and check all three initial/reference invariants**

Write `validate_task(task_id)` so tests require:

```text
initial FAIL_TO_PASS: at least one failure
initial PASS_TO_PASS: all pass
reference patch FAIL_TO_PASS + PASS_TO_PASS: all pass
task prompt/project/visible tests/hidden tests/reference patch: stable SHA-256
no network, clock, randomness or third-party import
allowed paths: T1 ranges.py; T2 retry.py and participant-added tests; T3 levels.py/events.py
```

Do not expose reference patches or `grader/` in participant workspaces. Reference patches may be represented as exact byte maps inside grader-only test utilities, not as files copied to runs.

- [ ] **Step 7: Confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k task_fixture -q
```

Expected GREEN: all integrity tests pass, with each initial FAIL_TO_PASS demonstrably red and each reference solution green.

## Task 2: Immutable manifest and balanced schedule

**Files:** Create `schema.py` and `manifest.py`; modify `tests/test_agent_comparison.py`.

- [ ] **Step 1: Write RED schema/schedule tests**

Implement against these concrete types:

```python
@dataclass(frozen=True)
class SystemConfig:
    system_id: str
    cli_version: str
    executable_sha256: str
    model: str
    auth_type: str
    argv_template: tuple[str, ...]
    environment_names: tuple[str, ...]
    permission_mode: str
    output_mode: str


@dataclass(frozen=True)
class RunCell:
    phase: str
    track: str
    system_id: str
    task_id: str
    replicate: int
    order_index: int


@dataclass(frozen=True)
class FrozenManifest:
    schema_version: int
    study_id: str
    seed: int
    timeout_seconds: int
    systems: tuple[SystemConfig, ...]
    task_hashes: dict[str, dict[str, str]]
    pilot: tuple[RunCell, ...]
    formal: tuple[RunCell, ...]
    track_b_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

Assert exactly 3 Pilot T1 cells and 18 formal cells; within each `(task, replicate)` block, all three systems appear once in seeded shuffled order. Two calls return byte-identical canonical JSON.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k manifest -q
```

- [ ] **Step 3: Implement manifest validation**

Reject:

```text
shell metacharacters or shell=True representation
empty/relative executable
credential-like argv values or environment values
unknown system/task/replicate
duplicate/missing run cells
timeout other than 900
unhashed task component
placeholder version/model/permission/output values
```

Environment manifests store names only. `auth_type` may be `environment`, `official-login-session`, or `none`; never account IDs/tokens. `track_b_status` is initially `PENDING_MODEL_CHECK`, then freezes to `READY` or `NOT_RUN_MODEL_MISMATCH` before Pilot.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k manifest -q
```

## Task 3: Fresh-workspace builder and hidden grader

**Files:** Create `workspace.py` and `grader.py`; modify `tests/test_agent_comparison.py`.

- [ ] **Step 1: Write RED isolation tests**

For each task, `create_run_workspace()` must:

```python
@dataclass(frozen=True)
class RunWorkspace:
    root: Path
    baseline_commit: str
    initial_tree_sha256: str
    initial_file_sha256: dict[str, str]


```

The builder has the exact interface `create_run_workspace(task_id: str, destination: Path) -> RunWorkspace`.

Assert it copies only participant-visible project files, initializes a local Git repository with one baseline commit, uses fixed local test identity (not the user's global Git identity), and contains no `grader`, hidden test, reference patch, parent path symlink/junction, `.env`, agent instruction, plugin, MCP, session or transcript artifact.

- [ ] **Step 2: Write RED grading tests**

Expose:

```python
@dataclass(frozen=True)
class GradeResult:
    resolved: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_passed: int
    pass_to_pass_total: int
    forbidden_changes: tuple[str, ...]
    regression: bool
    tests_observed: bool
    changed_files: tuple[str, ...]
    insertions: int
    deletions: int
    patch_sha256: str
    primary_failure: str | None
    evidence_tags: tuple[str, ...]


```

The grader has the exact interface `grade_workspace(task_id: str, workspace: RunWorkspace) -> GradeResult`.

Test correct, incorrect, no-change, forbidden-file, deleted-visible-test, syntax-error, T2-no-regression-test and T2-toothless-test patches. Primary failure precedence is frozen:

```text
SCOPE_VIOLATION > TOOL_OR_INFRA_FAILURE > TIMEOUT > REGRESSION >
DID_NOT_TEST > LOCALIZATION_FAILURE > INCORRECT_PATCH
```

Only run-specific timeout/crash is supplied by the caller; grader itself never infers model behavior from prose.

- [ ] **Step 3: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k "workspace or grader" -q
```

- [ ] **Step 4: Implement minimal builder/grader and confirm GREEN**

Use `git diff --binary --no-ext-diff` only to hash the final patch; do not store full patches in formal JSON. Copy hidden tests into a separate ephemeral grading directory or invoke them with `PYTHONPATH` pointing at the participant workspace after the Agent exits. Restore neither the participant patch nor baseline during grading except the isolated T2 artifact counterfactual copy.

```powershell
python -m pytest tests/test_agent_comparison.py -k "workspace or grader" -q
```

## Task 4: Safe subprocess adapters

**Files:** Create `adapters.py`; modify `tests/test_agent_comparison.py`.

- [ ] **Step 1: Write RED adapter tests with fake executables**

Public API:

```python
@dataclass(frozen=True)
class AdapterResult:
    returncode: int | None
    timed_out: bool
    elapsed_seconds: float
    tests_observed: bool
    stop_reason: str
    usage: dict[str, int | float | str | None] | str
```

The exact interfaces are `materialize_argv(config: SystemConfig, *, workspace: Path, task: str) -> tuple[str, ...]` and `run_adapter(config: SystemConfig, *, workspace: Path, task: str, timeout_seconds: int, source_environment: Mapping[str, str]) -> AdapterResult`.

Tests must prove: `shell=False`; current working directory is the run root; only explicit environment names plus a minimal OS allowlist survive; credential values never appear in result/exception text; a parent+child timeout leaves the child dead; raw stdout/stderr and final model answer are not returned; parser failure yields `usage="unavailable"`, not guessed metrics.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k adapter -q
```

- [ ] **Step 3: Implement using existing termination/redaction primitives**

Reuse `run_process()` and `sanitized_subprocess_environment()`; do not duplicate Windows Job Object or Unix process-group logic. `tests_observed` is true only when a product's machine-readable events or a before/after process marker establishes a pytest invocation; plain final prose is insufficient.

Frozen production command construction after local help verification:

```python
code_operator_argv = (
    sys.executable,
    "-m",
    "code_operator",
    "--workspace",
    str(workspace),
    "--auto-approve-tests",
    task,
)
kimi_code_argv = ("kimi", "-p", task, "--output-format", "stream-json")
```

For Claude Code, preflight constructs `claude_argv` only from the installed official help's supported print/non-interactive, machine-readable-output and workspace-safe permission options, then persists the resulting literal tuple in the reviewed manifest. No unresolved value may remain in the manifest. Do not invent or silently add bypass/unsafe permission flags. If Claude or Kimi cannot run non-interactively with safe workspace-limited permissions, preflight fails and the cell is classified as infrastructure-limited rather than granting broader authority.

- [ ] **Step 4: Confirm GREEN and kill behavior**

```powershell
python -m pytest tests/test_agent_comparison.py -k adapter -q
python -m pytest tests/test_golden_eval.py -k "timeout or process" -q
```

## Task 5: Environment preflight and official Kimi setup gate

**Files:** Modify `manifest.py` and tests; generate `docs/evidence/agent-comparison-manifest.json` only after review.

- [ ] **Step 1: Run read-only local discovery**

Locate `python`, `git`, `pytest`, `claude`, and `kimi` without installation. For found commands, capture only executable path hash, `--version`, and relevant `--help` option names. Do not print environment variables, config files, account IDs, auth caches or tokens.

- [ ] **Step 2: If `kimi` is absent, stop for installation approval**

Show the exact Moonshot official source, package name/version, install target, files expected to change and rollback method. Installation/login are external state changes and are not authorized by the design approval. Limit setup investigation to 60 minutes; if still unavailable, freeze `INVALID_INFRA` rather than substituting another tool.

- [ ] **Step 3: Freeze exact system configurations**

For each required system, record exact version/model/auth type/permission/output mode and literal argv template. Verify `kimi-k3` identity for Track B from product output or official configuration—not from product branding. If exact model and endpoint class cannot be matched, set `track_b_status=NOT_RUN_MODEL_MISMATCH` and do not run a near-model substitute.

- [ ] **Step 4: Review then create manifest exclusively**

Run all task, manifest, isolation, grader and adapter tests. Present task hashes, exact prompt texts, commands, providers, environment-variable names, timeout and data boundaries to the project author. After the configuration is accepted, write the canonical manifest once; any later manifest change invalidates Pilot/formal evidence and requires a new review.

## Task 6: Pilot—three excluded infrastructure runs

**Files:** Modify `run_study.py` and tests; generate `agent-comparison-pilot.json` after authorization.

- [ ] **Step 1: Write RED orchestration tests**

Test with fake adapters that `run_phase(manifest, phase="pilot")` executes exactly three T1 cells serially, creates a fresh workspace for each, always grades after process exit, deletes the workspace after extracting only approved metrics, retains failure rows, and refuses an existing output file.

- [ ] **Step 2: Confirm RED, implement, confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k orchestration -q
```

Implement the smallest coordinator; it must not retry any cell automatically.

- [ ] **Step 3: Obtain fresh data-transfer authorization**

Before Pilot, show all T1 visible files and exact T1 prompt plus the fact that each product may send the synthetic prompt, visible workspace files, patches, tool/command outputs and conversational context required for its run to Moonshot or Anthropic. Exact vendor-internal request envelopes and service-side retention are outside this harness's observability and must be stated as such. Ask for explicit approval covering only the three Pilot runs. Existing Moonshot O1 permission does not cover Claude, Kimi Code, or this dataset.

- [ ] **Step 4: Run Pilot once and decide infrastructure validity**

```powershell
python -m evals.agent_comparison.run_study --manifest docs/evidence/agent-comparison-manifest.json --phase pilot --report docs/evidence/agent-comparison-pilot.json
```

Pilot quality is excluded. Review only workspace isolation, timeout/child cleanup, output parsing, grader operation, environment scope and report redaction. Fixing a harness defect invalidates all three Pilot cells and requires a fresh three-cell Pilot; a poor patch does not.

## Task 7: Formal 18-cell Track A

**Files:** Generate `agent-comparison-track-a.json` after a separate authorization.

- [ ] **Step 1: Re-display the frozen outbound scope**

Show exact T1/T2/T3 visible files and prompts, all three providers/system configs, 18-cell seeded order, maximum 15 minutes per cell, possible outbound synthetic prompts/files/patches/command outputs/context, report schema and local non-retention boundaries. Obtain new explicit authorization covering the formal 18 runs; do not imply that local non-retention controls vendor-side processing.

- [ ] **Step 2: Confirm pre-run invariants**

```powershell
python -m pytest tests/test_agent_comparison.py -q
git status --short --branch
```

Recompute every manifest/task hash; require an exact match. The output file must not exist.

- [ ] **Step 3: Run the entire frozen schedule once**

```powershell
python -m evals.agent_comparison.run_study --manifest docs/evidence/agent-comparison-manifest.json --phase formal --report docs/evidence/agent-comparison-track-a.json
```

Do not rerun a timeout, refusal, crash, bad patch or low score. On harness/fixture/grader defect, stop, mark the affected complete balanced block invalid, correct via TDD, obtain review/authorization, and rerun the entire invalid block—not selected cells.

- [ ] **Step 4: Validate completeness without interpreting winners**

Require exactly 18 unique `(system, task, replicate)` rows, two rows per system-task pair, six per system, no missing/duplicate order index, and every row carrying task/manifest/patch hashes. Summary remains raw `0/2`, `1/2`, `2/2`, and later `x/6` only.

## Task 8: Conditional Track B and optional systems

- [ ] If and only if the frozen manifest says `READY`, create a separate Track B schedule for T2 with two paired replicate blocks: code-operator and Kimi Code each run twice, four cells total, under exact `kimi-k3` and endpoint-class parity. Obtain separate authorization, store results separately and state the remaining scaffold/tool/prompt confounds.
- [ ] If the manifest says `NOT_RUN_MODEL_MISMATCH`, create no Track B run and preserve that exact reason.
- [ ] Codex is optional only after all mandatory O1, deterministic, Pilot, Track A and report prerequisites fit the cutoff. If admitted, it must run all 3×2 cells with a new pre-registration and authorization; no cherry-picked task.
- [ ] Deep Code/DeepSeek is last in the degradation order and follows the same full-suite rule. Never let optional setup endanger mandatory synthesis or the 18:00 freeze.

## Task 9: Full verification and human review checkpoint

```powershell
python -m pytest tests/test_agent_comparison.py -q
python -m pytest -q
python -m compileall -q code_operator evals tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
git status --short --branch
```

- [ ] Scan candidate and history for exact current API Key, Bearer values, private-key headers, sk-like credentials, local absolute paths, auth/config/cache/session/transcript/log/tmp artifacts; report counts without printing secret values.
- [ ] Confirm no raw stdout/stderr, final product response, prompt text, reasoning, credential or absolute ephemeral path entered result JSON.
- [ ] Add `REVIEW_LOG.md#research-comparison-001` only after the complete harness, manifest, Pilot/formal evidence and limits have been reviewed.
- [ ] Obtain `RESEARCH-COMPARISON-001` human approval before local commit `test(research): add reproducible agent comparison harness`.
- [ ] Do not push without the complete v4.5 section 9.5 display and new exact push authorization.

## Scientific claim boundary

The comparison unit is the full configured product, not the base model or isolated scaffold. Results apply only to these three frozen synthetic tasks and two replicates. Do not calculate significance, confidence intervals or an “industry rank”; do not claim official SWE-bench comparability; do not hide failures or infer missing token/cost data. A lower score remains a valid research result when its failure evidence is reproducible.
