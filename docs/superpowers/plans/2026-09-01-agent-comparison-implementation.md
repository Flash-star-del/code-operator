# Three-System Agent Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个可复现、SWE-bench 风格但不冒充官方榜单的微基准，对 code-operator、Claude Code、Kimi Code 在三个冻结 Python 任务上各运行一次，统一评分并如实保留全部 9 个正式结果。

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
REPLICATES = (1,)
TIMEOUT_SECONDS = 360
FORMAL_RUN_COUNT = 9
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

- `docs/evidence/agent-comparison-pilot-9.json`（旧 `agent-comparison-pilot*.json` 均为旧 manifest 下的无效证据，保留不覆盖）
- `docs/evidence/agent-comparison-track-a-9.json`
- `docs/evidence/agent-comparison-track-b.json` only when the exact same-model gate passes; otherwise the manifest records `NOT_RUN_MODEL_MISMATCH`.

**九单元修订（2026-09-02，第三次无效 Pilot 之后、任何正式单元之前）：** 项目作者认定原 18 单元方案过于复杂，明确采用快速方案：正式 Track A 收敛为 3 系统 × 3 任务 × 1 次 = 9 单元，严格串行、每单元 360 秒、不自动重试，重复性研究目标取消。能力主判据仅为独立隐藏测试、补丁正确性与文件范围；CLI 事件解析只作辅助遥测，`INVALID_OUTPUT` 不覆盖独立 grader 结果；grader 与报告忽略 `__pycache__`、`.pytest_cache`、`*.pyc`；只输出简单结果表，不计算显著性、置信区间或行业排名。TDD 证据：缓存误判修复先 RED（回归测试实际得到 `primary_failure == SCOPE_VIOLATION`）后 GREEN（`test_task1_grader_ignores_pytest_generated_cache_files` 通过），`_is_cache_path()` 在 Git status、diff name-status 与 numstat 路径进入 `changed_files` 前统一过滤；manifest 缩减先 RED（两个 manifest 测试对旧 `(1, 2)`/18 单元失败）后 GREEN（manifest 专项 7 passed）；`run_phase(phase=)` 与 CLI `--phase pilot|formal` 入口先 RED（2 failed）后 GREEN（合并定向验证 10 passed），其中 formal 门禁要求恰好九个唯一 `system × task × replicate=1` 单元，且 adapter `INVALID_OUTPUT` 不覆盖 grader 结果。横评专项套件（`test_agent_comparison.py`、`test_agent_comparison_manifest_env.py`、`test_agent_comparison_protocols.py`）共 149 passed。旧 18 单元 manifest 保留为 `docs/evidence/agent-comparison-manifest-18-invalid.json`（SHA-256 `ef56fc36ba4af7a5cc9ab4e7f3766bd9124e6a5a6986146c79399a5c11bad5e6`）；新九单元 manifest 为 `docs/evidence/agent-comparison-manifest.json`（SHA-256 `bf29d3eb9cfef21531c1a11d2307314426daf1ff5995109dc5b27ebdb364e890`），严格 loader 复核输出 3 Pilot、9 formal、360 秒。三次无效 Pilot 时间线（见 Task 6）保持为旧 manifest 下的无效基础设施证据，不覆盖、不复用；新 Pilot 与正式运行均须重新授权，报告排他写入上述 `-9` 文件。另将 `tests/test_session_probe.py::test_subprocess_report_contains_counts_only` 的禁止值断言由字符串搜索改为递归检查 JSON 标量值是否精确等于整数 41/42（原断言把运行耗时 `2.141…` 中的子串 `41` 误判；生产代码零改动），修复后完整离线套件在全新 `--basetemp` 下重跑为 854 passed, 7 skipped。注：本机 `Temp\pytest-of-86138` 目录 ACL 已损坏（当前用户连列出都被拒绝，与代码无关），在清理前完整套件需以 `--basetemp` 指向全新目录运行。

