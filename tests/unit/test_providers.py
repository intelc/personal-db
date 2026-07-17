import yaml

from personal_db.core.config import Config
from personal_db.core.providers import resolve_email_context_provider
from personal_db.core.sources import install_source_template


def test_resolve_email_context_provider_returns_none_when_unconfigured(tmp_root):
    cfg = Config(root=tmp_root)
    assert resolve_email_context_provider(cfg) is None


def test_resolve_email_context_provider_grandfathers_spark_email_when_installed(tmp_root):
    cfg = Config(root=tmp_root)
    install_source_template(cfg, "spark_email")

    provider = resolve_email_context_provider(cfg)

    assert provider is not None
    assert provider.name == "email"


def test_resolve_email_context_provider_uses_explicit_config(tmp_root):
    cfg = Config(root=tmp_root)
    install_source_template(cfg, "spark_email")
    (tmp_root / "config.yaml").write_text(
        yaml.safe_dump({"providers": {"email_context": "spark_email"}})
    )

    provider = resolve_email_context_provider(cfg)

    assert provider is not None


def test_resolve_email_context_provider_unknown_name_returns_none(tmp_root):
    cfg = Config(root=tmp_root)
    (tmp_root / "config.yaml").write_text(
        yaml.safe_dump({"providers": {"email_context": "not_a_real_provider"}})
    )

    assert resolve_email_context_provider(cfg) is None


def test_resolve_email_context_provider_no_source_and_no_config_returns_none(tmp_root):
    cfg = Config(root=tmp_root)
    # spark_email source not installed, config.yaml absent -> no grandfathering.
    assert resolve_email_context_provider(cfg) is None
