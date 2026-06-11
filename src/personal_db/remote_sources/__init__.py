"""Remote/live data sources for personal_db.

Remote sources expose callable APIs over external systems without requiring
personal_db to materialize the whole source into db.sqlite.
"""

from personal_db.remote_sources.spark import SparkEmailSource

__all__ = ["SparkEmailSource"]
