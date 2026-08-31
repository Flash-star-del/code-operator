# Deterministic Reliability Ablation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以冻结的确定性场景比较当前生产机制与三个 Eval-only 弱基线，回答完整组上下文裁剪、中止结果补齐和结构化错误回灌是否维持其声明的不变量。

**Architecture:** 研究包只读取生产公开接口，并把弱基线实现隔离在 `evals/reliability/`。每个实验先生成不可变场景，再对 baseline/production 两臂运行统一验证器；主结果不联网、不修改生产代码、不用模型自述作判据。

**Tech Stack:** Python 3.11、pytest、标准库 `dataclasses/hashlib/json/pathlib`、现有 `ContextManager`、`AgentLoop`、`ToolRegistry`、`FakeModelClient`、`write_report_exclusive`。

---

## Files and frozen public API

**Create:**

- `evals/reliability/__init__.py`
- `evals/reliability/schema.py`
- `evals/reliability/context_study.py`
- `evals/reliability/abort_study.py`
- `evals/reliability/error_study.py`
- `evals/run_reliability_study.py`
- `tests/test_reliability_study.py`

**Generated:** `docs/evidence/reliability-study.json`

Public immutable schema:

```python
STUDY_ID = "reliability-2026-09-01-preregistered"


@dataclass(frozen=True)
class PairingViolation:
    kind: str
    call_id: str | None
    message_index: int


@dataclass(frozen=True)
class ArmResult:
    scenario_id: str
    mechanism: str
    arm: str
    passed: bool
    metrics: dict[str, int | float | str | bool | None]
    violations: tuple[PairingViolation, ...] = ()


@dataclass(frozen=True)
class StudyReport:
    schema_version: int
    study_id: str
    scenario_manifest_sha256: str
    results: tuple[ArmResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

The deterministic report must contain no raw model prompt, temporary absolute path, environment value, traceback, command stdout/stderr, or provider credential.

## Task 1: Pairing validator and frozen scenario manifest

**Files:** Create `evals/reliability/schema.py`; create `tests/test_reliability_study.py`.

- [ ] **Step 1: Write RED tests for pairing violations**

Use these exact cases:

```python
def test_validate_tool_pairing_accepts_complete_ordered_group() -> None:
    messages = [
        {"role": "assistant", "tool_calls": [call("c1"), call("c2")]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "tool", "tool_call_id": "c2", "content": "{}"},
    ]
    assert validate_tool_pairing(messages) == ()


@pytest.mark.parametrize(
    ("messages", "kind"),
    [
        ([{"role": "tool", "tool_call_id": "orphan"}], "ORPHAN_RESULT"),
        ([{"role": "assistant", "tool_calls": [call("missing")]}], "MISSING_RESULT"),
        ([
            {"role": "assistant", "tool_calls": [call("a"), call("b")]},
            {"role": "tool", "tool_call_id": "b"},
            {"role": "tool", "tool_call_id": "a"},
        ], "OUT_OF_ORDER_RESULT"),
    ],
)
def test_validate_tool_pairing_classifies_invalid_groups(messages, kind) -> None:
    assert validate_tool_pairing(messages)[0].kind == kind
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_reliability_study.py -q
```

Expected: `evals.reliability.schema` is missing.

- [ ] **Step 3: Implement the validator and canonical manifest hashing**

Add the exact interface `validate_tool_pairing(messages: Sequence[Mapping[str, object]]) -> tuple[PairingViolation, ...]` and this canonical hash helper:

```python
def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

The validator walks assistant tool IDs in declaration order, consumes exactly that many immediately following tool messages, and emits stable kinds for missing, orphan, duplicate, and out-of-order IDs. It must not import private methods from `ContextManager`.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_reliability_study.py -q
```

## Task 2: RQ1 full-group trimming versus message-level trimming

**Files:** Create `evals/reliability/context_study.py`; modify `tests/test_reliability_study.py`.

- [ ] **Step 1: Write RED tests for the frozen matrix**

Define the exact scenario type and matrix:

```python
@dataclass(frozen=True)
class ContextScenario:
    scenario_id: str
    messages: tuple[dict[str, object], ...]
    tools: tuple[dict[str, object], ...]
    context_window: int
    max_output_tokens: int


def frozen_context_scenarios() -> tuple[ContextScenario, ...]:
    return tuple(context_scenario(*row) for row in CONTEXT_MATRIX)
