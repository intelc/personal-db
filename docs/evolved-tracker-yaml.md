# Evolved Tracker YAML Shape

This document sketches the next tracker manifest shape for a cloud-capable,
agent-generated personal_db. It is intentionally an evolution of the current
`manifest.yaml`, not a replacement.

The current manifest already has the right center of gravity:

- tracker identity and description
- setup and permission steps
- schedule
- table declarations with column semantics
- related entities
- local-only marker

The evolved shape keeps those fields and adds optional metadata for cloud sync,
platform runtime, capabilities, transforms, validation, provenance, and package
origin.

## Design Principles

Do not enforce global data layers such as `raw`, `normalized`, or `derived`.
Those are useful words for humans, but they should not be required runtime
structure.

Instead, a tracker declares:

- which tables exist
- which transforms read and write those tables
- which tables sync to cloud
- which platform capabilities are needed
- which validation gates must pass before promotion
- how derived rows preserve provenance

In short:

```text
tables + transforms + sync policy + provenance + tests = data topology
```

This mirrors the useful part of Datus: agent-generated artifacts are only
promoted after their contracts validate and their tests pass.

## Current Minimal Shape

Current manifests look like this:

```yaml
name: imessage
description: iMessage messages from ~/Library/Messages/chat.db
permission_type: full_disk_access
setup_steps:
  - type: fda_check
    probe_path: "~/Library/Messages/chat.db"

schedule:
  every: 30m

time_column: sent_at
granularity: event

schema:
  tables:
    imessage_messages:
      columns:
        id: {type: INTEGER, semantic: "rowid in chat.db"}
        text: {type: TEXT, semantic: "message body"}
        sent_at: {type: TEXT, semantic: "ISO-8601 UTC"}

related_entities: ["people"]
local_only: true
```

That should remain valid.

## Proposed Additive Shape

All new sections below should be optional at first.

```yaml
manifest_version: 2

name: commitments
description: Commitments extracted from meetings, messages, and notes

package:
  origin: local_generated # bundled | registry | local_generated | user_authored
  generated_by: personal_db_builder
  generated_at: "2026-06-04T12:00:00Z"

runtime:
  platforms: [macos, cloud] # macos | ios | android | cloud

permission_type: none
setup_steps: []

schedule:
  every: 30m

time_column: created_at
granularity: event
related_entities: ["people", "topics"]
local_only: false

capabilities:
  reads:
    - table: imessage_messages
    - table: calendar_events
    - table: omi_transcripts
  actions: []

schema:
  tables:
    commitments:
      primary_key: [commitment_id]
      sync:
        cloud: true
        mode: upsert
      columns:
        commitment_id:
          type: TEXT
          semantic: stable commitment id
        source_table:
          type: TEXT
          semantic: table where the strongest evidence came from
        source_id:
          type: TEXT
          semantic: row id in the source table
        person_id:
          type: INTEGER
          semantic: person associated with the commitment
        text:
          type: TEXT
          semantic: commitment statement
          privacy: sensitive
        due_at:
          type: TEXT
          semantic: optional due date
        confidence:
          type: REAL
          semantic: extraction confidence from 0 to 1
        created_at:
          type: TEXT
          semantic: extraction timestamp

transforms:
  extract_commitments:
    reads:
      - imessage_messages
      - calendar_events
      - omi_transcripts
    writes:
      - commitments
    kind: python
    entrypoint: ingest.extract_commitments

validation:
  required:
    - manifest_schema
    - schema_apply
    - transform_dag
    - dry_run
    - idempotency
  smoke_queries:
    - "SELECT COUNT(*) FROM commitments"
  fixtures:
    - tests/fixtures/basic_commitments.json

provenance:
  required: true
  source_columns:
    table: source_table
    id: source_id
```

## Section Notes

### `manifest_version`

Optional version marker for future schema evolution. Current manifests without
this field are treated as version 1.

### `package`

Describes where the tracker came from. This is product and trust metadata, not
execution logic.

Suggested origins:

- `bundled`: shipped with personal_db
- `registry`: downloaded from a signed tracker registry
- `local_generated`: created by the local agent builder
- `user_authored`: written by the user directly

This helps the UI explain risk and support rollback.

### `runtime`

Declares where the tracker or transform can run.

