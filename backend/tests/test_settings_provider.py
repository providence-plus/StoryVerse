"""回归：关掉 Mock 后必须真的用真实 Provider，不能被遗留的 provider=mock 拽回去。"""

from __future__ import annotations

from backend.core import llm_provider, settings_service


def test_mock_mode_is_the_only_truth():
    cfg = {"mock_mode": False, "provider": "mock", "base_url": "http://127.0.0.1:8080/v1", "model": "x"}
    assert isinstance(llm_provider.get_provider(cfg), llm_provider.OpenAICompatProvider)

    cfg["mock_mode"] = True
    assert isinstance(llm_provider.get_provider(cfg), llm_provider.MockProvider)


def test_llm_settings_normalizes_stale_provider(tmp_path, monkeypatch):
    """用户只改了 mock_mode、没改 provider 的旧配置必须被自洽地修正。"""
    import yaml

    from backend.core import file_utils

    stale = tmp_path / "settings.yaml"
    stale.write_text(
        yaml.safe_dump({"llm": {"provider": "mock", "mock_mode": False, "model": "nyx"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", stale)
    cfg = settings_service.llm_settings()
    assert cfg["mock_mode"] is False
    assert cfg["provider"] == "openai_compatible"
    assert settings_service.public_settings()["provider"] == "openai_compatible"
    assert "api_key" not in settings_service.public_settings() or settings_service.public_settings()["api_key"] == ""
    # 确认 file_utils 的原子写仍可用（本测试不做真实写盘断言，仅保证依赖存在）
    assert hasattr(file_utils, "atomic_write_yaml")


def test_save_settings_syncs_provider(tmp_path, monkeypatch):
    target = tmp_path / "settings.yaml"
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", target)
    public = settings_service.save_settings({"mock_mode": False, "model": "real-model"})
    assert public["provider"] == "openai_compatible"
    assert public["mock_mode"] is False

    public = settings_service.save_settings({"mock_mode": True})
    assert public["provider"] == "mock"
    assert public["mock_mode"] is True


def test_local_base_url_bypasses_system_proxy():
    """本机服务不该被 HTTP_PROXY 接管；远程地址仍要保留代理能力。"""
    local = llm_provider.OpenAICompatProvider({"base_url": "http://127.0.0.1:8080/v1"})
    assert local.trust_env is False
    localhost = llm_provider.OpenAICompatProvider({"base_url": "http://localhost:8080/v1"})
    assert localhost.trust_env is False
    remote = llm_provider.OpenAICompatProvider({"base_url": "https://api.example.com/v1"})
    assert remote.trust_env is True


def _force_real_provider(monkeypatch, tmp_path):
    import yaml

    cfg = tmp_path / "settings.yaml"
    cfg.write_text(yaml.safe_dump({"llm": {
        "mock_mode": False, "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:8080/v1", "model": "nyx",
    }}), encoding="utf-8")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", cfg)


def test_connection_warns_when_model_runs_on_cpu(monkeypatch, tmp_path):
    """5 token/秒 必须被明确点出来是没吃到 GPU，而不是只说「连接成功」。"""
    _force_real_provider(monkeypatch, tmp_path)
    monkeypatch.setattr(
        llm_provider.OpenAICompatProvider, "probe",
        lambda self: llm_provider.GenerationResult(
            text="1 2 3", usage={"timings": {"predicted_per_second": 5.4}},
            provider="openai_compatible", model="nyx",
        ),
    )
    out = settings_service.test_connection()
    assert out["ok"] is True
    assert "5.4" in out["message"]
    assert "GPU" in out["message"]


def test_connection_quiet_when_speed_is_normal(monkeypatch, tmp_path):
    _force_real_provider(monkeypatch, tmp_path)
    monkeypatch.setattr(
        llm_provider.OpenAICompatProvider, "probe",
        lambda self: llm_provider.GenerationResult(
            text="1 2 3", usage={"timings": {"predicted_per_second": 82.0}},
            provider="openai_compatible", model="nyx",
        ),
    )
    out = settings_service.test_connection()
    assert out["ok"] is True
    assert "82.0" in out["message"]
    assert "GPU" not in out["message"]


def test_short_tasks_do_not_carry_the_long_form_budget(world_a):
    """世界观 / 人物仍是长文预算；剧情、记忆、字段任务要有各自的上限。"""
    from backend.core import panel_service, prompt_builder

    assert prompt_builder.build_world_request("一句话").max_tokens >= 8192

    schema = panel_service.load_schema(world_a)
    story = prompt_builder.build_story_request(world_a, {}, schema, [], "", "行动", "", {}, 1)
    assert story.max_tokens <= 4096

    assert prompt_builder.build_memory_request(world_a, "", [], "", "", []).max_tokens <= 1536
    assert prompt_builder.build_backfill_request(
        world_a, {}, {}, [], "", [], [], "", 1).max_tokens <= 2048
    assert prompt_builder.build_reconcile_request(
        world_a, {}, [], {}, "", [], "").max_tokens <= 2048
