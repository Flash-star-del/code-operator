# E3 Minimal Terminal Execution Trace — historical TDD execution record

> 本文是 2026-08-29 起草并已执行的 TDD 草案与执行记录，不是要求未来 agent 重新执行的实施指令。代码块保存最初的红绿意图；后续规格复核和 review-driven TDD 已对接口、断言、隐私边界和验证命令作出修正，因此其中的初始示例不一定是最终实现，也不构成可重新执行的权威指令。最终源码、tests 与最新验证命令是唯一权威来源。checkbox 与执行 evidence 保留为历史轨迹。

**Goal:** Add a default-on, privacy-preserving plain-text execution trace to the CLI without coupling presentation to JSONL audit or changing AgentLoop outcomes.

**Architecture:** A new `TerminalTrace` presentation observer formats model-round, tool-result, and run-end events. `AgentLoop` exposes an optional three-method `TraceLike` protocol and isolates observer failures; `run_task()` forwards an optional observer while the CLI constructs one by default. Existing tools remain the source of bounded diff/stdout/stderr data, and the terminal layer applies a smaller marked head-tail bound after redaction.

**Tech Stack:** Python 3.11 standard library, existing dataclasses and `Redactor`, pytest, plain stdout; no new runtime or development dependency.

---

## File map and ownership

- Create `code_operator/trace.py`: pure terminal formatting, argument-body omission, redaction, marked head-tail bounds, sink failure containment.
- Create `tests/test_trace.py`: formatter/privacy/output-contract unit tests.
- Modify `code_operator/loop.py`: optional trace protocol and three event emission points only.
- Modify `tests/test_loop.py`: event ordering, terminal-stop coverage, and observer failure isolation.
- Modify `code_operator/__main__.py`: optional `run_task()` trace forwarding, default CLI construction, explicit approval result.
- Modify `tests/test_cli.py`: default-on wiring, approval ALLOW/DENY, and existing usage-output compatibility.
- Modify `README.md`: document the verified ordinary-terminal behavior and remove the brittle global test-count sentence.
- Modify `DESIGN.md`: record E3 responsibilities, privacy boundary, event ordering, and verified limitations.
- Modify `docs/superpowers/plans/2026-08-29-terminal-trace-implementation.md`: check boxes and record actual red/green evidence during execution.
- Modify `REVIEW_LOG.md` only after the project author approves the final staged E3 scope.

No dependency file, Provider protocol, tool implementation, audit schema, context logic, Eval fixture, or Git history may change.

Because every commit requires prior human review, Tasks 1–4 deliberately leave their verified changes uncommitted. Task 5 creates the single planned `feat(cli): clarify terminal execution trace` commit only after `E3-001` approval.

### Task 1: Build the bounded and redacted terminal formatter

**执行证据（2026-08-29）：** 首次红灯为 `ModuleNotFoundError: No module named 'code_operator.trace'`。规格复核推动正式 `ToolCall + ToolResult` 接口、普通文本流层级和完整命令矩阵；质量复核又以真实红灯关闭未注册敏感键、脱敏工具名控制流、病态 JSON 和小预算截断问题。最终 `tests/test_trace.py` 为 `34 passed`，规格结论 `SPEC_COMPLIANT`，质量结论 `QUALITY_APPROVED`。

**Files:**
- Create: `tests/test_trace.py`
- Create: `code_operator/trace.py`

- [x] **Step 1: Write the formatter contract tests before the module exists**

Create `tests/test_trace.py` with these tests:

