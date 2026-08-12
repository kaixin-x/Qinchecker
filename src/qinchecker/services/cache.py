"""来源原文的本地 JSON 缓存。"""

from __future__ import annotations

import hashlib
import json
from json import JSONDecodeError
from pathlib import Path

from qinchecker.models.source import SourceSnapshot


class SourceCache:
    """以规范化请求学名为键的持久缓存。

    缓存只保存页面原文和抓取元数据，不保存 Excel，也不产生字段修改建议。
    """

    def __init__(self, cache_directory: Path) -> None:
        self.cache_directory = cache_directory
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_species_name(name: str) -> str:
        return " ".join(name.strip().casefold().split())

    def path_for(self, species_name: str) -> Path:
        key = self.normalize_species_name(species_name).encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        return self.cache_directory / f"{digest}.json"

    def load(self, species_name: str) -> SourceSnapshot | None:
        path = self.path_for(species_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SourceSnapshot.from_dict(data)
        except (OSError, JSONDecodeError, KeyError, TypeError, ValueError):
            # 强制关闭或磁盘写入中断造成的坏缓存不能阻塞整个批次；下次成功抓取会覆盖它。
            return None

    def save(self, snapshot: SourceSnapshot) -> Path:
        path = self.path_for(snapshot.requested_species_name)
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return path
