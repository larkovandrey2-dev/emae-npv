"""Generated portfolios as deal sources: build them in the background, run on any of them."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .generator import GeneratorParams, generate
from .ports import DealSource

log = logging.getLogger(__name__)

DEFAULT_SOURCE = "default"


class DatasetService:
    def __init__(self, repo, default: DealSource, file_source: Callable[[str], DealSource]):
        self.repo = repo
        self.default = default
        self.file_source = file_source
        self._sources: dict[str, DealSource] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="generate")
        repo.interrupted()

    def start(self, name: str, params: GeneratorParams) -> dict[str, Any]:
        params.validate()
        record = self.repo.create(name, params.rows, params.to_dict())
        self._pool.submit(self._generate, record["id"], params)
        return record

    def _generate(self, dataset_id: str, params: GeneratorParams) -> None:
        last = [0.0]

        def progress(written: int) -> None:
            now = time.monotonic()
            if now - last[0] > 0.5 or written == params.rows:
                last[0] = now
                self.repo.progress(dataset_id, written)

        try:
            started = time.monotonic()
            size = generate(params, self.repo.path_for(dataset_id), progress)
            self.repo.complete(dataset_id, size)
            log.info("dataset_generated id=%s rows=%d bytes=%d s=%.1f", dataset_id, params.rows, size,
                     time.monotonic() - started)
        except Exception as exc:
            log.error("dataset_failed id=%s: %s\n%s", dataset_id, exc, traceback.format_exc())
            self.repo.fail(dataset_id, str(exc))

    def source(self, dataset_id: str | None) -> DealSource:
        """Deal source of a run: the configured portfolio or a finished generated one."""
        if not dataset_id or dataset_id == DEFAULT_SOURCE:
            return self.default
        record = self.repo.get(dataset_id)
        if not record:
            raise LookupError(f"набор сделок {dataset_id} не найден")
        if record["status"] != "done":
            raise ValueError(f"набор сделок «{record['name']}» ещё не готов")
        with self._lock:
            if dataset_id not in self._sources:
                self._sources[dataset_id] = self.file_source(record["path"])
            return self._sources[dataset_id]

    def forget(self, dataset_id: str) -> None:
        with self._lock:
            self._sources.pop(dataset_id, None)
