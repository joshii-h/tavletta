"""Reading a file a user (or a label from elsewhere) names: only a regular
file, and no more of it than a limit. One way for label files, data files
and the image and PDF files a label embeds."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO


class NotRegularFile(Exception):
    """A device, a FIFO or a folder: /dev/zero never ends, a FIFO blocks."""


class FileTooLarge(Exception):
    """More than the limit; nothing more than that was read."""

    def __init__(self, limit: int):
        super().__init__("file larger than %d MB" % (limit // 2**20))
        self.limit = limit


def open_regular(path: str | Path) -> BinaryIO:
    """A regular file opened for reading, without blocking (a FIFO would
    block) and checked as opened, not by its name before (it could be
    swapped in between). OSError if it cannot be opened, NotRegularFile."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except BaseException:
        os.close(fd)
        raise
    if not regular:  # also a folder: closed here, open() would leave it open
        os.close(fd)
        raise NotRegularFile("not a regular file")
    return open(fd, "rb")  # owns fd from here on


def read_limited(path: str | Path, limit: int) -> bytes:
    """The content of a regular file (open_regular) of at most `limit`
    bytes: at most one byte more than the limit is read, however large
    the file is. OSError if it cannot be opened, NotRegularFile,
    FileTooLarge."""
    with open_regular(path) as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise FileTooLarge(limit)
    return raw
