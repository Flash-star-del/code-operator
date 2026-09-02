from lru import LRUCache


def test_get_refreshes_recency() -> None:
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None
