# interfaces/

Capability protocols live here.

This package is a placeholder introduced in Phase 1a of the boundary
refactor (see `concurrent-dazzling-bengio.md`, section 1c). It will hold
`Protocol` definitions such as `email_context.py` that let services depend
on a capability contract instead of importing a concrete extension (e.g.
finance enrichment depending on an `EmailContextProvider` protocol instead
of importing Spark directly). No protocols are defined yet — that work
lands in a later phase.
