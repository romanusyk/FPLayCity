from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from src.fpl.loader.utils import ensure_dir_exists


@dataclass(frozen=True)
class SnapshotSpec:
    """Describe the base path (without timestamp) for JSON snapshots."""

    base_path: str


class JsonSnapshotStore:
    """Manage timestamped JSON snapshots for a single resource."""

    def __init__(self, spec: SnapshotSpec):
        self._spec = spec

    @property
    def base_path(self) -> str:
        return self._spec.base_path

    def build_filename(self, current_dt: datetime) -> str:
        base_name = os.path.basename(self.base_path)
        dir_name = os.path.dirname(self.base_path)
        filename = f"{base_name}_{current_dt.isoformat(timespec='seconds')}.json"
        return os.path.join(dir_name, filename) if dir_name else filename

    def list_all(self) -> list[tuple[datetime, str]]:
        dir_path = os.path.dirname(self.base_path) or "."
        base_name = os.path.basename(self.base_path)

        if not os.path.isdir(dir_path):
            return []

        files: list[tuple[datetime, str]] = []
        for file_name in os.listdir(dir_path):
            if not file_name.startswith(base_name + "_") or not file_name.endswith(".json"):
                continue

            timestamp_part = file_name[len(base_name) + 1 : -5]
            try:
                dt = datetime.fromisoformat(timestamp_part)
            except ValueError as exc:  # pragma: no cover - defensive logging
                raise ValueError(
                    f"Invalid timestamp format in filename '{file_name}': {exc}"
                ) from exc
            filepath = os.path.join(dir_path, file_name)
            files.append((dt, filepath))

        files.sort()
        return files

    def find_latest(self) -> tuple[datetime, str] | None:
        snapshots = self.list_all()
        if not snapshots:
            return None
        return snapshots[-1]

    @staticmethod
    def is_up_to_date(latest_state: datetime, freshness_days: int) -> bool:
        return datetime.now() - latest_state < timedelta(days=freshness_days)

    def load_latest(self) -> dict:
        latest = self.find_latest()
        if latest is None:
            raise FileNotFoundError(f"No snapshots found for base path '{self.base_path}'")
        _, path = latest
        with open(path, "r") as fh:
            return json.load(fh)

    def write(self, body: dict, current_dt: datetime | None = None, *, delete_older: bool = True) -> str:
        current_dt = current_dt or datetime.now()
        filepath = self.build_filename(current_dt)
        ensure_dir_exists(filepath)
        with open(filepath, "w") as fh:
            json.dump(body, fh, indent=4)

        if delete_older:
            for _, old_path in self.list_all():
                if old_path == filepath:
                    continue
                if os.path.exists(old_path):
                    os.remove(old_path)

        return filepath

    async def get_or_fetch(
        self,
        freshness: int,
        fetch_fn: Callable[[], Awaitable[dict]],
    ) -> dict:
        latest = self.find_latest()
        if latest and self.is_up_to_date(latest[0], freshness):
            _, path = latest
            with open(path, "r") as fh:
                return json.load(fh)

        body = await fetch_fn()
        self.write(body, delete_older=True)
        return body

