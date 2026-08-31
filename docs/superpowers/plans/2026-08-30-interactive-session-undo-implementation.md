# E4 Bounded Interactive Session and File Undo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-process conversational CLI session with `/undo`, `/new`, and `/exit` while preserving the existing one-shot API, protocol, safety, and evaluation behavior.

**Architecture:** Make `AgentLoop` re-entrant over a protocol-valid multi-user history, place resource ownership and local commands in a new `AgentSession`, and record only successful direct `write_file`/`edit_file` mutations in a bounded in-memory `ChangeJournal`. Keep Chat Completions message replay local, keep all six model tool schemas unchanged, and retain the existing `run_task()` as a one-shot compatibility wrapper.

**Tech Stack:** Python 3.11, standard library `dataclasses/pathlib/hashlib/difflib/tempfile/os`, existing `httpx`, `pytest`, and existing code-operator policy/redaction/trace/audit primitives.

---

## Execution constraints

- Execute in the current `main` checkout because the project author explicitly authorized local changes and semantic commits in this workspace; do not create or push a remote branch.
- Do not push. A later push requires the complete v4.5 section 9.5 gate and a new explicit “允许推送”.
- The approved design requires one removable implementation commit. Keep all task changes uncommitted until the complete E4 staged diff receives `E4-001` human approval.
- For every production behavior, write the named failing test first, run it and record the expected failure, then add only the minimal implementation and rerun.
- Do not make a real API call. A new `kimi-k3` session probe requires separate data-egress authorization.
- Do not add runtime or development dependencies, model tools, JSONL audit fields, Rich/TUI output, persistent sessions, `/history`, or command-side-effect rollback.

## File responsibility map

- Create `code_operator/journal.py`: immutable change/undo result types and bounded in-memory stack only.
- Create `code_operator/session.py`: session composition, client ownership, repeated task execution, pending undo notices, reset, and close.
- Modify `code_operator/tools/filesystem.py`: record successful direct mutations and perform guarded restore/delete operations.
- Modify `code_operator/tools/registry.py`: expose sequential result iteration so interrupted multi-call turns retain completed results.
- Modify `code_operator/context.py`: parse and trim multiple complete user turns without splitting assistant/tool groups.
- Modify `code_operator/loop.py`: persist history across `run()` calls, reset per-request counters, reset history, and pair interrupted calls.
- Modify `code_operator/__main__.py`: preserve one-shot mode and add lazy interactive command loop.
- Create `tests/test_journal.py` and `tests/test_session.py`; extend context, registry, loop, CLI, and integration tests.
- Modify `README.md`, `README.txt`, `DESIGN.md`, `REFERENCES.md`, `REVIEW_LOG.md`, and the root v4.5 plan only after implementation is green.

## Task 1: Bounded in-memory ChangeJournal

**Files:**
- Create: `code_operator/journal.py`
- Create: `tests/test_journal.py`

- [x] **Step 1: Write failing stack and capacity tests**

Add tests with these public names and contracts:

```python
def record(path: str, before: str | None, after_hash: str) -> ChangeRecord:
    return ChangeRecord(
        path=path,
        before_content=before,
        before_hash=None if before is None else sha256(before.encode()).hexdigest(),
        after_hash=after_hash,
        source_tool="write_file",
    )


def test_journal_is_lifo_and_discard_requires_current_top() -> None:
    journal = ChangeJournal(max_entries=32, max_snapshot_bytes=8 * 1024 * 1024)
    first = record("a.py", "a", "after-a")
    second = record("b.py", "b", "after-b")
    journal.record(first)
    journal.record(second)
    assert journal.peek() is second
    with pytest.raises(ValueError, match="栈顶"):
        journal.discard(first)
    journal.discard(second)
    assert journal.peek() is first


def test_journal_evicts_oldest_by_entry_limit() -> None:
    journal = ChangeJournal(max_entries=2, max_snapshot_bytes=100)
    journal.record(record("a.py", "a", "a"))
    journal.record(record("b.py", "b", "b"))
    journal.record(record("c.py", "c", "c"))
    assert journal.depth == 2
    newest = journal.peek()
    assert newest is not None and newest.path == "c.py"
    journal.discard(newest)
    assert journal.peek() is not None and journal.peek().path == "b.py"


def test_journal_evicts_oldest_by_utf8_snapshot_bytes() -> None:
    journal = ChangeJournal(max_entries=32, max_snapshot_bytes=6)
    journal.record(record("a.py", "中文", "a"))
    journal.record(record("b.py", "x", "b"))
    assert journal.peek() is not None and journal.peek().path == "b.py"
    assert journal.snapshot_bytes == 1


def test_new_file_record_counts_entry_but_zero_snapshot_bytes() -> None:
    journal = ChangeJournal(max_entries=2, max_snapshot_bytes=1)
    journal.record(record("new.py", None, "new"))
    assert journal.depth == 1
    assert journal.snapshot_bytes == 0


def test_clear_removes_records_and_byte_accounting() -> None:
    journal = ChangeJournal()
    journal.record(record("a.py", "中文", "a"))
    journal.clear()
    assert journal.depth == 0
    assert journal.snapshot_bytes == 0
    assert journal.peek() is None
```

