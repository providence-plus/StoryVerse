"""存档服务：整包快照 + 原子写入。

不同存档各自保存一份完整的剧情状态、人物状态、关系、历史与摘要，
因此切换存档绝不会覆盖另一个分支。读档 = 把快照反向写回工作区。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import file_utils, state_manager, world_manager
from backend.models.world import CurrentState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_dir(world_id: str, save_id: str) -> Path:
    if not save_id or "/" in save_id or "\\" in save_id or save_id.startswith(".."):
        raise ValueError(f"非法 save_id: {save_id!r}")
    return world_manager.resolve(world_id, "saves", save_id)


def _meta_path(world_id: str, save_id: str) -> Path:
    return _save_dir(world_id, save_id) / "save.yaml"


def list_saves(world_id: str) -> list[dict[str, Any]]:
    root = world_manager.resolve(world_id, "saves")
    if not root.is_dir():
        return []
    result = []
    for folder in sorted(root.iterdir()):
        meta = folder / "save.yaml"
        if folder.is_dir() and meta.is_file():
            data = file_utils.read_yaml(meta, {}) or {}
            data.setdefault("save_id", folder.name)
            result.append(data)
    return sorted(result, key=lambda x: x.get("updated_at", ""), reverse=True)


def _ensure_auto_save(world_id: str) -> str:
    """每个世界首次进入时自动建一个 auto 存档。"""
    state = state_manager.get_current_state(world_id)
    save_id = state.save_id or "auto"
    meta = _meta_path(world_id, save_id)
    if not meta.is_file():
        _save_dir(world_id, save_id).mkdir(parents=True, exist_ok=True)
        file_utils.atomic_write_yaml(meta, {
            "save_id": save_id,
            "name": "自动存档" if save_id == "auto" else save_id,
            "created_at": _now(),
            "updated_at": _now(),
            "turn": state.turn,
        })
    return save_id


def snapshot(world_id: str, save_id: str | None = None) -> dict[str, Any]:
    """把当前工作区状态完整复制进某个存档目录。"""
    sid = save_id or _ensure_auto_save(world_id)
    target = _save_dir(world_id, sid)
    target.mkdir(parents=True, exist_ok=True)
    (target / "character_states").mkdir(parents=True, exist_ok=True)

    root = world_manager.world_dir(world_id)
    # 文本文件直接复制内容；JSON 用读写保证格式统一。
    for rel in (
        "state/current.json", "state/relationships.json", "state/field_suggestions.json",
        "memory/summary.md", "memory/timeline.jsonl", "memory/history.jsonl",
    ):
        source = root / rel
        dest = target / Path(rel).name
        if source.is_file():
            if source.suffix == ".json":
                file_utils.atomic_write_json(dest, file_utils.read_json(source, {}) or {})
            else:
                file_utils.atomic_write_text(dest, file_utils.read_text(source))

    # 每个角色的动态状态单独存一份，读档时按角色 ID 写回。
    for cid, is_proto in state_manager.character_kinds(world_id).items():
        folder = (root / "characters" / "protagonist") if is_proto else (root / "characters" / "npcs" / cid)
        state_file = folder / "state.json"
        if state_file.is_file():
            file_utils.atomic_write_json(
                target / "character_states" / f"{cid}.json",
                file_utils.read_json(state_file, {}) or {},
            )

    current = state_manager.get_current_state(world_id)
    meta_path = _meta_path(world_id, sid)
    meta = file_utils.read_yaml(meta_path, {}) or {}
    meta.update({
        "save_id": sid,
        "name": meta.get("name") or ("自动存档" if sid == "auto" else sid),
        "created_at": meta.get("created_at") or _now(),
        "updated_at": _now(),
        "turn": current.turn,
    })
    file_utils.atomic_write_yaml(meta_path, meta)
    return meta


def create_save(world_id: str, name: str) -> dict[str, Any]:
    """手动新建存档并切换到它；当前进度先完整快照，再另起分支。"""
    state = state_manager.get_current_state(world_id)
    old_id = _ensure_auto_save(world_id)
    snapshot(world_id, old_id)

    new_id = f"save_{uuid.uuid4().hex[:8]}"
    _save_dir(world_id, new_id).mkdir(parents=True, exist_ok=True)
    state.save_id = new_id
    state.updated_at = _now()
    state_manager.write_current_state(world_id, state)
    return snapshot(world_id, new_id)


def load_save(world_id: str, save_id: str) -> dict[str, Any]:
    """读取旧存档：把快照反向写回工作区，并切换当前 save_id。"""
    target = _save_dir(world_id, save_id)
    meta = target / "save.yaml"
    if not meta.is_file():
        raise FileNotFoundError(f"存档不存在: {save_id}")

    root = world_manager.world_dir(world_id)
    mapping = {
        "current.json": "state/current.json",
        "relationships.json": "state/relationships.json",
        "field_suggestions.json": "state/field_suggestions.json",
        "summary.md": "memory/summary.md",
        "timeline.jsonl": "memory/timeline.jsonl",
        "history.jsonl": "memory/history.jsonl",
    }
    for filename, rel in mapping.items():
        source = target / filename
        if not source.is_file():
            continue
        dest = root / rel
        if source.suffix == ".json":
            file_utils.atomic_write_json(dest, file_utils.read_json(source, {}) or {})
        else:
            file_utils.atomic_write_text(dest, file_utils.read_text(source))

    # 角色状态按 ID 写回对应目录；存档里没有的角色保持原状。
    kinds = state_manager.character_kinds(world_id)
    for cid, is_proto in kinds.items():
        source = target / "character_states" / f"{cid}.json"
        if source.is_file():
            folder = (root / "characters" / "protagonist") if is_proto else (root / "characters" / "npcs" / cid)
            folder.mkdir(parents=True, exist_ok=True)
            file_utils.atomic_write_json(folder / "state.json", file_utils.read_json(source, {}) or {})

    state = state_manager.get_current_state(world_id)
    state.save_id = save_id
    state.updated_at = _now()
    # 读档属于「事件触发」，立刻允许做一次字段校准。
    state.turns_since_reconcile = 999
    state_manager.write_current_state(world_id, state)
    return file_utils.read_yaml(meta, {}) or {}


def auto_save(world_id: str) -> dict[str, Any]:
    """每轮结束时自动保存到当前分支，不覆盖其他存档。"""
    state = state_manager.get_current_state(world_id)
    return snapshot(world_id, state.save_id or "auto")
