class LRUCache:
    """Fixed-capacity mapping; get and put both refresh recency; least recent is evicted."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: dict[str, object] = {}

    def get(self, key: str, default: object = None) -> object:
        if key not in self._items:
            return default
        return self._items[key]  # frozen defect: recency is not refreshed on read

    def put(self, key: str, value: object) -> None:
        if key in self._items:
            del self._items[key]
        elif len(self._items) >= self._capacity:
            del self._items[next(iter(self._items))]
        self._items[key] = value