```

`context_scenario(scenario_id: str, old_turns: int, current_groups: int, calls_per_group: int, payload: str, baseline_drop_count: int) -> ContextScenario` builds literal roles, IDs and payloads deterministically. It sets `max_output_tokens=64`; computes the input budget by applying exactly `baseline_drop_count` oldest-message removals to a copy; and sets `context_window=64 + estimate_tokens(after_exact_removals, tools)`. Freeze this exact matrix in source before execution:

```python
CONTEXT_MATRIX = (
    ("C1_ONE_CALL_BOUNDARY", 0, 1, 1, "x" * 180, 1),
    ("C2_TWO_CALL_BOUNDARY", 0, 1, 2, "y" * 180, 1),
    ("C3_OLD_TURN_DROP", 1, 1, 1, "old" * 80, 3),
    ("C4_MULTI_TURN_MULTI_CALL", 2, 2, 2, "payload" * 40, 4),
    ("C5_NO_TOOL_OLD_TURN", 2, 0, 0, "plain" * 60, 2),
    ("C6_UTF8_BOUNDARY", 1, 1, 2, "中文路径" * 60, 3),
)
```

The builder always emits one system message, `old_turns` complete old user turns, and one current user turn; each tool group is one assistant message followed immediately by `calls_per_group` ordered tool results. Tool IDs are `{scenario_id}-g{group}-c{call}` and every tool result content is `{"ok":true}` plus the fixed payload. `C5` uses complete assistant text groups without tools. The test first asserts the calibrated full context exceeds the input budget and the exact-removal copy fits, preventing a vacuous no-trim case. Assert:

```python
scenarios = frozen_context_scenarios()
assert tuple(item.scenario_id for item in scenarios) == (
    "C1_ONE_CALL_BOUNDARY",
    "C2_TWO_CALL_BOUNDARY",
    "C3_OLD_TURN_DROP",
    "C4_MULTI_TURN_MULTI_CALL",
    "C5_NO_TOOL_OLD_TURN",
    "C6_UTF8_BOUNDARY",
)
for scenario in scenarios:
    baseline, production = run_context_scenario(scenario)
    assert production.arm == "production_full_group"
    assert production.violations == ()
assert any(run_context_scenario(item)[0].violations for item in scenarios)
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_reliability_study.py -k context -q
```

Expected: context study module/functions are missing.

- [ ] **Step 3: Implement the two arms**

`message_level_trim()` is intentionally weak Eval-only code: copy messages, preserve system and newest user, then drop individual oldest non-system messages until `ContextManager.estimate_tokens()` fits. It must not call production `prepare()`.

The production arm constructs `ContextManager(context_window=scenario.context_window, max_output_tokens=scenario.max_output_tokens)`, calls `prepare()`, and validates returned messages. Both arms record `estimated_tokens`, `kept_messages`, `trimmed_messages`, `trimmed_turns`, and `trimmed_rounds`; only the production arm may use production trimming metadata.

- [ ] **Step 4: Confirm GREEN against production context tests**

```powershell
python -m pytest tests/test_reliability_study.py -k context -q
python -m pytest tests/test_context.py -q
```

## Task 3: RQ2 immediate abort versus ordered convergence

**Files:** Create `evals/reliability/abort_study.py`; modify `tests/test_reliability_study.py`.

- [ ] **Step 1: Write RED tests for every abort position**

Freeze:

```python
@dataclass(frozen=True)
class AbortScenario:
    scenario_id: str
    tool_count: int
    abort_index: int


def frozen_abort_scenarios() -> tuple[AbortScenario, ...]:
    return tuple(
        AbortScenario(f"A{count}_{index}", count, index)
        for count in (2, 3, 4)
        for index in range(count)
    )
```

For each scenario, a fake assistant emits ordered IDs from `call-0` through `call-(N-1)`; the handler at `abort_index` returns `ToolResult(ok=False, error_code="USER_ABORTED", message="工具执行被用户中止", details={})`, exactly matching the existing returned-abort contract. Assert the production `AgentLoop` produces exactly N ordered tool results and can accept a next fake model turn, while `immediate_abort_baseline()` omits at least the current/future results.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_reliability_study.py -k abort -q
```

- [ ] **Step 3: Implement the Eval-only baseline and production adapter**

Expose the exact interfaces `immediate_abort_baseline(scenario: AbortScenario) -> ArmResult`, `production_abort_result(scenario: AbortScenario) -> ArmResult`, and `run_abort_scenario(scenario: AbortScenario) -> tuple[ArmResult, ArmResult]`.