```python
from __future__ import annotations

import json

import pytest

from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor
from code_operator.trace import MAX_TRACE_DETAIL_CHARS, TerminalTrace


def captured_trace(secret: str = "") -> tuple[TerminalTrace, list[str]]:
    output: list[str] = []
    return TerminalTrace(Redactor([secret]), write=output.append), output


def test_model_and_run_lines_use_stable_contract() -> None:
    trace, output = captured_trace()

    trace.record_model_round(1, 2, True)
    trace.record_model_round(2, 0, False)
    trace.record_run(RunResult("COMPLETED", "done", 2, 2, None, 100))

    assert output == [
        "[模型 1] tool_calls=2 usage=available",
        "[模型 2] tool_calls=0 usage=unavailable",
        "[结束] stop_reason=COMPLETED usage=unavailable",
    ]


def test_arguments_omit_file_bodies_and_redact_nested_secrets() -> None:
    secret = "synthetic-terminal-secret"
    trace, output = captured_trace(secret)
    call = ToolCall(
        "edit",
        "edit_file",
        json.dumps(
            {
                "path": f"目录/{secret}/价格.py",
                "old_text": f"TOKEN={secret}",
                "new_text": f"Bearer {secret}",
                "metadata": {"API_KEY": secret},
            },
            ensure_ascii=False,
        ),
    )
    result = ToolResult(
        "edit",
        "edit_file",
        True,
        None,
        "edited",
        {"diff": "--- a/价格.py\n+++ b/价格.py\n-old\n+new\n"},
    )

    trace.record_tool(call, result)
    rendered = "\n".join(output)

    assert secret not in rendered
    assert "old_text" in rendered and "new_text" in rendered
    assert rendered.count("<omitted; chars=") == 2
    assert '"metadata":{"API_KEY":"<REDACTED>"}' in rendered
    assert "[结果] edit_file ok=true error_code=-" in rendered
    assert "+++ b/价格.py" in rendered


@pytest.mark.parametrize(
    ("arguments_raw", "private_text", "marker"),
    [
        ('{"content":"BROKEN_PRIVATE_BODY"', "BROKEN_PRIVATE_BODY", "invalid-json"),
        ('"SCALAR_PRIVATE_BODY"', "SCALAR_PRIVATE_BODY", "non-object-json"),
    ],
)
def test_invalid_argument_shapes_never_echo_raw_input(
    arguments_raw: str,
    private_text: str,
    marker: str,
) -> None:
    trace, output = captured_trace()

    trace.record_tool(
        ToolCall("bad", "write_file", arguments_raw),
        ToolResult("bad", "write_file", False, "INVALID_ARGUMENTS", "bad", {}),
    )

    rendered = "\n".join(output)
    assert private_text not in rendered
    assert marker in rendered


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
def test_write_tools_show_marked_head_and_tail_of_long_diff(tool_name: str) -> None:
    trace, output = captured_trace()
    long_diff = "HEAD_OF_DIFF\n" + ("middle\n" * MAX_TRACE_DETAIL_CHARS) + "TAIL_OF_DIFF\n"

    trace.record_tool(
        ToolCall("write", tool_name, '{"path":"src/很长的路径.py"}'),
        ToolResult("write", tool_name, True, None, "written", {"diff": long_diff}),
    )

    rendered = "\n".join(output)
    assert "HEAD_OF_DIFF" in rendered
    assert "TAIL_OF_DIFF" in rendered
    assert "<truncated; original_chars=" in rendered


def test_run_command_shows_exit_timeout_and_bounded_stdout_stderr() -> None:
    trace, output = captured_trace("command-secret")
    stdout = "STDOUT_HEAD\n" + ("x" * MAX_TRACE_DETAIL_CHARS) + "\nSTDOUT_TAIL"
    stderr = "TOKEN=command-secret\n测试失败\n"

    trace.record_tool(
        ToolCall("test", "run_command", '{"argv":["pytest","-q"]}'),
        ToolResult(
            "test",
            "run_command",
            False,
            "COMMAND_FAILED",
            "failed",
            {
                "exit_code": 1,
                "timed_out": False,
                "stdout": stdout,
                "stderr": stderr,
            },
        ),
    )

    rendered = "\n".join(output)
    assert "[命令] exit_code=1 timed_out=false" in rendered
    assert "STDOUT_HEAD" in rendered and "STDOUT_TAIL" in rendered
    assert "测试失败" in rendered
    assert "command-secret" not in rendered
    assert "TOKEN=<REDACTED>" in rendered


@pytest.mark.parametrize("tool_name", ["read_file", "grep", "list_dir"])
def test_read_only_tools_never_repeat_result_payload(tool_name: str) -> None:
    trace, output = captured_trace()
    private_payload = "PRIVATE_RESULT_PAYLOAD"

    trace.record_tool(
        ToolCall("read", tool_name, '{"path":"src/示例.py"}'),
        ToolResult(
            "read",
            tool_name,
            True,
            None,
            "done",
            {"content": private_payload, "text": private_payload, "matches": [private_payload]},
        ),
    )

    rendered = "\n".join(output)
    assert f"[工具] {tool_name}" in rendered
    assert f"[结果] {tool_name} ok=true error_code=-" in rendered
    assert private_payload not in rendered


def test_unknown_tool_uses_only_the_minimal_common_result() -> None:
    trace, output = captured_trace()

    trace.record_tool(
        ToolCall("future", "future_tool", "{}"),
        ToolResult("future", "future_tool", True, None, "done", {"raw": "PRIVATE"}),
    )

    rendered = "\n".join(output)
    assert "future_tool" in rendered
    assert "PRIVATE" not in rendered


def test_sink_failure_is_contained_and_disables_future_writes() -> None:
    attempts = 0

    def broken_write(_text: str) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("terminal unavailable")

    trace = TerminalTrace(Redactor([]), write=broken_write)

    trace.record_model_round(1, 0, False)
    trace.record_run(RunResult("COMPLETED", "done", 1, 0, None, 10))

    assert trace.output_failed is True
    assert attempts == 1
```

- [x] **Step 2: Run the new tests and capture the expected red state**

Run:

```powershell
python -m pytest tests/test_trace.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'code_operator.trace'`. Record this command and failure signature in this plan under Task 1 before creating the module.

- [x] **Step 3: Implement the minimal formatter**

Create `code_operator/trace.py`:

```python
from __future__ import annotations

import json
from collections.abc import Callable

from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor


MAX_ARGUMENT_SUMMARY_CHARS = 500
MAX_TRACE_DETAIL_CHARS = 4_000
_FILE_BODY_ARGUMENTS = {"content", "old_text", "new_text"}
_WRITE_TOOLS = {"write_file", "edit_file"}
_READ_ONLY_TOOLS = {"read_file", "grep", "list_dir"}


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = f"\n... <truncated; original_chars={len(text)}> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _indented_block(label: str, text: str) -> list[str]:
    content = text if text else "<empty>"
    return [f"  {label}:", *(f"  {line}" for line in content.splitlines())]


class TerminalTrace:
    def __init__(
        self,
        redactor: Redactor,
        *,
        write: Callable[[str], None] = print,
    ) -> None:
        self._redactor = redactor
        self._write = write
        self.output_failed = False

    def _emit(self, lines: list[str]) -> None:
        if self.output_failed:
            return
        try:
            self._write("\n".join(lines))
        except Exception:
            self.output_failed = True

    def _argument_summary(self, arguments_raw: str) -> str:
        try:
            decoded = json.loads(arguments_raw)
        except (json.JSONDecodeError, TypeError):
            cleaned = f"<invalid-json; chars={len(arguments_raw)}>"
        else:
            if not isinstance(decoded, dict):
                cleaned = f"<non-object-json; type={type(decoded).__name__}>"
            else:
                summary = {
                    key: (
                        f"<omitted; chars={len(value)}>"
                        if key in _FILE_BODY_ARGUMENTS and isinstance(value, str)
                        else "<omitted>"
                        if key in _FILE_BODY_ARGUMENTS
                        else value
                    )
                    for key, value in decoded.items()
                }
                cleaned = json.dumps(
                    self._redactor.redact_object(summary),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
        return _bounded(cleaned, MAX_ARGUMENT_SUMMARY_CHARS)

    def record_model_round(
        self,
        round_number: int,
        tool_call_count: int,
        usage_available: bool,
    ) -> None:
        usage = "available" if usage_available else "unavailable"
        self._emit(
            [f"[模型 {round_number}] tool_calls={tool_call_count} usage={usage}"]
        )

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        tool_name = self._redactor.redact(call.name)
        error_code = (
            self._redactor.redact(result.error_code)
            if isinstance(result.error_code, str) and result.error_code
            else "-"
        )
        lines = [
            f"[工具] {tool_name} 参数={self._argument_summary(call.arguments_raw)}",
            f"[结果] {tool_name} ok={_bool_text(result.ok)} error_code={error_code}",
        ]
        details = result.details if isinstance(result.details, dict) else {}
        if call.name in _WRITE_TOOLS and result.ok:
            diff = details.get("diff")
            if isinstance(diff, str) and diff:
                clean_diff = _bounded(
                    self._redactor.redact(diff), MAX_TRACE_DETAIL_CHARS
                )
                lines.extend(f"  {line}" for line in clean_diff.splitlines())
        elif call.name == "run_command":
            exit_code = details.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                exit_code = "-"
            timed_out = details.get("timed_out")
            timed_out_text = _bool_text(timed_out) if isinstance(timed_out, bool) else "-"
            lines.append(
                f"[命令] exit_code={exit_code} timed_out={timed_out_text}"
            )
            for label in ("stdout", "stderr"):
                value = details.get(label)
                if isinstance(value, str):
                    clean = _bounded(
                        self._redactor.redact(value), MAX_TRACE_DETAIL_CHARS
                    )
                else:
                    clean = "<unavailable>"
                lines.extend(_indented_block(label, clean))
        elif call.name in _READ_ONLY_TOOLS:
            pass
        self._emit(lines)

    def record_run(self, result: RunResult) -> None:
        usage = (
            "available" if result.provider_total_tokens is not None else "unavailable"
        )
        stop_reason = self._redactor.redact(result.status)
        self._emit([f"[结束] stop_reason={stop_reason} usage={usage}"])
```

- [x] **Step 4: Run the formatter tests and confirm green**

Run:

```powershell
python -m pytest tests/test_trace.py -q
```

Observed after review-driven TDD additions: `34 passed`; synthetic secrets, unregistered credential-key values, and private bodies do not appear in captured output.

- [x] **Step 5: Check the formatter diff but do not commit**

Run:

```powershell
git diff --check -- code_operator/trace.py tests/test_trace.py
git diff --stat -- code_operator/trace.py tests/test_trace.py
```

Expected: both files are the only Task 1 files, `git diff --check` exits zero. Leave them uncommitted for the consolidated E3-001 review.