- [x] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_journal.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'code_operator.journal'`.

- [x] **Step 3: Implement the minimal bounded stack**

Create immutable types and a stack with these exact public fields:

```python
MAX_JOURNAL_ENTRIES = 32
MAX_JOURNAL_SNAPSHOT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ChangeRecord:
    path: str
    before_content: str | None
    before_hash: str | None
    after_hash: str
    source_tool: str

    @property
    def snapshot_bytes(self) -> int:
        if self.before_content is None:
            return 0
        return len(self.before_content.encode("utf-8"))


@dataclass(frozen=True)
class UndoResult:
    ok: bool
    error_code: str | None
    message: str
    path: str | None = None
    source_tool: str | None = None
    diff: str = ""
    diff_truncated: bool = False
    remaining: int = 0


class ChangeJournal:
    def __init__(
        self,
        *,
        max_entries: int = MAX_JOURNAL_ENTRIES,
        max_snapshot_bytes: int = MAX_JOURNAL_SNAPSHOT_BYTES,
    ) -> None:
        if max_entries <= 0 or max_snapshot_bytes <= 0:
            raise ValueError("Journal 上限必须为正整数")
        self._max_entries = max_entries
        self._max_snapshot_bytes = max_snapshot_bytes
        self._records: list[ChangeRecord] = []
        self._snapshot_bytes = 0

    @property
    def depth(self) -> int:
        return len(self._records)

    @property
    def snapshot_bytes(self) -> int:
        return self._snapshot_bytes

    def record(self, change: ChangeRecord) -> None:
        self._records.append(change)
        self._snapshot_bytes += change.snapshot_bytes
        while (
            len(self._records) > self._max_entries
            or self._snapshot_bytes > self._max_snapshot_bytes
        ):
            removed = self._records.pop(0)
            self._snapshot_bytes -= removed.snapshot_bytes

    def peek(self) -> ChangeRecord | None:
        return self._records[-1] if self._records else None

    def discard(self, expected: ChangeRecord) -> None:
        if not self._records or self._records[-1] is not expected:
            raise ValueError("只能移除当前栈顶记录")
        removed = self._records.pop()
        self._snapshot_bytes -= removed.snapshot_bytes

    def clear(self) -> None:
        self._records.clear()
        self._snapshot_bytes = 0