Examples:

```yaml
runtime:
  platforms: [macos]
```

Mac-only. The device must be online and awake. This is implied by `macos`; no
extra `requires_awake` flag is needed.

```yaml
runtime:
  platforms: [cloud]
```

Cloud-native. It can run in SaaS without a local device.

```yaml
runtime:
  platforms: [macos, cloud]
```

Portable or hybrid. It can run wherever its declared input tables or
capabilities are available.

### `capabilities`

Declares what the tracker needs to read or do. This is intentionally broader
than `permission_type`.

Early examples can reference tables:

```yaml
capabilities:
  reads:
    - table: imessage_messages
```

Later examples can reference signed-helper capabilities:

```yaml
capabilities:
  reads:
    - id: macos.messages.read
```

For v1 distribution, the signed daemon may still run tracker Python directly
with broad permissions. Later, these capability declarations become the input
to a granular capability broker.

### Table `sync`

Cloud sync is table-level. A tracker can keep some tables local while syncing
others.

```yaml
schema:
  tables:
    raw_local_messages:
      sync:
        cloud: false
    commitments:
      sync:
        cloud: true
        mode: upsert
```

Cloud-canonical does not mean cloud-exhaustive. The cloud is canonical for the
tables the tracker declares syncable.

Useful future fields:

```yaml
sync:
  cloud: true
  mode: upsert       # upsert | append
  retention: 365d
  redaction: default # default | contentless | none
```

### Table `primary_key`

The current framework gets primary keys from `schema.sql` and `t.upsert(...,
key=[...])`. Adding `primary_key` to YAML makes the contract easier for agents
to inspect and validate.

It should match the SQLite schema and the ingest code.

### Column `privacy`

Optional metadata to help cloud policy, UI warnings, and future redaction.

Examples:

```yaml
privacy: sensitive
privacy: pii
privacy: public
```

The runtime should not depend on this at first. It is a hint for validation and
policy.

### `transforms`

Today transforms can be declared in Python with:

```python
@transform(writes="enriched", depends_on=["raw"])
def enrich(t, ctx): ...
```

The YAML `transforms` section is the declarative counterpart. It lets the
builder, UI, and cloud planner see the topology without importing Python.

At first, Python decorators can remain the execution source of truth. The
validator should check that YAML and discovered decorators agree when both are
present.

### `validation`

Declares promotion gates for generated or registry trackers.

Suggested gates:

- `manifest_schema`: YAML parses and Pydantic accepts it
- `schema_apply`: `schema.sql` applies cleanly to an empty DB
- `schema_manifest_match`: manifest columns match SQLite tables
- `transform_dag`: transform dependencies are valid and acyclic
- `dry_run`: sync/backfill can run against fixture or sampled data
- `idempotency`: running twice does not duplicate rows
- `cloud_policy`: syncable tables have valid keys and privacy policy
- `benchmark`: tracker improves or preserves expected answers

The Datus lesson is that generation is not complete when files are written.
Generation is complete when validation passes and the artifact is promoted.

### `provenance`

Derived tables should be explainable. At minimum, a derived row should be able
to point back to source table and source id, or to an auxiliary provenance
ledger.

Simple inline provenance:

```yaml
provenance:
  required: true
  source_columns:
    table: source_table
    id: source_id
```

Future ledger-based provenance:

```yaml
provenance:
  required: true
  ledger: true
```

The exact storage can evolve; the manifest should express the expectation.

## Migration Strategy

1. Keep current manifests valid.
2. Extend the Pydantic model with optional fields only.
3. Teach `validate_tracker` to warn on inconsistent optional metadata.
4. Add table-level `sync` as metadata before building cloud sync.
5. Add YAML `transforms` as a read-only topology declaration before making it
   executable.
6. Require stricter fields only for generated/registry trackers once the
   builder and validator can produce them reliably.

## Relationship To Datus

Datus treats generated context artifacts as contracts:

```text
generate artifact -> validate -> publish -> retrieve in future runs
```

personal_db should do the same for tracker topology:

```text
generate tracker proposal -> validate -> dry-run -> promote -> sync/use
```

The YAML manifest is the tracker contract. The agent builder should be allowed
to modify that contract only through a proposal, and the proposal should be
promoted only after the declared validation gates pass.
