"""Capability protocols for personal_db extensions.

Protocol definitions here let services (daemon, MCP server) and extensions
(enrichments.finance) depend on a capability contract instead of a concrete
extension implementation — e.g. ``email_context.EmailContextProvider``, which
``enrichments.finance`` and the core email MCP tools depend on instead of
importing ``context_providers.email`` / ``remote_sources.spark`` directly.
Concrete providers are resolved by name via ``core.providers``.
"""