```

- [x] **Step 4: Run journal tests and confirm GREEN**

Run `python -m pytest tests/test_journal.py -q`.

Expected: `5 passed`.

- [x] **Step 5: Checkpoint without committing**

Run `git diff --check` and `git status --short`. Confirm only `journal.py` and `test_journal.py` are new at this checkpoint.

**Observed Task 1 evidence:** the named journal tests failed before the module existed; the minimal bounded stack then passed its focused tests, and the stage ended with 353 offline tests passing. Changes remained uncommitted.

## Task 2: Record file mutations and safely undo them

**Files:**
- Modify: `code_operator/tools/filesystem.py`
- Modify: `tests/test_journal.py`
- Modify: `tests/test_filesystem_tools.py`

- [x] **Step 1: Add failing mutation and restore tests**

Add tests named:

```python
def test_undo_restores_existing_file_and_updates_read_authorization(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("old\n", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.py")
    changed = file_tools.write_file(
        tool_call_id="write", path="sample.py", content="new\n"
    )
    undone = file_tools.undo_last_change()
    assert changed.ok is True and undone.ok is True
    assert target.read_text(encoding="utf-8") == "old\n"
    assert "-new" in undone.diff and "+old" in undone.diff
    rewritten = file_tools.write_file(
        tool_call_id="rewrite", path="sample.py", content="third\n"
    )
    assert rewritten.ok is True


def test_undo_deletes_new_file_and_supports_two_consecutive_undos(tmp_path: Path) -> None:
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="one", path="one.txt", content="one")
    file_tools.write_file(tool_call_id="two", path="two.txt", content="two")
    assert file_tools.undo_last_change().path == "two.txt"
    assert not (tmp_path / "two.txt").exists()
    assert file_tools.undo_last_change().path == "one.txt"
    assert not (tmp_path / "one.txt").exists()


def test_undo_refuses_external_change_and_keeps_record(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    target.write_text("external", encoding="utf-8")
    result = file_tools.undo_last_change()
    assert result.error_code == "UNDO_CONFLICT"
    assert journal.depth == 1
    assert target.read_text(encoding="utf-8") == "external"


def test_noop_write_does_not_create_journal_record(tmp_path: Path) -> None:
    target = tmp_path / "same.txt"
    target.write_text("same", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="same.txt")
    file_tools.write_file(tool_call_id="write", path="same.txt", content="same")
    assert journal.depth == 0


def test_reset_read_state_revokes_existing_overwrite_authorization(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old", encoding="utf-8")
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=ChangeJournal())
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    file_tools.reset_read_state()
    result = file_tools.write_file(
        tool_call_id="write", path="sample.txt", content="new"
    )
    assert result.error_code == "READ_REQUIRED"
```

Also add parameterized failures for empty stack, missing/replaced target, directory target, non-UTF-8 target, current hash mismatch, denied/symlink path, restore write failure, delete failure, diff truncation, and current API Key/terminal-control redaction.

- [x] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_journal.py tests/test_filesystem_tools.py -q
```

Expected: failures report unexpected `journal` constructor argument and missing `undo_last_change`/`reset_read_state`.

- [x] **Step 3: Connect successful writes to the journal**

Extend `FileTools.__init__` with `journal: ChangeJournal | None = None`. After a successful real mutation and computed `after_hash`, record only when `before_hash != after_hash`:

```python
if self.journal is not None and before_hash != after_hash:
    self.journal.record(
        ChangeRecord(
            path=relative,
            before_content=None if before_hash is None else before,
            before_hash=before_hash,
            after_hash=after_hash,
            source_tool="write_file",
        )
    )
```

Use the equivalent `source_tool="edit_file"` block after a successful edit. Never record before the file operation succeeds.

- [x] **Step 4: Implement guarded restore and state reset**

Add:

```python
def reset_read_state(self) -> None:
    self._complete_read_hashes.clear()


def undo_last_change(self) -> UndoResult:
    change = None if self.journal is None else self.journal.peek()
    if change is None:
        return UndoResult(False, "UNDO_EMPTY", "没有可撤销的文件修改")
    try:
        resolved = self.policy.resolve(change.path, for_write=True)
    except PathPolicyError as error:
        return UndoResult(False, "PATH_DENIED", self.redactor.redact(error), remaining=self.journal.depth)
```

Continue the method with explicit branches in this order:

- Existing-file restore requires a current regular UTF-8 file whose SHA-256 equals `change.after_hash`, writes `change.before_content` with the same temporary-file/fsync/replace sequence as normal overwrite, then stores `change.before_hash` in `_complete_read_hashes`.
- New-file restore requires the file to exist as a regular file with matching hash, unlinks it, then removes its read hash.
- All existence/type/hash mismatches return `UNDO_CONFLICT` and retain the record.
- Read/decode failures return `UNDO_READ_FAILED`; replace/unlink failures return `UNDO_WRITE_FAILED`.
- Generate and redact a reverse unified diff, pass it through existing `_truncate`, then discard the exact top record only after the disk and read-state updates succeed.
- After discard, return `UndoResult(ok=True, error_code=None, message="文件修改已撤销", path=change.path, source_tool=change.source_tool, diff=clean_diff, diff_truncated=diff_truncated, remaining=self.journal.depth)`.
- On `KeyboardInterrupt`, re-read the target; if it equals the intended restored state, finalize discard and return success with an explicit completion message, otherwise retain the record and re-raise for the CLI to report cancellation.

- [x] **Step 5: Run focused and regression tests**

Run:

```powershell
python -m pytest tests/test_journal.py tests/test_filesystem_tools.py tests/test_policy.py -q
```

Expected: all selected tests pass; existing file-tool result dictionaries remain unchanged.

- [x] **Step 6: Checkpoint without committing**

Run `git diff --check`. Confirm six model tool schemas and existing `ToolResult.details` contracts have not changed.

**Observed Task 2 evidence:** mutation/restore tests first failed against the unconnected file tools, then guarded recording and restore passed the focused regressions; the stage ended with 377 offline tests passing. Six model tool schemas and existing model-visible result dictionaries remained unchanged.

## Task 3: Multi-user context parsing and two-level trimming

**Files:**
- Modify: `code_operator/context.py`
- Modify: `tests/test_context.py`

- [x] **Step 1: Write failing multi-turn tests**

Add tests for these exact cases:

```python
def test_prepare_accepts_two_complete_user_turns() -> None:
    messages = [
        message("system", "system"),
        message("user", "first"),
        message("assistant", "first done"),
        message("user", "second"),
    ]
    prepared = ContextManager(
        context_window=10_000, max_output_tokens=100
    ).prepare(messages, [])
    assert prepared.messages == messages


def test_trim_drops_oldest_complete_user_turn_before_current_rounds() -> None:
    old_turn = [message("user", "old" * 100), message("assistant", "old done")]
    current = [
        message("user", "current"),
        tool_call_message("one"),
        tool_result_message("one", "kept"),
    ]
    sizing = ContextManager(context_window=10_000, max_output_tokens=10)
    budget = sizing.estimate_tokens([message("system", "s")] + current, [])
    prepared = ContextManager(
        context_window=budget + 10, max_output_tokens=10
    ).prepare([message("system", "s")] + old_turn + current, [])
    assert prepared.messages == [message("system", "s")] + current
    assert prepared.trimmed_turns == 1


def test_trim_current_turn_never_splits_latest_tool_group() -> None:
    current = [
        message("system", "s"),
        message("user", "current"),
        tool_call_message("old"),
        tool_result_message("old", "x" * 200),
        tool_call_message("latest"),
        tool_result_message("latest", "latest-result"),
    ]
    sizing = ContextManager(context_window=10_000, max_output_tokens=10)
    minimum = sizing.estimate_tokens(current[:2] + current[-2:], [])
    prepared = ContextManager(
        context_window=minimum + 10, max_output_tokens=10
    ).prepare(current, [])
    assert prepared.messages == current[:2] + current[-2:]
    assert prepared.trimmed_rounds == 1
```

Add malformed-history tests for a tool before assistant, mismatched IDs, duplicate IDs, an assistant before the first user, and a second system message.

- [x] **Step 2: Run context tests and confirm RED**

Run `python -m pytest tests/test_context.py -q`.

Expected: multi-user cases fail with `ContextLimitError` or missing `trimmed_turns`.

- [x] **Step 3: Replace the single-prefix parser with user-turn groups**

Extend `PreparedContext`:

```python
@dataclass(frozen=True)
class PreparedContext:
    messages: list[dict[str, object]]
    estimated_tokens: int
    trimmed_rounds: int
    trimmed_turns: int = 0
```

Implement a private parser returning `system` plus `list[list[list[message]]]`, where each outer entry is one user turn and its first inner group contains the user message. Reuse one helper to validate each assistant plus its ordered tool-result group. Reject every structure that cannot be replayed without guessing.

In `prepare()` remove oldest whole turns while more than one turn remains; then remove oldest assistant groups from the current turn while more than one assistant group remains. Flatten only after each removal and use the existing byte/3 estimator and output reservation.

- [x] **Step 4: Run focused and loop context regressions**

Run:

```powershell
python -m pytest tests/test_context.py tests/test_loop.py -q
```

Expected: all selected tests pass and the existing single-user trimming tests remain green.

- [x] **Step 5: Checkpoint without committing**

Run `git diff --check` and inspect every parser branch for complete tool ID ordering.

**Observed Task 3 evidence:** multi-user parsing/trimming tests failed against the prior single-user prefix assumption, then passed with complete user-turn and assistant/tool grouping; the stage ended with 391 offline tests passing.

## Task 4: Sequential interruption results and re-entrant AgentLoop

**Files:**
- Modify: `code_operator/tools/registry.py`
- Modify: `code_operator/loop.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_loop.py`

- [x] **Step 1: Write failing registry iteration and repeated-run tests**

Add:

```python
def test_iter_results_validates_all_ids_then_yields_in_order() -> None:
    seen: list[str] = []
    registry = ToolRegistry({"list_dir": result_handler(seen, "list_dir")})
    calls = [call("list_dir", {}, call_id="one"), call("list_dir", {}, call_id="two")]
    results = list(registry.iter_results(calls))
    assert [item.tool_call_id for item in results] == ["one", "two"]


def test_second_run_replays_first_complete_user_turn_and_resets_counts() -> None:
    model = FakeModelClient([
        turn(content="first done", total_tokens=3),
        turn(content="second done", total_tokens=5),
    ])
    loop = AgentLoop(model, ToolRegistry({}))
    first = loop.run("first task")
    second = loop.run("continue second task")
    assert first.model_rounds == second.model_rounds == 1
    assert first.provider_total_tokens == 3
    assert second.provider_total_tokens == 5
    assert model.calls[1][0][1:] == [
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "first done"},
        {"role": "user", "content": "continue second task"},
    ]


def test_reset_removes_prior_turns_but_keeps_system_prompt() -> None:
    model = FakeModelClient([turn(content="one"), turn(content="two")])
    loop = AgentLoop(model, ToolRegistry({}), system_prompt="system")
    loop.run("first")
    loop.reset()
    loop.run("second")
    assert model.calls[1][0] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "second"},
    ]
```

Add an interrupting handler test with three calls: first succeeds, second raises `KeyboardInterrupt`, third must not execute. Assert three result messages in original order with error codes `[None, "USER_ABORTED", "NOT_EXECUTED_AFTER_ABORT"]`, one run event, no later model request, and `USER_ABORTED` status.

- [x] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_registry.py tests/test_loop.py -q
```

Expected: missing `iter_results`/`reset`, second run omits first history, and interrupt pairing has fewer than three results.

- [x] **Step 3: Add validated sequential iteration**

In `ToolRegistry`, extract the existing empty/duplicate-ID validation into `_validate_call_ids(calls)`. Add:

```python
def iter_results(self, calls: Sequence[ToolCall]) -> Iterator[ToolResult]:
    self._validate_call_ids(calls)
    for call in calls:
        yield self._execute(call)

def execute_calls(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
    return list(self.iter_results(calls))
```

Import `Iterator` from `collections.abc`. Existing `execute_calls()` behavior and schema generation must remain byte-for-byte equivalent for valid inputs.

- [x] **Step 4: Persist AgentLoop history and reset only per-run counters**

Initialize:

```python
self._messages: list[dict[str, object]] = [
    {"role": "system", "content": self._system_prompt}
]

def reset(self) -> None:
    self._messages = [{"role": "system", "content": self._system_prompt}]
```

At the start of `run(user_task)`, append the user message and reset only local counters. Replace the local two-message initialization with `messages = self._messages`; whenever `ContextManager.prepare()` trims, assign both `messages` and `self._messages` to the prepared list. If the first prepare for the new user raises `ContextLimitError`, remove that just-added user message before returning.

- [x] **Step 5: Pair every interrupted call**

Iterate `registry.iter_results(turn.tool_calls)` while accumulating results. On `KeyboardInterrupt`, append a `USER_ABORTED` result for the call at index `len(results)` and `NOT_EXECUTED_AFTER_ABORT` for every later call. Process audit, trace, message append, signatures, and failure counts for all accumulated results before returning `USER_ABORTED`; do not return inside the per-result loop when the first abort marker is encountered.

Use these exact synthetic results:

```python
ToolResult(
    tool_call_id=current.id,
    name=current.name,
    ok=False,
    error_code="USER_ABORTED",
    message="工具执行被用户中止",
    details={},
)

ToolResult(
    tool_call_id=remaining.id,
    name=remaining.name,
    ok=False,
    error_code="NOT_EXECUTED_AFTER_ABORT",
    message="同轮较早的工具调用被中止，本调用未执行",
    details={},
)
```

- [x] **Step 6: Run focused and integration regressions**

Run:

```powershell
python -m pytest tests/test_registry.py tests/test_context.py tests/test_loop.py tests/test_integration_fake_model.py -q
```

Expected: all selected tests pass, including exact tool schemas and replay-field checks.

- [x] **Step 7: Checkpoint without committing**

Run `git diff --check`. Inspect that every assistant tool-call message retained by the loop has exactly one ordered tool result per ID.

**Observed Task 4 evidence:** sequential interruption and repeated-run tests first exposed lost partial results and fresh-history behavior; the re-entrant loop and ordered completion markers then passed focused/integration regressions, ending with 410 offline tests passing. A review-found handler-returned `USER_ABORTED` case was also covered before the stage closed.

## Task 5: AgentSession composition and local state events

**Files:**
- Create: `code_operator/session.py`
- Create: `tests/test_session.py`
- Modify: `code_operator/__main__.py`
- Modify: `tests/test_loop.py`

- [x] **Step 1: Write failing Session lifecycle tests**

Create fakes that expose `close_calls` and scripted responses, then add:

```python
def test_session_reuses_loop_and_client_for_two_tasks(tmp_path: Path) -> None:
    client = ClosingFakeModel([turn(content="one"), turn(content="two")])
    with AgentSession(config(), workspace=tmp_path, approve=deny, client=client) as session:
        session.run("first")
        session.run("second")
    assert client.close_calls == 0
    assert client.calls[1][0][-1] == {"role": "user", "content": "second"}
    assert any(item.get("content") == "first" for item in client.calls[1][0])


def test_owned_client_closes_exactly_once(monkeypatch, tmp_path: Path) -> None:
    client = ClosingFakeModel([turn(content="done")])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)
    session = AgentSession(config(), workspace=tmp_path, approve=deny)
    session.run("task")
    session.close()
    session.close()
    assert client.close_calls == 1


def test_successful_undo_adds_one_bounded_notice_to_next_user_input(tmp_path: Path) -> None:
    client = ClosingFakeModel([turn(content="done")])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)
    session.file_tools.write_file(tool_call_id="create", path="sample.py", content="x")
    assert session.undo().ok is True
    session.run("continue")
    sent = client.calls[0][0][-1]["content"]
    assert "[本地会话事件]" in sent and "sample.py" in sent
    assert "[当前用户请求]\ncontinue" in sent
    assert session.pending_event_count == 0


def test_reset_clears_history_reads_journal_and_pending_events(tmp_path: Path) -> None:
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=ClosingFakeModel([]))
    session.file_tools.write_file(tool_call_id="create", path="sample.py", content="x")
    session.undo()
    session.reset()
    assert session.undo_depth == 0
    assert session.pending_event_count == 0
