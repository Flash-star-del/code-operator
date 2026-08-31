from dataclasses import dataclass


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