**Kimi 0.40.1 重预检与 manifest 更替（2026-09-02，第四次无效 Pilot 之后）：** `kimi.exe` 于当日 19:21 由 0.39.1 自动更新至 0.40.1，启动前哈希检查按设计拒绝未冻结二进制（见 Task 6 时间线尝试 4）。经项目作者批准执行只读重预检：0.40.1 的 `--help` 仍支持冻结 argv 的 `-m`/`-p`/`--output-format stream-json`/`--skills-dir`，持久默认模型仍为 `moonshot-cn/kimi-k3`，登录产物存在。新 manifest 与上一版逐字段对比仅 `cli_version`（0.39.1→0.40.1）与 `executable_sha256` 两处变化，九单元顺序、任务哈希、360 秒与 Track B 状态均不变，严格 loader 复核 3 Pilot、9 formal、360 秒。项目作者审核接受后：上一版保留为 `docs/evidence/agent-comparison-manifest-9-kimi0391-invalid.json`（SHA-256 `bf29d3eb9cfef21531c1a11d2307314426daf1ff5995109dc5b27ebdb364e890`），现行 `docs/evidence/agent-comparison-manifest.json` 的 SHA-256 为 `66cf45ea705bf6b1ac76e409b961eca46b9465c76816c291bacd296278fe901a`。建议项目作者在 Kimi Code 设置中关闭自动更新以防再次漂移；下次 Pilot 需重新授权并继续排他写入新报告文件。

## Task 1: Freeze three task templates before any product run

**Files:** Create all `tasks/` files above; modify `tests/test_agent_comparison.py`.

- [x] **Step 1: Write RED integrity tests for all three absent task templates**

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

- [x] **Step 2: Confirm RED before creating a fixture**

```powershell
python -m pytest tests/test_agent_comparison.py -k task_fixture -q
```

Expected: failures identify the missing T1/T2/T3 fixture paths; no participant system is run.

- [x] **Step 3: Create the minimal T1 fixture and grader cases**

Create exactly the T1 production file, visible test, prompt and hidden boundary cases specified above. Do not add convenience metadata that exposes hidden expectations.

- [x] **Step 4: Create the minimal T2 fixture and grader cases**

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

- [x] **Step 5: Create the minimal T3 fixture and grader cases**

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

- [x] **Step 6: Implement and check all three initial/reference invariants**

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

- [x] **Step 7: Confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k task_fixture -q
```

Expected GREEN: all integrity tests pass, with each initial FAIL_TO_PASS demonstrably red and each reference solution green.

Task 1 evidence (2026-09-02): the initial integrity test failed on 13 absent fixture paths before any participant system ran. Four TDD/review correction rounds then closed T2 counterfactual attribution, cache-stable framed hashes, exact fixture completeness, candidate path/link handling and structured invalid-input results. Final focused verification was `15 passed`; the same working tree also completed `712 passed, 7 skipped` in the full offline suite before the final input-hardening round. Independent reviews concluded `SPEC APPROVED` and `QUALITY APPROVED`. The helper deliberately does not claim an OS-level sandbox; formal run-workspace and grader boundaries remain Task 3 work.

## Task 2: Immutable manifest and balanced schedule

**Files:** Create `schema.py` and `manifest.py`; modify `tests/test_agent_comparison.py`.

- [x] **Step 1: Write RED schema/schedule tests**

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

- [x] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k manifest -q
```

- [x] **Step 3: Implement manifest validation**

Reject:

```text
shell metacharacters or shell=True representation
empty/relative executable
credential-like argv values or environment values
unknown system/task/replicate
duplicate/missing run cells
timeout other than 360
unhashed task component
placeholder version/model/permission/output values
```

Environment manifests store names only. `auth_type` may be `environment`, `official-login-session`, or `none`; never account IDs/tokens. `track_b_status` is initially `PENDING_MODEL_CHECK`, then freezes to `READY` or `NOT_RUN_MODEL_MISMATCH` before Pilot.

- [x] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k manifest -q
```

Task 2 evidence (2026-09-02): RED tests exposed fabricated default system metadata, an unshuffled Pilot, schedule permutations that still validated, mutable nested hashes, malformed-input exceptions, credential-like metadata and an unsafe truthy Track B gate. The final implementation requires explicit preflight `SystemConfig` values, freezes the full seeded schedule and exact task hashes, deep-freezes nested hash mappings and returns stable violations for invalid inputs. Final focused verification was `34 passed, 19 deselected`; the complete comparison test file reached `49 passed` before the final four-case strict-boolean addition. Independent reviews concluded `SPEC APPROVED` and `QUALITY APPROVED`; compileall and `git diff --check` passed. No formal manifest or external run was created.

## Task 3: Fresh-workspace builder and hidden grader

**Files:** Create `workspace.py` and `grader.py`; modify `tests/test_agent_comparison.py`.

- [x] **Step 1: Write RED isolation tests**

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

- [x] **Step 2: Write RED grading tests**

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

- [x] **Step 3: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k "workspace or grader" -q
```

