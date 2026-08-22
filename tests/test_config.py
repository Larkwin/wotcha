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
