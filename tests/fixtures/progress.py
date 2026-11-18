"""`RecordingProgress`: a `Progress` that records what it is told, for tests (owner request M.2)."""

from dataclasses import dataclass, field


@dataclass
class RecordingProgress:
    """Each call as a tuple: `("step", message)`, `("started", name, position, total)` or
    `("finished", name, position, total, exit_code)`. `seconds` holds each finished stage's time, in order."""

    events: list[tuple[object, ...]] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)

    def step(self, message: str) -> None:
        self.events.append(("step", message))

    def stage_started(self, name: str, position: int, total: int) -> None:
        self.events.append(("started", name, position, total))

    def stage_finished(self, name: str, position: int, total: int, *, exit_code: int | None, seconds: float) -> None:
        self.events.append(("finished", name, position, total, exit_code))
        self.seconds.append(seconds)

    def stage_events(self) -> list[tuple[object, ...]]:
        return [event for event in self.events if event[0] != "step"]

    def steps(self) -> list[str]:
        return [str(event[1]) for event in self.events if event[0] == "step"]