- [x] **Step 4: Implement minimal builder/grader and confirm GREEN**

Use `git diff --binary --no-ext-diff` only to hash the final patch; do not store full patches in formal JSON. Copy hidden tests into a separate ephemeral grading directory or invoke them with `PYTHONPATH` pointing at the participant workspace after the Agent exits. Restore neither the participant patch nor baseline during grading except the isolated T2 artifact counterfactual copy.

```powershell
python -m pytest tests/test_agent_comparison.py -k "workspace or grader" -q
```

Task 3 evidence (2026-09-02): module-absence RED tests preceded implementation. Review-driven TDD then covered all three clean Git workspaces, fixed local identity and one baseline commit, artifact/link exclusion, correct/incorrect/no-change/scope/syntax/T2-test outcomes, full primary-failure precedence, structured invalid input and timeout handling, deep-frozen snapshots and deterministic staged/unstaged/untracked patch hashes without changing participant Git state. A final regression closed a `.git/info/exclude` scope bypass by independently enumerating every non-cache file outside `.git`. Final Task 3 verification was `37 passed, 53 deselected`; independent review concluded `SPEC APPROVED` and the focused integrity re-review concluded `REVIEW APPROVED`. This is an integrity grader, not an OS sandbox.

## Task 4: Safe subprocess adapters

**Files:** Create `adapters.py`; modify `tests/test_agent_comparison.py`.

- [x] **Step 1: Write RED adapter tests with fake executables**

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

