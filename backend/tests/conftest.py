"""测试夹具：把世界目录指向 tmp_path，绝不污染项目自带的 Demo 世界。"""

from __future__ import annotations

import pytest

from backend.core import world_manager
from backend.models.world import CreateWorldRequest


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    """测试使用独立的临时 settings.yaml，强制 Mock，绝不碰用户的真实配置。

    mock_mode 已成为 Provider 选择的唯一真源：如果用户当前关着 Mock，
    不隔离的话单元测试会真的向 8080 发请求，既慢又不稳定；
    同时 PUT /api/settings 也不能写进项目里的 config/settings.yaml。
    """
    import yaml

    from backend.core import settings_service

    cfg = tmp_path / "settings.yaml"
    cfg.write_text(yaml.safe_dump({"llm": {"mock_mode": True, "provider": "mock", "model": "mock-model"}}),
                   encoding="utf-8")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", cfg)
    return cfg


def seed_characters(world_id: str) -> None:
    """给临时世界放一个主角和一个 NPC，字段值由默认 Schema 补齐。"""
    from backend.core import state_manager

    state_manager.save_generated_character(
        world_id, {"name": "测试主角", "core_description": "临时世界的主角", "custom_fields": {}},
        is_protagonist=True,
    )
    state_manager.save_generated_character(
        world_id, {"name": "测试配角", "core_description": "临时世界的 NPC", "custom_fields": {}},
    )


@pytest.fixture
def worlds_root(tmp_path, monkeypatch):
    root = tmp_path / "worlds"
    root.mkdir()
    monkeypatch.setattr(world_manager, "WORLDS_ROOT", root)
    return root


@pytest.fixture
def world_a(worlds_root):
    world_manager.create_world(CreateWorldRequest(world_id="world_a", name="世界 A"))
    return "world_a"


@pytest.fixture
def world_b(worlds_root):
    world_manager.create_world(CreateWorldRequest(world_id="world_b", name="世界 B"))
    return "world_b"


@pytest.fixture
def seeded_world(world_a):
    seed_characters(world_a)
    return world_a
