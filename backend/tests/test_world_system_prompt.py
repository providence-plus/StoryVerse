"""每世界 System Prompt：全局铁律在前 + 世界风格追加，热加载同步语义。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.core import file_utils, prompt_builder, save_manager, world_manager
from backend.main import app
from backend.models.world import CreateWorldRequest


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _set_style(world_id: str, style: str) -> None:
    file_utils.atomic_write_text(world_manager.resolve(world_id, "system_prompt.md"), style)


def test_empty_world_file_means_global_only(world_a):
    """新建世界写的是 0 字节空文件，不能被当成风格拼进每一轮。"""
    path = world_manager.resolve(world_a, "system_prompt.md")
    assert path.is_file() and path.read_text(encoding="utf-8") == ""
    composed = prompt_builder.system_prompt_for(world_a)
    assert "叙事者身份与写作铁律" in composed
    assert "本世界专属写手风格" not in composed


def test_comment_only_file_is_ignored(world_a):
    _set_style(world_a, "<!-- 这里只写风格，基础规则在全局 -->\n")
    assert "本世界专属写手风格" not in prompt_builder.system_prompt_for(world_a)


def test_global_comes_before_world_style(world_a):
    _set_style(world_a, "用短句。对白克制，不用感叹号。")
    composed = prompt_builder.system_prompt_for(world_a)
    assert composed.index("叙事者身份与写作铁律") < composed.index("用短句。对白克制")
    assert "不得推翻前面的基础写作铁律" in composed


def test_worlds_are_isolated(world_a, world_b):
    _set_style(world_a, "风格甲：冷静克制")
    _set_style(world_b, "风格乙：浓烈铺陈")
    a = prompt_builder.system_prompt_for(world_a)
    b = prompt_builder.system_prompt_for(world_b)
    assert "风格甲" in a and "风格乙" not in a
    assert "风格乙" in b and "风格甲" not in b


def test_changes_apply_on_the_next_call(world_a):
    """游戏进行中改世界 Prompt：下一次拼装就用新版，不需要退出或重启。"""
    before = prompt_builder.system_prompt_for(world_a)
    assert "本世界专属写手风格" not in before
    _set_style(world_a, "第一版风格")
    assert "第一版风格" in prompt_builder.system_prompt_for(world_a)
    _set_style(world_a, "第二版风格")
    after = prompt_builder.system_prompt_for(world_a)
    assert "第二版风格" in after and "第一版风格" not in after


def test_global_change_affects_every_world(world_a, world_b, tmp_path, monkeypatch):
    """全局改动对所有世界生效（这也是 UI 要提示的范围）。"""
    fake_global = tmp_path / "global_system.md"
    fake_global.write_text("全局旧版铁律", encoding="utf-8")
    monkeypatch.setitem(prompt_builder.PROMPT_FILES, "system_prompt", fake_global)
    assert "全局旧版铁律" in prompt_builder.system_prompt_for(world_a)
    assert "全局旧版铁律" in prompt_builder.system_prompt_for(world_b)
    fake_global.write_text("全局新版铁律", encoding="utf-8")
    assert "全局新版铁律" in prompt_builder.system_prompt_for(world_a)
    assert "全局新版铁律" in prompt_builder.system_prompt_for(world_b)


def test_no_builder_reads_global_directly():
    """6 个 build 函数必须全部走统一入口，漏一个世界风格就会失效。"""
    source = Path(prompt_builder.__file__).read_text(encoding="utf-8")
    assert 'system_prompt=read_prompt("system_prompt")' not in source
    assert source.count("system_prompt=system_prompt_for(") == 8  # 6 个现有 + 2 个新补全


def test_world_request_has_no_world_style(world_a):
    _set_style(world_a, "只属于这个世界的风格")
    request = prompt_builder.build_world_request("一个新的世界")
    assert "只属于这个世界的风格" not in request.system_prompt
    assert request.system_prompt == prompt_builder.system_prompt_for(None)


def test_save_and_load_do_not_roll_back_prompt(world_a):
    """存档快照不含 Prompt 文件，读档不会把写手改动回滚。"""
    _set_style(world_a, "存档时的风格")
    save_manager.auto_save(world_a)
    _set_style(world_a, "读档前刚改的风格")
    save_manager.load_save(world_a, "auto")
    assert "读档前刚改的风格" in prompt_builder.system_prompt_for(world_a)


def test_api_get_put_world_prompt(world_a):
    client = _client()
    assert client.get(f"/api/worlds/{world_a}/system-prompt").json()["content"] == ""
    saved = client.put(f"/api/worlds/{world_a}/system-prompt", json={"content": "慢节奏，偏心理描写"})
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/worlds/{world_a}/system-prompt").json()["content"] == "慢节奏，偏心理描写"
    assert "慢节奏，偏心理描写" in prompt_builder.system_prompt_for(world_a)


def test_api_rejects_illegal_world_id():
    """新接口同样要挡住路径穿越。"""
    client = _client()
    assert client.get("/api/worlds/..%2Fevil/system-prompt").status_code in (400, 404, 422)
    assert client.put("/api/worlds/..%2Fevil/system-prompt", json={"content": "x"}).status_code in (400, 404, 422)


def test_writer_style_survives_confirm_world(worlds_root):
    """审核 M1：写手风格从预览一路写到 worlds/{id}/system_prompt.md。"""
    client = _client()
    payload = {
        "name": "风格世界", "genre": "现代", "description": "一个测试世界",
        "setting": "这是一段足够长的世界观背景，用来通过 confirm 的非空校验。" * 3,
        "rules": "规则一：所有角色都是成年人。规则二：不改写已发生的事件。",
        "opening_location": "门口", "opening_scene": "门半掩着。",
        "opening_choices": [{"id": "A", "text": "推门进去"}],
        "protagonist_hint": "", "npc_hints": [],
        "writer_style": "克制、慢节奏、对白短",
    }
    response = client.post("/api/worlds/confirm", json=payload)
    assert response.status_code == 200, response.text
    world_id = response.json()["world_id"]
    style_file = world_manager.resolve(world_id, "system_prompt.md")
    assert style_file.read_text(encoding="utf-8").strip() == "克制、慢节奏、对白短"
    assert "克制、慢节奏、对白短" in prompt_builder.system_prompt_for(world_id)


def test_create_world_without_ai_writes_style(worlds_root):
    """完全自己写的入口也要能带写手风格。"""
    client = _client()
    response = client.post("/api/worlds", json={
        "name": "手写世界", "description": "", "genre": "",
        "writer_style": "第二人称，冷调",
    })
    assert response.status_code == 200, response.text
    world_id = response.json()["world_id"]
    assert world_manager.resolve(world_id, "system_prompt.md").read_text(encoding="utf-8").strip() == "第二人称，冷调"
