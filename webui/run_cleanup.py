"""History data is retained until the user explicitly deletes a round."""

from __future__ import annotations


class HistoryDeletionBlocked(RuntimeError):
    """The round still owns an unfinished task and cannot be deleted."""


def delete_run(store, run_id: str, profile_id: str | None = None) -> bool:
    """Delete the selected round/track and its associated database records."""
    target = str(run_id or "")
    if not target:
        return False
    return bool(store.delete_run_closure(target, profile_id=profile_id))