The production adapter uses a fresh `AgentLoop`, `FakeModelClient`, and `ToolRegistry` per case. Derive result IDs from captured second-request messages, not a private `_messages` field. Metrics: `declared_calls`, `result_count`, `ordered_result_count`, `next_round_accepted`, `completed_before_abort`, `synthetic_after_abort`.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_reliability_study.py -k abort -q
python -m pytest tests/test_loop.py tests/test_registry.py -q
```

## Task 4: RQ3 vague versus structured error feedback

**Files:** Create `evals/reliability/error_study.py`; modify `tests/test_reliability_study.py`.

- [ ] **Step 1: Write RED deterministic classification tests**

Freeze nine inputs: three error classes (`PATH_OUTSIDE_WORKSPACE`, `COMMAND_DENIED`, `INVALID_ARGUMENTS`) crossed with three retry shapes (`same`, `corrected`, `unrelated`). Use one canonical injected failure per class.

```python
@dataclass(frozen=True)
class ErrorScenario:
    scenario_id: str
    error_code: str
    failed_tool: str
    first_arguments_sha256: str
    retry_shape: str
```

The classifier has the exact interface `classify_retry(*, first_tool: str, first_arguments: object, retry_tool: str, retry_arguments: object) -> str` and returns one of `SAME_FAILURE_RETRY`, `CORRECTED_RETRY`, or `UNRELATED_ACTION`.

Assert structured payloads always expose `ok=false`, stable `error_code`, and bounded `message`, while vague payload is exactly `{"ok": false, "message": "工具执行失败"}`. The deterministic primary metric is `attributable_failure` from observable fields, not a claim about model cognition.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_reliability_study.py -k error -q
```

- [ ] **Step 3: Implement deterministic arms**

Expose the exact interfaces `vague_error_payload() -> dict[str, object]`, `structured_error_payload(result: ToolResult) -> dict[str, object]`, and `run_error_scenario(scenario: ErrorScenario) -> tuple[ArmResult, ArmResult]`.

Use synthetic arguments containing credential-like strings in one test and assert persisted metrics contain only hashes/lengths, never the values. Do not call any model in the mandatory study. A later optional real-model pairing requires a separate manifest, authorization and result namespace; it must not alter these deterministic results.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_reliability_study.py -k error -q
python -m pytest tests/test_registry.py tests/test_policy.py tests/test_audit.py -q
```

## Task 5: Deterministic runner and exclusive report

**Files:** Create `evals/run_reliability_study.py`; modify `tests/test_reliability_study.py`.

- [ ] **Step 1: Write RED report tests**

Assert the CLI:

```text
uses exactly the frozen context/abort/error matrices
sorts rows by mechanism/scenario/arm
computes scenario_manifest_sha256 before running arms
refuses overwrite
is byte-identical across two in-memory generations except no timestamps exist
contains no provider/model/credential fields
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_reliability_study.py -k report -q
```

- [ ] **Step 3: Implement and generate once**

CLI:

```powershell
python -m evals.run_reliability_study --report docs/evidence/reliability-study.json
```

Expected report row count is fixed before execution:

```text
context: 6 scenarios × 2 arms = 12
abort:   9 scenarios × 2 arms = 18
error:   9 scenarios × 2 arms = 18
total: 48 rows
```

Use an exclusive create. Because this experiment has no secret input, the writer may pass an empty synthetic redaction value only if the existing helper supports it; otherwise add a local `open("x", encoding="utf-8")` exclusive writer with the same current-key/Bearer/private-key checks. Do not weaken the shared writer.

- [ ] **Step 4: Verify**

```powershell
python -m pytest tests/test_reliability_study.py -q
python -m evals.run_reliability_study --report docs/evidence/reliability-study.json
python -m pytest -q
python -m compileall -q code_operator evals tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
```

Manually verify the report states raw counts only, includes every failed baseline case, and makes no statistical-significance or real-model-causality claim.

## Task 6: Human review and local checkpoint

- [ ] Update the root v4.5 research checkboxes and add `REVIEW_LOG.md#research-ablation-001` with RED/GREEN commands, exact 48-row manifest hash, full-suite result, scans and limitations.
- [ ] Present the full implementation/evidence diff for `RESEARCH-ABLATION-001` human review.
- [ ] After approval, create local commit `test(research): add deterministic reliability ablations`.
- [ ] Do not push until the full v4.5 section 9.5 package is shown and a new explicit push authorization is received.

## Interpretation boundary

The valid conclusion is limited to the frozen deterministic matrix. The weak baselines are deliberately simplified experimental counterfactuals, not claims about third-party systems or every possible alternative implementation. If a production arm fails, retain that result and enter the separate failure-driven TDD gate; do not edit the scenario after seeing it.
