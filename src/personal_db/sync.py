"""Backward-compat shim: personal_db.sync moved to personal_db.core.sync.

Kept here (rather than deleted outright) because at least one test file we're
not permitted to edit in this change still imports the old path directly.
Internal code must import personal_db.core.sync — never this shim.
"""

from personal_db.core.sync import *  # noqa: F401,F403