### Task 2: Emit trace events from AgentLoop without changing outcomes

**执行证据（2026-08-29）：** 三个新测试先因 `AgentLoop.__init__()` 不接受 `trace` 失败。最小接入后，规格复核要求把空注册表改为真实成功 `write_file`，并把工具 `ok` 与整次 usage 纳入事件断言；修正后 `tests/test_loop.py` 为 `26 passed`。最终规格结论 `SPEC_COMPLIANT`，质量结论 `QUALITY_APPROVED`。

**Files:**
- Modify: `tests/test_loop.py`
- Modify: `code_operator/loop.py`

- [x] **Step 1: Add failing observer-order and failure-isolation tests**

Append to `tests/test_loop.py`:

```python
class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def record_model_round(
        self,
        round_number: int,
        tool_call_count: int,
        usage_available: bool,
    ) -> None:
        self.events.append(
            ("model", round_number, tool_call_count, usage_available)
        )

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        self.events.append(("tool", call.id, result.tool_call_id, result.ok))

    def record_run(self, result: RunResult) -> None:
        self.events.append(
            ("run", result.status, result.provider_total_tokens is not None)
        )


def test_trace_receives_model_tool_and_single_run_events_in_order() -> None:
    call = ToolCall("trace-write", "write_file", '{"path":"x","content":"y"}')
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls", total_tokens=4),
            turn(content="done", total_tokens=None),
        ]
    )
    trace = RecordingTrace()

    result = AgentLoop(
        model,
        ToolRegistry({"write_file": successful_handler}),
        trace=trace,
    ).run("write")

    assert result.status == "COMPLETED"
    assert trace.events == [
        ("model", 1, 1, True),
        ("tool", "trace-write", "trace-write", True),
        ("model", 2, 0, False),
        ("run", "COMPLETED", False),
    ]


def test_trace_exceptions_do_not_change_tool_loop_result() -> None:
    call = ToolCall("trace-failure", "write_file", '{"path":"x","content":"y"}')
    model = FakeModelClient(
        [turn(calls=[call], finish_reason="tool_calls"), turn(content="done")]
    )

    class BrokenTrace:
        def record_model_round(self, *_: object) -> None:
            raise OSError("broken model output")

        def record_tool(self, *_: object) -> None:
            raise OSError("broken tool output")

        def record_run(self, *_: object) -> None:
            raise OSError("broken run output")

    result = AgentLoop(
        model,
        ToolRegistry({"write_file": successful_handler}),
        trace=BrokenTrace(),
    ).run("write")

    assert result.status == "COMPLETED"
    assert result.model_rounds == 2
    assert result.tool_calls == 1


def test_context_limit_still_emits_exactly_one_run_event() -> None:
    model = FakeModelClient([AssertionError("不得请求模型")])
    trace = RecordingTrace()

    result = AgentLoop(
        model,
        ToolRegistry({}),
        context_window=400,
        max_output_tokens=200,
        system_prompt="short system",
        trace=trace,
    ).run("x" * 2_000)

    assert result.status == "CONTEXT_LIMIT"
    assert trace.events == [("run", "CONTEXT_LIMIT", False)]
```

Also add `RunResult` to the existing `code_operator.models` import in this test file.

- [x] **Step 2: Run the three tests and verify the red state**

Run:

```powershell
python -m pytest tests/test_loop.py::test_trace_records_model_tool_model_run_with_usage_availability tests/test_loop.py::test_broken_trace_does_not_change_successful_tool_loop tests/test_loop.py::test_context_limit_records_only_one_run_event_without_model_round -q
```

Expected: all three fail because `AgentLoop.__init__()` does not accept `trace`.

- [x] **Step 3: Add the protocol, optional constructor field, and isolated event calls**

In `code_operator/loop.py`, add this protocol next to `AuditLike`:

```python
class TraceLike(Protocol):
    def record_model_round(
        self,
        round_number: int,
        tool_call_count: int,
        usage_available: bool,
    ) -> None: ...

    def record_tool(self, call: ToolCall, result: ToolResult) -> None: ...

    def record_run(self, result: RunResult) -> None: ...
```

Add `trace: TraceLike | None = None` after `audit` in `AgentLoop.__init__()` and store `self._trace = trace`.

In the nested `result()` function, after the independent audit call and before returning, add:

```python
            if self._trace is not None:
                try:
                    self._trace.record_run(run_result)
                except Exception:
                    pass
```

Immediately after updating the current turn's usage and before appending the assistant replay message, add:

```python
            if self._trace is not None:
                try:
                    self._trace.record_model_round(
                        model_rounds,
                        len(turn.tool_calls),
                        turn.usage is not None and turn.usage.total_tokens is not None,
                    )
                except Exception:
                    pass
```

Inside the paired tool-result loop, after the independent audit call and before stop/failure accounting, add:

