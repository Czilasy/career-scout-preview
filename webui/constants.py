"""Centralized magic-number constants shared across webui backend modules.

Numeric literals that appear in multiple modules (store.py, app.py,
discovery_runner.py) are collected here so they have a single source of truth.
Already-named class/module constants (e.g. ``_REUSE_FRESHNESS_HOURS``,
``MAX_DETAIL_BUDGET``) are not duplicated here.
"""

# cleanup_expired_jobs / preview_cleanup_expired_jobs default retention window (days)
CLEANUP_EXPIRED_DAYS = 30

# Default detail-page scrape budget (number of jobs to fetch details for)
DETAIL_BUDGET = 60

# Feedback count interval that triggers AI settings refresh
FEEDBACK_THRESHOLD = 5

# Upper bound for list pagination endpoints
LIST_LIMIT = 100

# Number of tail log lines returned in task snapshots
LOG_TAIL_LINES = 50
