"""`NullProgress`, the `Progress` that reports nothing, and `duration()` for progress lines (owner request M.2)."""


class NullProgress:
    """Reports nothing."""

    def step(self, message: str) -> None:
        pass

    def stage_started(self, name: str, position: int, total: int) -> None:
        pass

    def stage_finished(self, name: str, position: int, total: int, *, exit_code: int | None, seconds: float) -> None:
        pass


def duration(seconds: float) -> str:
    """A time for people: `4.2s` under a minute, else `3m 05s`."""
    if round(seconds, 1) < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m {rest:02d}s"