```python
                    if self._trace is not None:
                        try:
                            self._trace.record_tool(call, tool_result)
                        except Exception:
                            pass
```

Do not refactor audit and trace into a shared sink: each must retain its own exception boundary.

- [x] **Step 4: Run focused and existing loop tests**

Run:

```powershell
python -m pytest tests/test_loop.py -q
```

Expected: all existing loop tests plus the 3 new trace tests pass; tool-result pairing and stop statuses remain unchanged.

- [x] **Step 5: Check Task 2 scope but do not commit**

Run:

```powershell
git diff --check -- code_operator/loop.py tests/test_loop.py
git diff --stat -- code_operator/loop.py tests/test_loop.py
```

Expected: zero format errors and no files outside Task 2 in this scoped stat.

### Task 3: Wire the trace into run_task and the CLI, including ASK decisions

**执行证据（2026-08-29）：** 四个初始测试先暴露审批 marker 缺失、CLI 未传 trace 和 `run_task` 不支持 trace。规格复核进一步锁定空输入默认拒绝、Ctrl-C 不伪造、配置 Key 确实进入脱敏器、空任务/配置错误短路、完整汇总和三类退出码；最终 `tests/test_cli.py` 为 `12 passed`，指定回归为 `79 passed`。规格结论 `SPEC_COMPLIANT`，质量结论 `QUALITY_APPROVED`。

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_loop.py`
- Modify: `code_operator/__main__.py`

- [x] **Step 1: Add failing CLI and run_task tests**

Update imports in `tests/test_cli.py` to include `Path`, `pytest`, `ProviderConfig`, `TerminalTrace`, and `_interactive_approval`. Replace the two `load_provider_config` fakes that return `object()` with a real synthetic `ProviderConfig` so CLI construction can use `api_key`:

```python
TEST_CONFIG = ProviderConfig(
    api_key="synthetic-cli-secret",
    base_url="https://provider.example/v1",
    model="test-model",
)
```

Append these tests:

```python
@pytest.mark.parametrize(
    ("answer", "expected", "marker"),
    [("y", True, "[审批] ALLOW"), ("", False, "[审批] DENY")],
)
def test_interactive_approval_prints_explicit_decision(
    answer: str,
    expected: bool,
    marker: str,
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    approved = _interactive_approval(["python", "-m", "pytest"], tmp_path)

    assert approved is expected
    output = capsys.readouterr().out
    assert marker in output


def test_cli_enables_terminal_trace_by_default(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_run_task(*_args: object, **kwargs: object) -> RunResult:
        trace = kwargs["trace"]
        assert isinstance(trace, TerminalTrace)
        captured["trace"] = trace
        trace.record_model_round(1, 0, False)
        result = RunResult("COMPLETED", "done", 1, 0, None, 100)
        trace.record_run(result)
        return result

    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config", lambda **_: TEST_CONFIG
    )
    monkeypatch.setattr("code_operator.__main__.run_task", fake_run_task)

    assert main(["task"]) == 0
    output = capsys.readouterr().out
    assert "trace" in captured
    assert "[模型 1] tool_calls=0 usage=unavailable" in output
    assert "[结束] stop_reason=COMPLETED usage=unavailable" in output
    assert "供应商用量不完整" in output
```

Append to `tests/test_loop.py`:

```python
def test_run_task_forwards_optional_trace_without_coupling_audit(
    tmp_path: Path,
) -> None:
    trace = RecordingTrace()
    config = ProviderConfig(
        api_key="synthetic-run-task-secret",
        base_url="https://provider.example/v1",
        model="test-model",
    )

    result = run_task(
        config,
        workspace=tmp_path,
        task="finish",
        approve=lambda _argv, _cwd: False,
        client=FakeModelClient([turn(content="done")]),
        environment={},
        trace=trace,
    )

    assert result.status == "COMPLETED"
    assert trace.events == [
        ("model", 1, 0, True),
        ("run", "COMPLETED", True),
    ]
    assert (tmp_path / ".code-operator" / "audit.jsonl").exists()
```

- [x] **Step 2: Run the new tests and verify red**

Run:

```powershell
python -m pytest tests/test_cli.py::test_interactive_approval_prints_explicit_decision_marker tests/test_cli.py::test_cli_passes_redacting_terminal_trace_and_preserves_summary tests/test_loop.py::test_run_task_passes_trace_and_keeps_jsonl_audit -q
```

Expected: the approval assertions fail because no stable decision is printed, the CLI test fails because no `trace` is passed, and the run_task test fails because the keyword is unsupported.

- [x] **Step 3: Add the minimal CLI wiring**

In `code_operator/__main__.py`:

```python
from code_operator.loop import AgentLoop, ModelLike, TraceLike
from code_operator.trace import TerminalTrace
```

Add `trace: TraceLike | None = None` to `run_task()` after `auto_approve_tests`, and pass `trace=trace` to `AgentLoop`.

Change `_interactive_approval()` to:

```python
def _interactive_approval(argv: list[str], cwd: Path) -> bool:
    print("\n命令需要人工批准：")
    print(f"  参数：{json.dumps(argv, ensure_ascii=False)}")
    print(f"  工作目录：{cwd}")
    answer = input("仅允许本次执行？[y/N] ").strip().casefold()
    approved = answer in {"y", "yes", "允许"}
    print(f"[审批] {'ALLOW' if approved else 'DENY'}")
    return approved
```

In `main()`, pass the default observer to `run_task()`:

```python
            trace=TerminalTrace(Redactor([config.api_key])),
```

Do not add a parser flag and do not construct a trace for `/exit`, empty tasks, or invalid configuration.

- [x] **Step 4: Run all CLI, loop, audit, and trace tests**

Run:

```powershell
python -m pytest tests/test_trace.py tests/test_cli.py tests/test_loop.py tests/test_audit.py -q
```

Expected: every selected test passes; existing final text, usage distinction, JSONL schema, `/exit`, loop pairing, and stop statuses remain green.

- [x] **Step 5: Check Task 3 diff but do not commit**

Run:

```powershell
git diff --check -- code_operator/__main__.py tests/test_cli.py tests/test_loop.py
git diff --stat -- code_operator/__main__.py tests/test_cli.py tests/test_loop.py
```

Expected: zero format errors. Do not commit before the complete E3 review.

### Task 4: Document verified behavior and perform width/readability checks

**执行证据（2026-08-30）：** 原 Task 4 记录中的 `316 passed` 及后续 `339 passed` 均是当时的历史快照；终端控制字符回归加入后，以 `python -m pytest --collect-only -q` 重新核实当前总数为 `348 tests collected`，再运行完整套件（见 Task 5 的最新命令）。重新执行不落盘的 UTF-8 合成预览：模型事件、长中文路径、超过 4000 字符的 `edit_file` diff（保留 `HEAD`/`TAIL` 与 `original_chars=10065`）、`run_command` 的 `exit_code=1`、stdout `3 failed, 2 passed`、中文 stderr，以及通过 `_interactive_approval` 和 `patch('builtins.input', return_value='y')` 产生的真实 `[审批] ALLOW` 均可见；argv/cwd 中的合成 secret 未泄露，结束行显示 `usage=unavailable` 与 `stop_reason=COMPLETED`。宽度 100 与 40 均用 Python `textwrap` 查看并通过同一组字段断言；这只是 UTF-8 文本、字符宽度和自然折行的合成检查，不模拟中文双宽显示单元、真实终端编码或实际换行。

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `docs/superpowers/plans/2026-08-29-terminal-trace-implementation.md`

- [x] **Step 1: Run the complete verification before writing claims**

Run:

```powershell
python -m pytest --collect-only -q
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
```

Expected: collect-only reports `348 tests collected`; the current full suite reports `348 passed`; compileall exits zero with no output. If the count differs, investigate the collected test set before changing documentation—do not copy an unexplained count.

- [x] **Step 2: Add the exact README terminal-trace section**

In `README.md`, after “安装与运行”的 approval paragraph, add:

```markdown
## 普通终端执行轨迹

CLI 默认使用稳定纯文本展示模型轮次、工具名称、脱敏参数摘要、人工审批结果、文件 diff、命令退出码与有限 stdout/stderr、usage 可用性和最终停止原因，不需要额外开关。文件正文参数只显示字符数；读取、搜索和目录工具不会在终端重复打印结果正文。长 diff 与命令输出保留头尾并明确标记原始字符数。

终端轨迹与 `.code-operator/audit.jsonl` 相互独立，只面向本次操作者且不落盘。它不展示 reasoning、原始模型消息、请求 ID 或认证信息；输出失败也不会改变 AgentLoop 的真实结果。普通文本允许由窄终端自然换行，不提供全屏 TUI、动画、鼠标或流式 tool-call 拼接。
```

In “开发状态”, replace `当前 Windows/Python 3.11 上 269 项离线测试通过；` with `当前 Windows/Python 3.11 上完整离线测试通过；` so future test additions do not make the top-level status immediately stale.

- [x] **Step 3: Add the exact DESIGN E3 section**

Insert after “E2 黄金 Eval 验证” and before “提示、工具描述与顺序执行”:

```markdown
## E3 最小普通终端执行轨迹验证

E3 在 JSONL 审计之外增加独立的 `TerminalTrace` 展示观察器。AgentLoop 只在成功解析模型响应、形成工具结果和建立最终 `RunResult` 三个位置发送结构化事件；每个通知都有独立异常边界，因此终端不可写不会改变消息配对、工具结果、审计记录或停止原因。库调用和 Eval 可以不传观察器，普通 CLI 则默认启用，不新增开关。

模型事件只显示轮次、tool call 数和该轮 usage 是否可用；写入工具显示脱敏的有限 unified diff；命令显示退出码、超时以及 stdout/stderr 头尾；读取、搜索和目录工具不重复输出结果正文。参数中的文件正文始终替换为字符数摘要，所有展示文本再次经过统一脱敏，超限内容使用包含原始字符数的头尾标记。人工 ASK 明确显示 `ALLOW` 或 `DENY`，不改变默认拒绝语义。

自动化测试覆盖中文、长路径、非法参数、文件正文省略、合成凭据、超长 diff、命令失败/超时、读取类结果隐藏、usage 缺失、结束事件和输出 sink 异常。普通与窄宽度人工样例确认关键字段允许自然折行但仍可判断结果。本实现使用标准输出，不包含 Rich、ANSI 颜色、全屏 TUI、流式 tool-call 拼接或完整运行日志。
```

- [x] **Step 4: Generate and inspect common-width and narrow-width samples**

Run this read-only preview twice, first with width 100 and then width 40. The PowerShell here-string is piped to Python stdin and never written to disk:

```powershell
& {
$preview = @'
import contextlib, io, json, sys, textwrap
from pathlib import Path
from unittest.mock import patch
from code_operator.__main__ import _interactive_approval
from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor
from code_operator.trace import TerminalTrace

width = int(sys.argv[1])
secret = "synthetic-preview-secret"
redactor = Redactor([secret])
output = []
trace = TerminalTrace(redactor, write=output.append)
newline = chr(10)
path = "src/\u975e\u5e38\u957f\u7684\u4e2d\u6587\u76ee\u5f55\u540d\u79f0/\u8ba2\u5355\u4ef7\u683c\u6d41\u6c34\u7ebf.py"
diff = "HEAD: --- a/" + path + newline + "+++ b/" + path + newline
diff += newline.join(f"-\u65e7\u4ef7\u683c\u884c{i:04d}{newline}+\u65b0\u4ef7\u683c\u884c{i:04d}" for i in range(499))
diff += newline + "TAIL: +\u65b0\u4ef7\u683c\u884c0499"
approval = io.StringIO()
with contextlib.redirect_stdout(approval), patch("builtins.input", return_value="y"):
    approved = _interactive_approval(["python", "-c", f"Bearer {secret}", f"TOKEN={secret}"], Path(f"C:/\u957f\u4e2d\u6587\u5de5\u4f5c\u533a/{secret}/\u9879\u76ee"), redactor=redactor)
trace.record_model_round(1, 2, True)
trace.record_tool(ToolCall("e", "edit_file", json.dumps({"path": path, "old_text": "\u65e7\u6b63\u6587", "new_text": "\u65b0\u6b63\u6587"}, ensure_ascii=False)), ToolResult("e", "edit_file", True, None, "ok", {"diff": diff}))
trace.record_tool(ToolCall("c", "run_command", json.dumps({"argv": ["pytest", "-q"]})), ToolResult("c", "run_command", False, "COMMAND_FAILED", "bad", {"exit_code": 1, "timed_out": False, "stdout": "3 failed, 2 passed", "stderr": "\u6d4b\u8bd5\u5931\u8d25\uff1a\u4ef7\u683c\u4e0d\u4e00\u81f4"}))
trace.record_run(RunResult("COMPLETED", "done", 1, 2, None, 100))
rendered = approval.getvalue() + newline.join(output)
required = ["[\u6a21\u578b 1]", path, "HEAD", "TAIL", "original_chars=", "exit_code=1", "3 failed, 2 passed", "\u6d4b\u8bd5\u5931\u8d25", "[\u5ba1\u6279] ALLOW", "usage=unavailable", "stop_reason=COMPLETED"]
assert approved and len(diff) > 4000 and secret not in rendered and all(item in rendered for item in required)
print(f"PREVIEW width={width} logical_checks=ok diff_original_chars={len(diff)} secret_leaked=False")
print(newline.join(textwrap.fill(line, width=width, replace_whitespace=False, drop_whitespace=False) if line else "" for line in rendered.splitlines()))
'@
$preview | python -X utf8 - 100
$preview | python -X utf8 - 40
}
```

Inspect both outputs and confirm all of these remain visible: full logical path across wrapped lines, Chinese diff head/tail, `original_chars=10065`, `exit_code=1`, stdout `3 failed, 2 passed`, Chinese stderr failure text, `[审批] ALLOW`, `usage=unavailable`, and `stop_reason=COMPLETED`; confirm the synthetic secret is absent. Record the two widths and conclusion under Task 4 in this plan. Do not claim that arbitrary terminal emulators were validated.

- [x] **Step 5: Re-run documentation and focused checks**

Run:

```powershell
git diff --check
python -m pytest tests/test_trace.py tests/test_cli.py tests/test_loop.py -q
```

Expected: no whitespace errors and all selected tests pass.

### Task 5: Full review, human gate, one local commit, and separate push gate

**执行证据（2026-08-30，人工审核前快照）：** formatter 定向最初为 `54 passed`；最终质量审查随后以真实注入样例发现终端控制字符可伪造事件，TDD 红灯为 `9 failed, 67 passed`，补充 U+2028/U+2029 边界后另有 `1 failed`。最小修复统一采用“脱敏、终端安全编码、再截断”，修复后 formatter 为 `58 passed`，trace/CLI/loop 定向组合为 `105 passed in 0.36s`，全量为 `348 passed in 48.14s`，`compileall` 与完整 pending diff check 退出码 0；当前合成密钥值、私钥头、Authorization Bearer 值和禁止的 `.env`/audit/cache/PID/tmp/log 待提交文件名命中均为 0，依赖差异为空。终端安全修复规格复核结论为 `FIX_SPEC_APPROVED`，完整组合质量复核结论为 `FINAL_QUALITY_APPROVED`，均无 Critical/Important 问题；项目作者 `E3-001` 审核仍是后续门禁，本段不宣称已经提交或获准推送。

**Files:**
- Modify after approval: `REVIEW_LOG.md`
- Review: every E3 file listed in the file map

- [x] **Step 1: Run fresh final verification**

Run, in this order:

```powershell
python -m pytest --collect-only -q
python -m pytest tests/test_trace.py -q
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
git diff --check
git status --short
```

Expected: `58 passed` for the current formatter tests, `348 passed` for the current full suite confirmed by a fresh collect-only snapshot, compileall and diff check exit zero, and status contains only the intended E3 implementation/test/docs/plan files.

- [x] **Step 2: Verify dependency, privacy, and temporary-file boundaries**

Run:

```powershell
git diff -- requirements.txt requirements-dev.txt
git status --short
```

Expected: no dependency diff. Then scan the complete pending diff for the current `CODE_OPERATOR_API_KEY` by exact value without printing it; report only the numeric hit count. Scan staged names for `.env`, `audit.jsonl`, caches, PID files, `.tmp`, private keys, raw Provider responses, and generated preview files. Expected counts: zero. Confirm `code_operator/client.py`, tool implementations, policy, context, Eval fixtures/reports, and dependency files are unchanged.

- [x] **Step 3: Perform two-stage review before asking the user**

Use the already selected subagent-driven workflow:

1. A specification reviewer checks the complete pending diff against `docs/superpowers/specs/2026-08-29-terminal-trace-design.md`, the PDF hard requirements, DESIGN v4.5 correction, and E3 plan checkboxes.
2. After all spec findings are closed, a code-quality reviewer checks privacy, bounds, observer failure isolation, output stability, test credibility, and unintended coupling to JSONL audit.

Any fix must repeat a focused red—green cycle when behavior changes, followed by the full verification in Step 1. Do not accept reviewer claims without inspecting the diff and rerunning evidence locally.

- [x] **Step 4: Stage the exact E3 scope and request human review**

Stage only the intended files, run `git diff --cached --check`, save a complete binary-capable staged patch outside the repository, compute its SHA-256, and present:

- staged file list and stat;
- TDD red signatures and final green commands;
- common/narrow-width findings;
- specification and quality review conclusions;
- exact-key and forbidden-file scan counts;
- remaining platform/UI limitations;
- patch path and SHA-256.

Stop and request the exact approval `E3-001 人工审核通过`. This approval does not authorize pushing.

- [x] **Step 5: Record approval and create the single local implementation commit**

Only after the exact approval, append an `E3-001` entry to `REVIEW_LOG.md`, re-stage it, re-run `git diff --cached --check`, and commit:

```powershell
git commit -m "feat(cli): clarify terminal execution trace" -m "Human-Review: approved" -m "Review-Scope: E3-001; full-staged-diff" -m "Review-Record: REVIEW_LOG.md#e3-001"
```

Verify the resulting commit hash, trailers, file list, and clean working tree. Do not amend any pushed history.

- [ ] **Step 6: Prepare but do not cross the §9.5 push gate**

Before any remote action, display a fresh packet containing:

1. Git status;
2. commits in `origin/main..HEAD` (including design commit `68e470b` and the E3 implementation commit if neither is yet remote);
3. complete cumulative change range and review conclusion;
4. final tests, compileall, manual width samples, and any remote-independent probes;
5. credential and temporary-file scan results;
6. documentation/implementation/open-source-boundary consistency.

Wait for a new exact `允许推送` or `可以 push`. After authorization, use an ordinary non-force push, verify local HEAD, `origin/main`, and `git ls-remote` agree, then check the public `offline-tests` GitHub Actions run to completion. If CI fails, fix locally through a new reviewed commit; never rewrite the pushed commits.
