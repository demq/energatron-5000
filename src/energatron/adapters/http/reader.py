"""Running the shared read paths for the web adapter (P10 spec §4): one at a time, a fresh workspace context per call,
and the CLI's own envelope as the answer, redacted the same way.
"""

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from energatron.adapters import envelope, reads
from energatron.core.errors import EnergatronError, InternalError
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.privacy import DenyEntry

_log = logging.getLogger(__name__)

STATUS_BY_EXIT: Final = {0: 200, 1: 500, 2: 400, 3: 422, 4: 409, 5: 403, 6: 502, 7: 422, 8: 207}
"""The HTTP status for each exit code (P10 spec §5.1)."""


@dataclass(frozen=True)
class Answer:
    """One read path's outcome: the HTTP status and the redacted envelope document."""

    status: int
    document: dict[str, Any]


@dataclass
class Reader:
    """Runs read paths against one workspace, one at a time (P10 spec §4)."""

    paths: WorkspacePaths
    allow_documents: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def answer(self, path: reads.ReadPath, values: Mapping[str, object] | reads.Args) -> Answer:
        """Parse the arguments, run the read path, and build its redacted envelope, as `--json` would print it."""
        denylist: tuple[DenyEntry, ...] = ()

        def hold(manual: tuple[DenyEntry, ...]) -> None:
            nonlocal denylist
            denylist = manual

        with self._lock:
            try:
                args = values if isinstance(values, reads.Args) else path.parse(values)
                context = None
                if path.needs_workspace:
                    context, denylist = reads.open_context(self.paths, on_manual=hold)
                result = path.run(context, args)
                document = envelope.success(path.command, result, result.warnings)
            except EnergatronError as exc:
                document = envelope.failure(path.command, exc)
            except Exception as exc:
                _log.debug("internal error", exc_info=True)
                error = InternalError(
                    "An internal error occurred; rerun serve with --debug for the traceback.",
                    exception=type(exc).__name__,
                )
                document = envelope.failure(path.command, error)
            return Answer(STATUS_BY_EXIT[document.exit_code], envelope.redacted(document, denylist))


def failed(command: str | None, error: EnergatronError) -> Answer:
    """An error found before a read path runs (a bad query, a body too large): no denylist is loaded, and none is
    needed, because the message and details name only parameters."""
    return Answer(STATUS_BY_EXIT[error.exit_code], envelope.redacted(envelope.failure(command, error), ()))
