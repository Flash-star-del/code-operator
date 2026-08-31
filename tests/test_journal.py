import hashlib

import pytest

from code_operator.journal import ChangeJournal, ChangeRecord


def record(path, before, after_hash):
    before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest() if before is not None else None
    return ChangeRecord(
        path=path,
        before_content=before,
        before_hash=before_hash,
        after_hash=after_hash,
        source_tool="write_file",
    )


def test_journal_is_lifo_and_discard_requires_current_top():
    journal = ChangeJournal()
    first = record("a", "before a", "after a")
    second = record("b", "before b", "after b")

    journal.record(first)
    journal.record(second)

    assert journal.peek() is second
    with pytest.raises(ValueError, match="栈顶"):
        journal.discard(first)
    equal_but_distinct = ChangeRecord(
        path=second.path,
        before_content=second.before_content,
        before_hash=second.before_hash,
        after_hash=second.after_hash,
        source_tool=second.source_tool,
    )
    assert equal_but_distinct == second
    assert equal_but_distinct is not second
    with pytest.raises(ValueError, match="栈顶"):
        journal.discard(equal_but_distinct)
    assert journal.peek() is second
    journal.discard(second)
    assert journal.peek() is first


def test_journal_evicts_oldest_by_entry_limit():
    journal = ChangeJournal(max_entries=2, max_snapshot_bytes=100)
    first = record("a", "a", "after a")
    second = record("b", "b", "after b")
    third = record("c", "c", "after c")

    journal.record(first)
    journal.record(second)
    journal.record(third)

    assert journal.depth == 2
    assert journal.peek() is third
    journal.discard(third)
    assert journal.peek() is second


def test_journal_evicts_oldest_by_utf8_snapshot_bytes():
    journal = ChangeJournal(max_snapshot_bytes=6)
    first = record("a", "中文", "after a")
    second = record("b", "x", "after b")

    journal.record(first)
    journal.record(second)

    assert journal.depth == 1
    assert journal.peek() is second
    assert journal.snapshot_bytes == 1


def test_new_file_record_counts_entry_but_zero_snapshot_bytes():
    journal = ChangeJournal()
    new_file = record("new", None, "after new")

    journal.record(new_file)

    assert journal.depth == 1
    assert journal.snapshot_bytes == 0


def test_clear_removes_records_and_byte_accounting():
    journal = ChangeJournal()
    journal.record(record("a", "before", "after a"))
    journal.record(record("b", "中文", "after b"))

    journal.clear()

    assert journal.depth == 0
    assert journal.snapshot_bytes == 0
    assert journal.peek() is None
