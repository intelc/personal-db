"""Backward-compat shim: personal_db.ui.viz moved to personal_db.services.ui.viz.

Kept here (rather than deleted outright) because at least one test file we're
not permitted to edit in this change still imports the old path directly.
Internal code must import personal_db.services.ui.viz — never this shim.
"""

from personal_db.services.ui.viz import *  # noqa: F401,F403
