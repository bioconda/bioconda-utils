"""
Logging setup and terminal helpers.

All terminal output goes through Rich: console logging on stderr,
progress bars and spinners on stderr, and data tables on stdout.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Collection, Iterable
from itertools import islice
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .._types import RecipePath
from .progress import ProgressDisplay

logger = logging.getLogger(__name__)

console = Console()
err_console = Console(stderr=True)
progress_display = ProgressDisplay(err_console)


class LogFuncFilter:
    """Logging filter capping the number of messages emitted from given function

    Arguments:
      func: The function for which to filter log messages
      trunc_msg: The message to emit when logging is truncated, to inform user that
                  messages will from now on be hidden.
      max_lines: Max number of log messages to allow to pass
      consecutive: If true, filter applies to consecutive messages and resets
                      if a message from a different source is encountered.

    Fixme:
      The implementation  assumes that **func** uses a logger initialized with
      ``getLogger(__name__)``.
    """

    def __init__(
        self,
        func,
        trunc_msg: str | None = None,
        max_lines: int = 0,
        consecutive: bool = True,
    ) -> None:
        self.func = func
        self.max_lines = max_lines + 1
        self.cur_max_lines = max_lines + 1
        self.consecutive = consecutive
        self.trunc_msg = trunc_msg

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == self.func.__module__
            and record.funcName == self.func.__name__
        ):
            if self.cur_max_lines > 1:
                self.cur_max_lines -= 1
                return True
            if self.cur_max_lines == 1 and self.trunc_msg:
                self.cur_max_lines -= 1
                record.msg = self.trunc_msg
                return True
            return False
        if self.consecutive:
            self.cur_max_lines = self.max_lines
        return True


class LoggingSourceRenameFilter:
    """Logging filter for abbreviating module name in logs

    Maps ``bioconda_utils`` to ``BIOCONDA`` and for everything else
    to just the top level package uppercased.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("bioconda_utils"):
            record.name = "BIOCONDA"
        else:
            record.name = record.name.split(".")[0].upper()
        return True


def setup_logger(
    name: str = "bioconda_utils",
    loglevel: str | int = logging.INFO,
    logfile: Path | None = None,
    logfile_level: str | int = logging.DEBUG,
    log_command_max_lines=None,
) -> logging.Logger:
    """Set up logging for bioconda-utils using Rich on stderr.

    Args:
      name: Module name for which to get a logger (``__name__``)
      loglevel: Log level, can be name or int level
      logfile: File to log to as well
      logfile_level: Log level for file logging
      log_command_max_lines: Truncate ``support.subproc.run`` output after
        this many lines.

    Returns:
      A new logger
    """
    new_logger = logging.getLogger(name)
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    if logfile:
        if isinstance(logfile_level, str):
            logfile_level = getattr(logging, logfile_level.upper())
        log_file_handler = logging.FileHandler(logfile)
        log_file_handler.setLevel(logfile_level)
        log_file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root_logger.addHandler(log_file_handler)
    else:
        logfile_level = logging.FATAL

    if isinstance(loglevel, str):
        loglevel = getattr(logging, loglevel.upper())
    if isinstance(logfile_level, str):
        logfile_level = getattr(logging, logfile_level.upper())

    root_logger.setLevel(min(loglevel, logfile_level))

    rich_handler = RichHandler(
        console=err_console,
        rich_tracebacks=True,
        # Log records contain recipe metadata and subprocess output, so they
        # must always be treated as literal text.  Enabling markup globally
        # makes bracketed output disappear and unmatched closing tags raise
        # MarkupError from inside the logging handler.
        markup=False,
        show_time=True,
        show_path=False,
        omit_repeated_times=False,
        log_time_format="[%H:%M:%S]",
    )
    rich_handler.setLevel(loglevel)
    rich_handler.addFilter(LoggingSourceRenameFilter())
    root_logger.addHandler(rich_handler)

    if log_command_max_lines is not None:
        from .subproc import run

        log_filter = LogFuncFilter(
            run, "Command output truncated", log_command_max_lines
        )
        rich_handler.addFilter(log_filter)

    return new_logger


def format_recipes(recipes: Iterable[RecipePath | Path], separator: str = ", ") -> str:
    """Logging helper rendering a collection of recipes as recipe paths

    ``RecipePath`` renders as its path, but a *list* of them would be rendered
    with the tuple ``repr``.  Use this to log collections of recipes.

    Args:
      recipes: Recipes to render.
      separator: String to place between recipe paths.
    Returns:
      A string like "htslib/1.19, samtools/1.21" or "" if there are no recipes.
    """
    return separator.join(str(recipe) for recipe in recipes)


def ellipsize_recipes(
    recipes: Collection[os.PathLike[str]],
    recipe_folder: Path,
    n: int = 5,
    m: int = 50,
) -> str:
    """Logging helper showing recipe list

    Args:
      recipes: List of recipes
      recipe_folder: Folder name to strip from recipes.
      n: Show at most this number of recipes, with "..." if more are found.
      m: Don't show anything if more recipes than this
          (pointless to show first 5 of 5000)
    Returns:
      A string like " (htslib, samtools, ...)" or ""
    """
    if not recipes or len(recipes) > m:
        return ""
    recipe_paths: list[Path] = [Path(recipe) for recipe in islice(recipes, n)]
    if len(recipes) > n:
        append = ", ..."
    else:
        append = ""
    return (
        " ("
        + ", ".join(
            os.fspath(recipe.relative_to(recipe_folder)) for recipe in recipe_paths
        )
        + append
        + ")"
    )
