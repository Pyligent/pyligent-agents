"""Configuration holds structure. The environment holds secrets. Never both."""

from __future__ import annotations

import pytest

from pyligent_agents.config_file import Config, ConfigError, load, parse, scan_for_secrets

GOOD = """
# structure, not secrets
extractor:
  provider: anthropic
  model: claude-sonnet-5
  api_key_env: ANTHROPIC_API_KEY
ingestion:
  backend: docling
  local_ocr: true
gates:
  csa:
    version: v7
    strict: false
"""


def test_nested_configuration_parses_without_a_yaml_dependency():
    c = Config(values=parse(GOOD))
    assert c.get("extractor.model") == "claude-sonnet-5"
    assert c.get("gates.csa.version") == "v7"
    assert c.get("ingestion.local_ocr") is True
    assert c.get("gates.csa.strict") is False
    assert c.get("nothing.here", "fallback") == "fallback"


def test_a_secret_is_read_from_the_environment_the_config_names(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-value-that-lives-only-here")
    c = Config(values=parse(GOOD))
    assert c.secret("extractor.api_key_env").startswith("sk-ant-")


def test_a_named_variable_that_is_not_set_fails_loudly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = Config(values=parse(GOOD))
    with pytest.raises(ConfigError, match="not set in this environment"):
        c.secret("extractor.api_key_env")


@pytest.mark.parametrize("secret,what", [
    ("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA", "Anthropic"),
    ("AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "Google"),
    ("AKIAIOSFODNN7EXAMPLE", "AWS"),
    ("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "GitHub"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
])
def test_credential_shapes_are_detected(secret, what):
    assert scan_for_secrets(f"provider:\n  key: {secret}\n")


def test_a_reference_key_holding_a_value_rather_than_a_name_is_caught():
    """`api_key_env` must name a variable. A value there is the whole mistake."""
    findings = scan_for_secrets("extractor:\n  api_key_env: my-actual-key-material\n")
    assert any("not a variable name" in f for f in findings)
    assert not scan_for_secrets("extractor:\n  api_key_env: ANTHROPIC_API_KEY\n")


def test_loading_a_file_with_a_credential_is_refused(tmp_path):
    """The control that matters: refusal at load, not a linter someone remembers."""
    p = tmp_path / "pyligent.yaml"
    p.write_text("extractor:\n  api_key_env: sk-ant-api03-AAAAAAAAAAAAAAAAAAAA\n")
    with pytest.raises(ConfigError) as exc:
        load(p)
    # The message has to say why, because the person reading it is about to
    # argue that it is fine.
    assert "does not remove it from history" in str(exc.value)


def test_a_clean_file_loads(tmp_path):
    p = tmp_path / "pyligent.yaml"
    p.write_text(GOOD)
    assert load(p).get("extractor.provider") == "anthropic"
