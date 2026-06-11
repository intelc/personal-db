# Spark Email Remote Source

This source uses the Spark Desktop CLI as a live external data source.

Requirements:

- Spark Desktop is running in the user's macOS session.
- The `spark` CLI is available on `PATH`.
- Account access is enabled in Spark Desktop under Settings -> AI Agents.

The installed `source.yaml` can be edited to change the command name/path or
timeout. It does not store email data in `db.sqlite`.
