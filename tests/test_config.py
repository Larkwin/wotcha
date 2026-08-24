from wotcha.config import settings


def test_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("WOTCHA_AWS_REGION", "ca-central-1")
    monkeypatch.setenv("WOTCHA_TABLE_NAME", "wotcha-test")
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", "demo")
    settings.cache_clear()
    s = settings()
    assert s.aws_region == "ca-central-1"
    assert s.table_name == "wotcha-test"
    assert s.household_id == "demo"
    # documented defaults
    assert s.bedrock_model_id == "us.anthropic.claude-sonnet-4-6"
    assert s.link_secret_name == "wotcha/link-signing-key"


def test_settings_is_cached(monkeypatch):
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", "first")
    settings.cache_clear()
    assert settings().household_id == "first"
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", "second")
    assert settings().household_id == "first"  # cached, not re-read


def test_the_liaison_has_its_own_model_setting(monkeypatch):
    """Per-agent model selection is a spec section 13 requirement: the answer
    may legitimately differ by agent, and the Planner and the Liaison must
    not be forced to move together."""
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", "demo")
    monkeypatch.setenv("WOTCHA_LIAISON_MODEL_ID", "ca.amazon.nova-lite-v1:0")
    settings.cache_clear()
    assert settings().liaison_model_id == "ca.amazon.nova-lite-v1:0"


def test_the_liaison_defaults_to_a_cheap_rung(monkeypatch):
    """Extraction against a seven-item list is the kind of work section 13
    expects a small model to handle. Starting there is what makes the first
    real numbers interesting."""
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", "demo")
    monkeypatch.delenv("WOTCHA_LIAISON_MODEL_ID", raising=False)
    settings.cache_clear()
    assert settings().liaison_model_id == "ca.amazon.nova-lite-v1:0"