- [x] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_agent_comparison.py -k adapter -q
```

- [x] **Step 3: Implement using existing termination/redaction primitives**

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

- [x] **Step 4: Confirm GREEN and kill behavior**

```powershell
python -m pytest tests/test_agent_comparison.py -k adapter -q
python -m pytest tests/test_golden_eval.py -k "timeout or process" -q
```

Task 4 evidence (2026-09-02): module/API absence produced the RED gate. Review-driven TDD added literal placeholder validation, explicit manifest-approved environments, pre-launch executable hashing, bounded strict JSON-event parsing, parent/child timeout cleanup and stable non-retaining results. Because code-operator intentionally prints a human trace, its existing redacted `.code-operator/audit.jsonl` is used as the machine observation and then safely removed before grading; denied commands with no integer exit code do not count as executed tests. Final focused verification was `15 passed` for adapters and `6 passed` for existing timeout/process tests. Independent reviews concluded `SPEC APPROVED` and `QUALITY APPROVED`; compileall and `git diff --check` passed. No real product command was run.

## Task 5: Environment preflight and official Kimi setup gate

**Files:** Modify `manifest.py` and tests; generate `docs/evidence/agent-comparison-manifest.json` only after review.

- [x] **Step 1: Run read-only local discovery**

Locate `python`, `git`, `pytest`, `claude`, and `kimi` without installation. For found commands, capture only executable path hash, `--version`, and relevant `--help` option names. Do not print environment variables, config files, account IDs, auth caches or tokens.

- [x] **Step 2: If `kimi` is absent, stop for installation approval**

Show the exact Moonshot official source, package name/version, install target, files expected to change and rollback method. Installation/login are external state changes and are not authorized by the design approval. Limit setup investigation to 60 minutes; if still unavailable, freeze `INVALID_INFRA` rather than substituting another tool.

- [x] **Step 3: Freeze exact system configurations**

For each required system, record exact version/model/auth type/permission/output mode and literal argv template. Verify `kimi-k3` identity from product output, official configuration or an explicit project-author route attestation—not from product branding. When all three Track A systems are already frozen to `kimi-k3`, set `track_b_status=NOT_RUN_REDUNDANT_UNIFIED_TRACK_A` rather than repeating a smaller controlled-model track.

Preflight status (2026-09-02): Python 3.11.5, Git 2.47.1.windows.1 and pytest 8.4.2 were found. Claude Code 2.1.131 was found at its hashed executable and lists print, stream-JSON, model, `dontAsk`, tool allow/deny and no-session-persistence controls. The project author installed and logged in to Kimi Code 0.39.1 personally; its executable SHA-256 is frozen and its persistent default model is `moonshot-cn/kimi-k3`. The project author then used CCSwitch 3.19.2 (SHA-256 `f712ddd95e080eb6a353ffa434904de5e786cbf11c96201a72af73ffa6116612`) to persist a Moonshot Anthropic-compatible Claude Code route; read-only inspection confirmed that the primary and default Claude model mappings all equal `kimi-k3`, the credential is present without reading or displaying its value, and the settings contain no hooks, MCP servers, plugins, agents or permission rules. code-operator's configured Moonshot endpoint and `kimi-k3` model also matched. CCSwitch is configuration provenance, not an additional process launched by the harness. The project author explicitly confirmed the routed model identity and asked to proceed without a separate model probe. Step 4 remains pending for exact command, environment and manifest review.

- [x] **Step 4: Review then create manifest exclusively**

Run all task, manifest, isolation, grader and adapter tests. Present task hashes, exact prompt texts, commands, providers, environment-variable names, timeout and data boundaries to the project author. After the configuration is accepted, write the canonical manifest once; any later manifest change invalidates Pilot/formal evidence and requires a new review.

Task 5 evidence (2026-09-02): `RESEARCH-MANIFEST-001` approved the exact three-system configuration, unified `kimi-k3` Track A, redundant Track B status and 360-second serial timeout before any product run. TDD first failed on the absent redundancy status and exclusive writer; the minimal implementation then accepted the explicit status and wrote exact canonical bytes with create-only semantics. The complete comparison suite passed `145` tests. The resulting 5,303-byte manifest has SHA-256 `ef56fc36ba4af7a5cc9ab4e7f3766bd9124e6a5a6986146c79399a5c11bad5e6`, validates with three Pilot and eighteen formal cells, and contains zero detected credential-value or private-key patterns. No product was run and no data was sent.

## Task 6: Pilot—three excluded infrastructure runs

**Files:** Modify `run_study.py` and tests; generate `agent-comparison-pilot.json` after authorization.

- [x] **Step 1: Write RED orchestration tests**

Test with fake adapters that `run_phase(manifest, phase="pilot")` executes exactly three T1 cells serially, creates a fresh workspace for each, always grades after process exit, deletes the workspace after extracting only approved metrics, retains failure rows, and refuses an existing output file.

- [x] **Step 2: Confirm RED, implement, confirm GREEN**

```powershell
python -m pytest tests/test_agent_comparison.py -k orchestration -q
```

Implement the smallest coordinator; it must not retry any cell automatically.

Task 6 Steps 1–2 evidence (2026-09-02): module-absence and CLI-absence RED gates preceded a serial, non-retaining coordinator with strict canonical manifest loading, create-only reports, fresh workspaces, post-process grading, failure-row retention, environment-name filtering and code-operator-only `PYTHONPATH` injection. Focused orchestration verification passed `8` tests; the later full comparison gate passed `145` tests. Product-specific offline protocol fixtures additionally cover Kimi Message JSONL and Claude stream-JSON without retaining message or tool-result payloads. At that offline checkpoint, no external product command had run.

Invalid Pilot attempt 1 (2026-09-02): after fresh outbound authorization, the first Kimi Code cell returned and grading completed, but cleanup stopped on `WinError 5` before any report was created; code-operator and Claude Code were never started. Root-cause inspection found five read-only loose Git objects in the synthetic temporary workspace and no evaluation child process. A real-Git cleanup test reproduced the failure before implementation; the minimal Windows-compatible callback clears read-only attributes only after `PermissionError` and retries the failed removal. Focused cleanup/orchestration verification passed `3` tests and the complete comparison suite passed `146` tests. The exact synthetic leftover was removed after path validation, the manifest remains byte-identical at SHA-256 `ef56fc36ba4af7a5cc9ab4e7f3766bd9124e6a5a6986146c79399a5c11bad5e6`, and no Pilot report exists. The attempt is invalid infrastructure evidence, not a product result; the full three-cell Pilot requires fresh authorization and no cell may be selectively reused.

Invalid Pilot attempt 2 (2026-09-02): after fresh authorization, all three cells started but exited in under one second. Boundary checks reproduced that Claude Code and Kimi Code fail only under the coordinator's over-restricted environment, while their version commands work in the normal process environment; code-operator's help command remained usable. The invalid report is retained as `agent-comparison-pilot-invalid-environment.json` with SHA-256 `051ffcd677301125233c736a1109d7b9dc52e7892c6df627e9cf451fe3956c2f`. A RED test then required a fixed minimal OS allowlist in addition to manifest-declared credential names; the minimal implementation passed focused command checks and the complete comparison suite passed `146` tests. This is invalid environment evidence, not a product result.

Invalid Pilot attempt 3 (2026-09-02): after a third fresh authorization and the minimal-OS fix, all three real products ran serially against fresh T1 workspaces. Kimi Code and Claude Code returned zero after 91.56 and 72.39 seconds respectively and each changed only `ranges.py` apart from Python cache artifacts; code-operator returned one after 186.80 seconds and also created `_verify_edge.py` and `_verify_edge_test.py`. The report nevertheless marked all three cells `SCOPE_VIOLATION` because Git-status collection admitted `__pycache__/*.pyc` paths before the independent non-cache enumeration filter. Separately, the real Kimi Code and Claude Code event streams did not satisfy the existing offline protocol parsers, so their adapter results are `INVALID_OUTPUT` with `tests_observed=false`. The 4,398-byte report is retained as `agent-comparison-pilot.json` with SHA-256 `33f4a36862da79ba68b43f9a576115e4aa7d972115e587d03105ca1f3f93eead`; detected credential-value and private-key pattern counts are zero. This balanced block remains invalid infrastructure evidence and does not authorize or justify formal Track A.

Invalid Pilot attempt 4 (2026-09-02, nine-cell manifest): after a fourth fresh three-cell authorization, the serial runner completed and exclusively wrote `agent-comparison-pilot-9.json` (3,670 bytes, SHA-256 `25554539ead8c75202890ec20afa3092e84ca7b76b355726150c2c27b6e8eb2a`). The Kimi Code cell ended in 0.25 seconds with `INFRA_ERROR` before launch: `kimi.exe` had auto-updated from 0.39.1 to 0.40.1 at 19:21 the same day, and the pre-launch executable-hash check correctly refused the unfrozen binary. code-operator exited 1 after 21.5 seconds; Claude Code exited 0 after 58.2 seconds with `INVALID_OUTPUT` telemetry (known event-parser gap, not a grading input). Both changed only `ranges.py` (+1/−1) and were independently graded resolved with hidden and visible suites green, validating the new cache filter in production; no `SCOPE_VIOLATION` occurred and no workspace leftovers remained. Because one system never ran, the balanced block is invalid infrastructure evidence, not a product result; continuing requires re-preflight of Kimi Code 0.40.1, a newly reviewed manifest, and fresh Pilot authorization.

Valid Pilot attempt 5 (2026-09-02, Kimi 0.40.1 manifest `66cf45ea…fe901a`): after a fifth fresh three-cell authorization and a pre-launch self-check (all three executable hashes matched the frozen manifest), the serial runner completed and exclusively wrote `agent-comparison-pilot-9-v2.json` (3,812 bytes, SHA-256 `62f2edba35e872158e591b609909f51e38ee6308390ecc78e53c91ae592ce612`). Kimi Code ran 80.4 seconds, exit 0, independently graded resolved with only `ranges.py` (+1/−1) and hidden plus visible suites green (`INVALID_OUTPUT` telemetry unchanged and non-scoring). code-operator ran 151.0 seconds, exit 1, and left `_check_ranges_tmp.py` and `tests/_tmp_edge_check.py`, a genuine `SCOPE_VIOLATION` correctly detected past the cache filter. Claude Code exited 1 after only 4.5 seconds with an empty diff, zero input/output tokens and 0.004 USD cost — graded `LOCALIZATION_FAILURE`; this resembles an immediate product-side API error rather than an attempted fix (attempt 4 had resolved in 58.2 seconds), and raw output is intentionally not retained, so no deeper root cause is recorded. Every pipeline stage — frozen-hash launch, serial execution, independent grading, real scope-violation detection, exclusive report write, credential-free output, workspace cleanup — was exercised, so the project author accepted the Pilot as valid infrastructure evidence and separately authorized the formal nine-cell Track A run, with any recurrence of the Claude Code fast-exit to be recorded honestly as a product FAIL.

Formal nine-cell Track A run (2026-09-02): after a separate explicit authorization, all nine manifest cells (order indices 3–11) ran strictly serially with no retries and no timeouts, and the report was exclusively written to `agent-comparison-track-a-9.json` (10,864 bytes, SHA-256 `dea849aadfe2603f2dbb008589395bb931bf6189532a561960a5997eb499f5ce`, manifest `66cf45ea…fe901a`). Completeness checks passed: exactly nine unique `system × task × replicate=1` rows, no missing or duplicate order index, every row carrying task and patch hashes, credential-pattern scan zero hits. Raw per-system results: kimi-code 3/3 resolved (only in-scope files changed); code-operator 2/3 (T2 and T3 resolved; T1 failed `SCOPE_VIOLATION` by leaving `_verify_ranges.py`, the same temp-artifact behavior seen in Pilot attempts 3 and 5); claude-code 0/3 (all three cells exited in 6.2–6.4 seconds with empty diffs — `LOCALIZATION_FAILURE` twice and `DID_NOT_TEST` once — matching the Pilot attempt 5 fast-exit anomaly and resembling an immediate product-side error during this run window rather than an attempted fix; per the frozen no-retry protocol these are recorded as FAIL with this caveat stated, and no significance, confidence intervals or industry ranking are computed). Results apply only to these three frozen synthetic tasks and a single replicate per pair.

- [x] **Step 3: Obtain fresh data-transfer authorization**

Before Pilot, show all T1 visible files and exact T1 prompt plus the fact that each product may send the synthetic prompt, visible workspace files, patches, tool/command outputs and conversational context required for its run to Moonshot or Anthropic. Exact vendor-internal request envelopes and service-side retention are outside this harness's observability and must be stated as such. Ask for explicit approval covering only the three Pilot runs. Existing Moonshot O1 permission does not cover Claude, Kimi Code, or this dataset.

- [ ] **Step 4: Run Pilot once and decide infrastructure validity**

```powershell
python -m evals.agent_comparison.run_study --manifest docs/evidence/agent-comparison-manifest.json --phase pilot --report docs/evidence/agent-comparison-pilot-9.json
```

Pilot quality is excluded. Review only workspace isolation, timeout/child cleanup, output parsing, grader operation, environment scope and report redaction. Fixing a harness defect invalidates all three Pilot cells and requires a fresh three-cell Pilot; a poor patch does not.

## Task 7: Formal nine-cell Track A

**Files:** Generate `agent-comparison-track-a-9.json` after a separate authorization.

- [ ] **Step 1: Re-display the frozen outbound scope**

Show exact T1/T2/T3 visible files and prompts, all three providers/system configs, nine-cell seeded order, maximum 6 minutes per cell, possible outbound synthetic prompts/files/patches/command outputs/context, report schema and local non-retention boundaries. Obtain new explicit authorization covering the formal nine runs; do not imply that local non-retention controls vendor-side processing.

Pre-registration amendment (2026-09-02, before manifest creation, Pilot, or any formal cell): the project author approved reducing the common per-cell timeout from 900 seconds to 360 seconds. All three systems and every Pilot/formal cell use the same limit; execution remains strictly serial, timeouts remain retained failures, and no completed sample was observed or selected before this amendment.

- [ ] **Step 2: Confirm pre-run invariants**

```powershell
python -m pytest tests/test_agent_comparison.py -q
git status --short --branch
```

Recompute every manifest/task hash; require an exact match. The output file must not exist.

- [ ] **Step 3: Run the entire frozen schedule once**

```powershell
python -m evals.agent_comparison.run_study --manifest docs/evidence/agent-comparison-manifest.json --phase formal --report docs/evidence/agent-comparison-track-a-9.json
```

Do not rerun a timeout, refusal, crash, bad patch or low score. On harness/fixture/grader defect, stop, mark the affected complete balanced block invalid, correct via TDD, obtain review/authorization, and rerun the entire invalid block—not selected cells.

- [ ] **Step 4: Validate completeness without interpreting winners**

Require exactly nine unique `(system, task, replicate=1)` rows, one row per system-task pair, three per system, no missing/duplicate order index, and every row carrying task/manifest/patch hashes. Summary remains a simple per-system `x/3` table with per-task PASS/FAIL and failure reasons only.

## Task 8: Conditional Track B and optional systems

- [ ] If and only if the frozen manifest says `READY`, create a separate Track B schedule for T2 with two paired replicate blocks: code-operator and Kimi Code each run twice, four cells total, under exact `kimi-k3` and endpoint-class parity. Obtain separate authorization, store results separately and state the remaining scaffold/tool/prompt confounds.
- [ ] If the manifest says `NOT_RUN_MODEL_MISMATCH`, create no Track B run and preserve that exact reason.
- [ ] Codex is optional only after all mandatory O1, deterministic, Pilot, Track A and report prerequisites fit the cutoff. If admitted, it must run all 3×1 cells with a new pre-registration and authorization; no cherry-picked task.
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

The comparison unit is the full configured product, not the base model or isolated scaffold. Results apply only to these three frozen synthetic tasks and a single replicate per system-task pair. Do not calculate significance, confidence intervals or an “industry rank”; do not claim official SWE-bench comparability; do not hide failures or infer missing token/cost data. A lower score remains a valid research result when its failure evidence is reproducible.