```

Also test no `.code-operator/session*`, transcript, or checkpoint file is created, and external clients are not closed.

- [x] **Step 2: Run Session tests and confirm RED**

Run `python -m pytest tests/test_session.py -q`.

Expected: collection fails with `ModuleNotFoundError: No module named 'code_operator.session'`.

- [x] **Step 3: Implement session-owned composition**

Create `AgentSession` with constructor parameters matching existing `run_task()` inputs. Build `WorkspacePolicy`, `Redactor`, `ChangeJournal`, `FileTools`, `SearchTools`, `CommandPolicy`, command handler, `ToolRegistry`, `JsonlAudit`, and `AgentLoop` once. Expose read-only `file_tools`, `undo_depth`, and `pending_event_count` properties needed by the CLI and black-box tests.

Use this ownership rule:

```python
self._owned_client = ModelClient(config) if client is None else None
self._client = self._owned_client if self._owned_client is not None else client
self._closed = False

def close(self) -> None:
    if self._closed:
        return
    self._closed = True
    if self._owned_client is not None:
        self._owned_client.close()
```

- [x] **Step 4: Implement run, undo, reset, and pending notice**

`run(task)` rejects a closed session, consumes at most one pending undo notice, builds the approved bounded Chinese prefix, then calls the retained `AgentLoop.run()`. `undo()` calls `file_tools.undo_last_change()` and appends a redacted, terminal-safe path notice only on success. `reset()` calls loop reset, file read-state reset, journal clear, and pending-event clear without changing files or closing the client.

- [x] **Step 5: Preserve build_registry and run_task compatibility**

Move shared construction into functions in `session.py`, but keep `code_operator.__main__.build_registry()` as a compatibility wrapper returning the same `ToolRegistry`. Reimplement `run_task()` as:

```python
with AgentSession(
    config,
    workspace=workspace,
    approve=approve,
    client=client,
    environment=environment,
    ask_all=ask_all,
    auto_approve_tests=auto_approve_tests,
    trace=trace,
) as session:
    return session.run(task)
