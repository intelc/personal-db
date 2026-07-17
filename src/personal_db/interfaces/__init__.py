"""Capability protocols for personal_db extensions.

This package is the future home of Protocol definitions that let services
(daemon, MCP server) depend on a capability contract instead of a concrete
extension implementation — e.g. an ``EmailContextProvider`` protocol that
``enrichments/finance`` depends on instead of importing
``context_providers.email`` / ``remote_sources.spark`` directly.

Content arrives in a later refactor phase (see interfaces/README.md).
"""
