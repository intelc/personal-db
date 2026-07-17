"""Capability-provider resolution.

Maps a configured provider name (`config.yaml: providers.<capability>`) to a
concrete implementation, so services and enrichments depend on this module
(core) plus a `interfaces.*` Protocol instead of importing a concrete
extension package (`context_providers.*`, `remote_sources.*`) directly.

Design choice — "the resolver knows the one built-in name": rather than a
self-registration scheme (which would require something to import
`context_providers.email` early enough for its registration side effect to
run before resolution is needed — awkward given daemon/MCP no longer import
it at all), `resolve_email_context_provider` hardcodes the one built-in
provider name ("spark_email") and imports its concrete class lazily, inside
the function body, only when that name is actually selected. This is the
one narrow, intentional place core is allowed to reference a concrete
extension by name; every other core/services module must not.
"""

from __future__ import annotations

from personal_db.core.config import Config
from personal_db.interfaces.email_context import EmailContextProvider

_BUILTIN_EMAIL_CONTEXT_PROVIDER = "spark_email"


def _configured_email_context_provider_name(cfg: Config) -> str | None:
    config_path = cfg.root / "config.yaml"
    if not config_path.is_file():
        return None
    import yaml

    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    name = providers.get("email_context")
    return str(name).strip() or None if name else None


def _spark_email_source_installed(cfg: Config) -> bool:
    return (cfg.sources_dir / "spark_email" / "source.yaml").is_file()


def resolve_email_context_provider(cfg: Config) -> EmailContextProvider | None:
    """Resolve the configured email-context provider, or None if unconfigured.

    Resolution order:
      1. ``config.yaml: providers.email_context: <name>`` if present.
      2. Grandfathering: if unset *and* the `spark_email` source is
         installed, default to it — this matches pre-1c behavior for
         existing installs that never had to configure this explicitly.
         New installs should set `providers: {email_context: spark_email}`
         in config.yaml to avoid depending on this fallback (the setup
         wizard can populate it in a later phase).

    Returns None (never raises) when no provider can be resolved or
    construction fails; callers should degrade with a clear "no email
    context provider configured" error/result rather than propagate an
    import or construction failure.
    """
    name = _configured_email_context_provider_name(cfg)
    if name is None and _spark_email_source_installed(cfg):
        name = _BUILTIN_EMAIL_CONTEXT_PROVIDER
    if name is None:
        return None
    if name == _BUILTIN_EMAIL_CONTEXT_PROVIDER:
        try:
            from personal_db.context_providers.email import SparkEmailContextProvider
        except ImportError:
            return None
        try:
            return SparkEmailContextProvider.from_config(cfg)
        except Exception:
            return None
    return None