```

The Session must not close a caller-injected client.

- [x] **Step 6: Run Session, assembly, and Eval harness tests**

Run:

```powershell
python -m pytest tests/test_session.py tests/test_loop.py tests/test_integration_fake_model.py tests/test_golden_eval.py -q
```

Expected: all selected tests pass; no network call occurs.

- [x] **Step 7: Checkpoint without committing**

Run `git diff --check` and confirm no session/transcript/checkpoint artifacts exist under the repository.

**Observed Task 5 evidence:** lifecycle and local-event tests failed before `AgentSession` existed, then passed with shared composition, ownership-safe close, reset, Undo event injection and one-shot compatibility; the stage ended with 427 offline tests passing and no session/transcript/checkpoint artifact.

## Task 6: Interactive CLI and exact local commands

**Files:**
- Modify: `code_operator/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_session.py`

- [x] **Step 1: Write failing interactive-loop tests**

Use scripted `input()` values and a fake Session factory. Add tests named:

```python
def test_no_argument_cli_runs_two_tasks_in_one_session_then_exits(monkeypatch, capsys) -> None:
    inputs = iter(["first", "continue", "/exit"])
    fake = FakeSession(results=[completed("one"), completed("two")])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr("code_operator.__main__._create_session", lambda *_a, **_k: fake)
    assert main([]) == 0
    assert fake.tasks == ["first", "continue"]
    assert fake.close_calls == 1


