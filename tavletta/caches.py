"""Caches bounded by the memory of what they keep as well as by a count:
a decoded image file can take hundreds of megabytes, so eight of them
(as functools.lru_cache(8) would keep) can fill the memory."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from functools import update_wrapper
from typing import Any, Generic, NamedTuple, TypeVar

from PIL import Image

T = TypeVar("T")


def image_bytes(image: Image.Image) -> int:
    """Memory of the pixels: Pillow keeps RGB (and 32 bit) in 4 bytes."""
    return image.width * image.height * (1 if image.mode in ("1", "L", "P") else 4)


class Kept:
    """The most recently used values, at most `items` of them and at most
    `max_bytes` together (`size` of each; 0: no limit of bytes); a value
    larger than that alone is not kept. Safe to use from several threads."""

    def __init__(self, items: int, max_bytes: int = 0, size: Callable[[Any], int] = len):
        self.items, self.max_bytes = items, max_bytes
        self.size = size if max_bytes else lambda _value: 0  # by count only: no sizes
        self._values: OrderedDict[Any, Any] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: Any, value: Any) -> None:
        size = self.size(value)
        if self.max_bytes and size > self.max_bytes:
            return
        with self._lock:
            if key in self._values:
                self._bytes -= self.size(self._values.pop(key))
            self._values[key] = value
            self._bytes += size
            while len(self._values) > self.items or (
                self.max_bytes and self._bytes > self.max_bytes
            ):
                self._bytes -= self.size(self._values.popitem(last=False)[1])

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._values)

    @property
    def bytes(self) -> int:
        return self._bytes


class CacheInfo(NamedTuple):
    """As functools.lru_cache's cache_info()."""

    hits: int
    misses: int
    maxsize: int
    currsize: int


class KeptCall(Generic[T]):
    """A function whose results are kept in a Kept: as functools.lru_cache
    (cache_clear(), cache_info()), bounded by their memory as well."""

    def __init__(self, function: Callable[..., T], kept: Kept):
        self.function, self.kept = function, kept
        self.hits = self.misses = 0
        update_wrapper(self, function)

    def __call__(self, *args: Hashable) -> T:
        found = self.kept.get(args)
        if found is not None:
            self.hits += 1
            return found
        self.misses += 1
        value = self.function(*args)  # not under the lock: renders run in threads
        self.kept.put(args, value)
        return value

    def cache_clear(self) -> None:
        self.kept.clear()
        self.hits = self.misses = 0

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self.hits, self.misses, self.kept.items, len(self.kept))


def kept_images(items: int, max_bytes: int) -> Callable[[Callable[..., T]], KeptCall[T]]:
    """A decorator: the images a function returns kept, at most `items` of
    them and `max_bytes` of pixels together."""
    return lambda function: KeptCall(function, Kept(items, max_bytes, image_bytes))
