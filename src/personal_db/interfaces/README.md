# interfaces/

Capability protocols live here.

- `email_context.py` — `EvidenceRef`/`ContextResult` (shared provenance
  vocabulary) plus the `EmailContextProvider` Protocol that
  `enrichments.finance` and the core `email_search_receipts`/
  `email_read_thread` MCP tools depend on instead of importing a concrete
  provider directly.

Concrete providers are resolved by name via `core/providers.py` — see that
module for the resolution order (explicit `config.yaml: providers.
email_context`, falling back to `spark_email` if that source is installed
and nothing is configured).

Import direction: `interfaces` has no dependency on `core`, but `core` (e.g.
`core/enrichment_queue.py`) may import from `interfaces` — the layers
contract places `interfaces` below `core`.