def test_exit_empty_and_unknown_command_do_not_load_config(monkeypatch, capsys) -> None:
    inputs = iter(["", "/history", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )
    assert main([]) == 0
    assert "未知本地命令" in capsys.readouterr().out


def test_slash_word_inside_natural_language_is_sent_to_model(monkeypatch) -> None:
    inputs = iter(["请解释 /undo 的边界", "/exit"])
    fake = FakeSession(results=[completed("done")])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr("code_operator.__main__._create_session", lambda *_a, **_k: fake)
    assert main([]) == 0
    assert fake.tasks == ["请解释 /undo 的边界"]
```

Add tests for `/undo` before initialization, successful/failed undo rendering, `/new` and `/exit` confirmation with non-empty depth, default denial, EOF warning, Ctrl-C at prompt, `USER_ABORTED` task returning to prompt, Provider failure returning to prompt, configuration failure exit 2, and one-shot exit-code compatibility.

Also lock positional command behavior: `main(["/exit"])` remains a configuration-free successful exit for compatibility; `main(["/undo"])` and `main(["/new"])` print that the command requires interactive mode and return 2 without loading Provider configuration. A positional natural-language task containing `/undo` remains an ordinary model task.

- [x] **Step 2: Run CLI tests and confirm RED**

Run `python -m pytest tests/test_cli.py -q`.

Expected: the first test exits after one input or `_create_session` is missing; command confirmation and lazy initialization tests fail.

- [x] **Step 3: Split one-shot rendering from interactive control**

Extract helpers with these responsibilities:

```python
def _print_run_result(result: RunResult, redactor: Redactor) -> int:
    if result.final_text:
        safe_final = terminal_safe_text(redactor.redact(result.final_text), multiline=True)
        print("[回答]")
        for line in safe_final.split(chr(10)):
            print(f"  {line}")
    safe_status = terminal_safe_text(redactor.redact(result.status))
    print(
        f"状态={safe_status} 模型轮次={result.model_rounds} "
        f"工具调用={result.tool_calls}"
    )
    if result.provider_total_tokens is None:
        print("供应商用量不完整")
    else:
        print(f"供应商总 token={result.provider_total_tokens}")
    print(f"本地估算 token={result.estimated_context_tokens}（非供应商真实用量）")
    if result.status == "COMPLETED":
        return 0
    if result.status == "USER_ABORTED":
        return 130
    return 1

def _confirm(message: str) -> bool:
    answer = input(message).strip().casefold()
    return answer in {"y", "yes", "允许"}

def _local_command(text: str) -> str | None:
    folded = text.strip().casefold()
    return folded if folded in {"/undo", "/new", "/exit"} else None
```

Do not treat text containing whitespace plus additional words as a local command. If stripped input begins with `/` but is not an exact command, print a local unknown-command message and continue without loading Provider configuration.

- [x] **Step 4: Implement lazy interactive loop**

`main()` handles exact positional `/exit`, `/undo`, and `/new` according to the preceding compatibility tests, then routes every other non-empty positional task to the existing one-shot path. With no task, loop over `input("code-operator> ")`; handle blank, local commands, unknown slash commands, Ctrl-C, and EOF before lazily creating the Session for the first normal task.

For `/new` and `/exit`, require confirmation only when `session is not None and session.undo_depth > 0`. `/undo` before Session exists renders `UNDO_EMPTY`. A task result never exits the interactive loop. Always close an initialized Session in `finally`.

- [x] **Step 5: Render Undo safely**

Add a renderer that prints stable `[撤销] OK/ERROR`, tool, path, remaining count, and successful reverse diff. Pass message/path/diff through the existing `Redactor` and `terminal_safe_text`; allow ordinary LF only for diff, and do not write this event to JSONL audit.

- [x] **Step 6: Run CLI and terminal-safety tests**

Run:

```powershell
python -m pytest tests/test_cli.py tests/test_trace.py tests/test_session.py -q
```

Expected: all selected tests pass; injected ANSI/OSC, CR, backspace, bidi, current-key and Bearer samples cannot forge a top-level command/result line.

- [x] **Step 7: Run all core regression groups**

Run:

```powershell
python -m pytest tests/test_config.py tests/test_client.py tests/test_registry.py tests/test_context.py tests/test_loop.py tests/test_policy.py tests/test_filesystem_tools.py tests/test_search_tool.py tests/test_command_tool.py tests/test_audit.py tests/test_trace.py tests/test_cli.py tests/test_session.py tests/test_journal.py -q
```

Expected: all selected tests pass with no network access.

**Observed Task 6 evidence:** interactive-loop tests first failed against the previous one-prompt CLI, then passed with lazy configuration, exact local commands, confirmation guards, safe Undo rendering and preserved one-shot exit codes; the stage ended with 471 offline tests passing. No real API request was made.

## Task 7: Documentation, references, and plan evidence

**Files:**
- Modify: `README.md`
- Modify: `README.txt`
- Modify: `DESIGN.md`
- Modify: `REFERENCES.md`
- Modify: `docs/superpowers/plans/2026-08-30-interactive-session-undo-implementation.md`
- Modify outside repository: `../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md`

- [x] **Step 1: Write a failing documentation fact probe**

Run a read-only PowerShell assertion that requires all of these literals after implementation:

```powershell
$readme = Get-Content -Raw -LiteralPath README.md
$design = Get-Content -Raw -LiteralPath DESIGN.md
$short = Get-Content -Raw -LiteralPath README.txt
if ($readme -notmatch '/undo' -or $readme -notmatch '/new' -or $readme -notmatch '跨进程') { throw 'README session facts missing' }
if ($design -notmatch 'AgentSession' -or $design -notmatch 'NOT_EXECUTED_AFTER_ABORT') { throw 'DESIGN session facts missing' }
if ($short -notmatch '/undo') { throw 'README.txt undo fact missing' }
```

Expected before documentation edits: exit 1 with `README session facts missing`.

- [x] **Step 2: Update README.md and README.txt truthfully**

Document both invocation modes and only the three implemented local commands. State that Session history and Undo are memory-only; `/undo` covers direct `write_file/edit_file` changes, does not undo commands, checks external hashes, and disappears at `/new`/exit. Keep the real narrow-terminal and Ubuntu limitations. Keep README.txt at or below 1000 Unicode characters.

- [x] **Step 3: Update DESIGN.md and REFERENCES.md**

Add the implemented E4 architecture, multi-user trimming, interrupt result pairing, capacity constants, local event notice, and exact non-goals. In REFERENCES, cite the official OpenAI Responses conversation-state page and official Claude Code CLI/checkpoint pages as behavior references, record the independent Chat Completions/local-state implementation difference, and keep third-party production code reuse as none.

- [x] **Step 4: Update the root execution plan without falsifying the video gate**

Record that the project author explicitly deferred the first complete video and authorized E4 design/implementation despite the original gate. Mark E4 checkboxes only after the corresponding implementation evidence exists. Do not mark video, ZIP, upload, Resume, command rollback, or real session probe complete.

- [x] **Step 5: Mark implementation-plan checkboxes with actual evidence**

Change each completed `- [ ]` in this plan to `- [x]` only after its command has actually run with the stated outcome. Replace expected counts in the final evidence section with observed counts; do not rewrite the red-phase expectations.

- [x] **Step 6: Run documentation and preflight checks**

Run:

```powershell
python -m pytest tests/test_submission_preflight.py -q
python -X utf8 scripts/preflight_submission.py --help
python -c "from pathlib import Path; text=Path('README.txt').read_text(encoding='utf-8'); print(len(text)); assert len(text) <= 1000"
git diff --check
```

Expected: submission preflight tests pass, help exits 0, README.txt assertion passes, and diff check is silent.

**Observed Task 7 evidence:** the pre-edit fact probe failed because README lacked `/undo`, `/new`, and `跨进程`, DESIGN lacked `AgentSession` and `NOT_EXECUTED_AFTER_ABORT`, and README.txt lacked `/undo`. After the documentation updates, the same probe passed; `tests/test_submission_preflight.py` reported 22 passed, preflight `--help` exited 0, README.txt measured 874 Unicode characters, local Markdown links and project-name scans passed, and `git diff --check` reported no whitespace error. The full 471-test result remains Task 6 evidence until Task 8 reruns the complete suite after documentation changes.

## Task 8: Full verification, human review, and one local implementation commit

**Files:**
- Modify: `REVIEW_LOG.md` only after `E4-001` approval
- Verify: every changed production, test, documentation, plan, and reference file

- [x] **Step 1: Run full offline verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
git diff --check
```

Observed: `471 passed in 47.32s`; the outer elapsed time was 47.930 seconds and pytest exited 0. `python -m compileall -q code_operator evals scripts tests` exited 0 in 174 ms. `git diff --check` exited 0 in 130 ms; it emitted only Git's existing LF-to-CRLF working-copy warnings and no whitespace errors.

- [x] **Step 2: Run focused compatibility verification**

Run:

```powershell
python -m pytest tests/test_probe_protocol.py tests/test_client.py tests/test_registry.py tests/test_integration_fake_model.py tests/test_golden_eval.py tests/test_submission_preflight.py -q
```

Observed: `115 passed in 42.41s`; the outer elapsed time was 43.052 seconds and pytest exited 0. The selected groups cover the six-tool schemas, P0 replay fields, fake end-to-end loop, Eval harness, CLI/Undo/interrupt/context behavior, and submission preflight.

- [x] **Step 3: Inspect Git scope and prohibited artifacts**

Observed `git status --short --branch`: `main...origin/main [ahead 1]`, with 14 tracked modifications and 5 untracked files in the repository; the complete candidate list is the 19 repository files shown by `git diff --name-only` plus the root v4.5 plan outside the repository. `git diff -- requirements.txt requirements-dev.txt` was empty (exit 0). No candidate audit/session/transcript/checkpoint/PID/tmp/cache artifact exists; ignored `__pycache__` and `.pytest_cache` files are test-run residue and are not candidates. No dependency file changed.

- [x] **Step 4: Scan credentials and privacy boundaries**

Observed over all 20 candidate files (19 repository files plus the root plan): `CODE_OPERATOR_API_KEY` was present in the environment but matched 0 times; Authorization Bearer pattern matched 2 synthetic test strings in `tests/test_filesystem_tools.py`; private-key headers and `sk-`-like tokens matched 0 times. No `.env` file, `.code-operator/audit.jsonl`, transcript/checkpoint/PID/tmp/probe/cache candidate exists. The scan never printed a secret or matching source line.

- [x] **Step 5: Prepare the complete E4-001 review evidence**

Present to the project author:

- Git status and complete changed-file list.
- Every recorded RED command and its expected feature-missing failure.
- Focused and full GREEN results.
- One-shot, schema, P0, Eval, CLI, Undo, interrupt and context compatibility conclusions.
- Credential/artifact/dependency scan results.
- Documentation, implementation, public-reference and independent-implementation consistency.
- Remaining limits: no real new API probe, no persistent Resume, no command rollback, Ubuntu still unverified, first complete video still deferred.

Do not stage or commit production changes before this review package is complete.

Observed review package prepared from the plan's recorded RED evidence and the fresh GREEN/scan results above. This package does not constitute `E4-001` human approval and does not authorize staging, committing, pushing, real API probing, or any deferred delivery item.

- [x] **Step 6: Wait for explicit `E4-001 人工审核通过`**

Do not treat section approvals, design approval, test success, or a general “继续” as implementation approval. The exact human decision must be recorded in the collaboration task.

Observed: the project author explicitly replied `E4-001 人工审核通过` on 2026-08-31. This authorizes the reviewed local implementation commit only and does not authorize remote push.

- [x] **Step 7: Add the approved REVIEW_LOG entry and stage the exact implementation scope**

After approval, add `REVIEW_LOG.md#e4-001` with baseline `b6d273e`, scope, TDD evidence, exact test results, safety scan, independent implementation boundary, known limits, timestamp, and explicit statement that approval does not authorize remote push. Stage only the reviewed files and rerun `git diff --cached --check`, staged-name inspection, dependency diff, and credential scan.

Observed: `REVIEW_LOG.md#e4-001` records the explicit approval, baseline, reviewed scope, TDD and final verification evidence, safety boundary and known limits without authorizing push. Exactly 20 repository files were staged; the root v4.5 plan remains outside the repository. `git diff --cached --check` exited 0, the dependency diff was empty, and no unstaged repository diff remained. The staged scan found 0 exact current-key matches, 0 private-key headers, 0 `sk-`-like tokens and 0 prohibited paths; the 2 Bearer-shaped values are synthetic redaction fixtures in `tests/test_filesystem_tools.py`.

- [x] **Step 8: Create the single local implementation commit**

Run:

```powershell
git commit -m "feat(session): add bounded interactive session and file undo" `
  -m "Human-Review: approved" `
  -m "Review-Scope: E4-001; full-staged-diff" `
  -m "Review-Record: REVIEW_LOG.md#e4-001"
```

Expected: one new local commit after `b6d273e`; worktree clean except the root plan outside the repository; `main` ahead of `origin/main` by two commits until a separately authorized push.

Observed: the approved 20-file E4 candidate was committed locally with the planned semantic title and the `Human-Review`, `Review-Scope`, and `Review-Record` trailers. No remote operation was performed.

- [x] **Step 9: Stop before remote push**

Report the local commit hash, verification evidence, remaining risks, and next-stage estimate. Do not push until the complete section 9.5 gate is shown and the project author replies “允许推送” or “可以 push” for this exact candidate.

Observed: execution stopped at the local commit boundary. The branch is two commits ahead of `origin/main`; remote push remains a separate, incomplete gate requiring a newly displayed section 9.5 evidence package and a new explicit authorization for this exact candidate.
