"""文件读写工具层。

所有世界文件都经这里读写，统一 UTF-8、统一原子写入，避免半截文件损坏存档。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def atomic_write_text(path: Path, content: str) -> None:
    """先写同目录临时文件并刷盘，再用 os.replace 一次性替换正式文件。

    同目录是为了保证 os.replace 在同一文件系统上，不会退化为复制。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".storyverse_", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 任何失败都不留下临时垃圾，也绝不动原文件。
        if tmp.exists():
            tmp.unlink()
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def atomic_write_yaml(path: Path, data: Any) -> None:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    atomic_write_text(path, text)


def read_text(path: Path, default: str = "") -> str:
    p = Path(path)
    if not p.is_file():
        return default
    return p.read_text(encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 损坏的存档不能被静默当成空数据继续覆盖，交给调用方决定。
        raise ValueError(f"JSON 文件损坏或不可读: {p}")


def read_yaml(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or default
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"YAML 文件损坏或不可读: {p}: {exc}") from exc


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """事件时间线只追加，不改写历史。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with p.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue  # 单行损坏不影响其余历史
        if isinstance(item, dict):
            records.append(item)
    return records[-limit:] if limit else records


def safe_child(root: Path, *parts: str) -> Path:
    """解析子路径并确认仍在 root 内，挡住 ../ 之类的路径穿越。"""
    root_resolved = root.resolve()
    target = root_resolved.joinpath(*parts).resolve()
    if not target.is_relative_to(root_resolved):
        raise ValueError(f"路径越界，拒绝访问: {target}")
    return target
