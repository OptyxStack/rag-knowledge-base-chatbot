"""Tests for branding prompt helpers."""

from app.services import branding_config


def test_get_system_prompt_includes_pass_weak_guidance(monkeypatch):
    monkeypatch.setitem(branding_config._cache, "persona", None)
    monkeypatch.setitem(branding_config._cache, "prompt_domain", None)

    prompt = branding_config.get_system_prompt()

    assert "PASS_WEAK" in prompt
    assert 'decision="PASS"' in prompt
