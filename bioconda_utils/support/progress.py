"""Application progress tasks rendered by one ordinary Rich Live display.

The command owns ``live``; operations own individual tasks. Programmatic callers
can use ``with progress_display.live:`` to animate a group of operations too.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from multiprocessing import parent_process

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


class ProgressDisplay:
    """Configure Rich displays and scope tasks without starting their renderers."""

    def __init__(self, console: Console) -> None:
        self.counts = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            auto_refresh=False,
        )
        self.downloads = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            auto_refresh=False,
        )
        self.statuses = Progress(
            SpinnerColumn("dots"),
            TextColumn("{task.description}", markup=False),
            console=console,
            auto_refresh=False,
        )
        self.live = Live(
            Group(self.counts, self.downloads, self.statuses),
            console=console,
            transient=True,
            refresh_per_second=10,
            # The command may also write serialized results to stdout.
            redirect_stdout=False,
        )

    def count_task(
        self, description: str, *, total: float | None = None
    ) -> AbstractContextManager[tuple[Progress, TaskID]]:
        return self._task(self.counts, description, total=total)

    def download_task(
        self, description: str, *, total: float | None = None
    ) -> AbstractContextManager[tuple[Progress, TaskID]]:
        return self._task(self.downloads, description, total=total)

    def status(
        self, description: str
    ) -> AbstractContextManager[tuple[Progress, TaskID]]:
        return self._task(self.statuses, description)

    @contextmanager
    def _task(
        self,
        progress: Progress,
        description: str,
        *,
        total: float | None = None,
    ) -> Iterator[tuple[Progress, TaskID]]:
        # Workers leave terminal output to the parent's item counter. Use a
        # local disabled Progress so forked workers never touch inherited locks.
        if parent_process() is not None:
            progress = Progress(*progress.columns, auto_refresh=False, disable=True)
        task_id = progress.add_task(description, total=total)
        try:
            yield progress, task_id
        finally:
            progress.remove_task(task_id)
