import pytest

from lru import LRUCache


def test_read_keeps_entry_alive() -> None:
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b", "gone") == "gone"
    assert cache.get("c") == 3


def test_put_evicts_least_recent_without_reads() -> None:
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a", "gone") == "gone"
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_overwrite_refreshes_recency_and_keeps_size() -> None:
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert cache.get("b", "gone") == "gone"
    assert cache.get("a") == 10
    assert cache.get("c") == 3


@pytest.mark.parametrize("capacity", [0, -1])
def test_rejects_non_positive_capacity(capacity: int) -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        LRUCache(capacity)
