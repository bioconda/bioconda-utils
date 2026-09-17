"""
Logging setup and terminal helpers.

Everything that shapes console output lives here: logger configuration,
progress-bar aware log handlers, and small helpers that keep the
terminal responsive during long operations.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Collection
from pathlib import Path
from threading import Event, Thread

import tqdm as _tqdm
from colorlog import ColoredFormatter

from .subproc import run

logger = logging.getLogger(__name__)


class TqdmHandler(logging.StreamHandler):
    """Tqdm aware logging StreamHandler

    Passes all log writes through tqdm to allow progress bars and log
    messages to coexist without clobbering terminal
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # initialise internal tqdm lock so that we can use tqdm.write
        _tqdm.tqdm(disable=True, total=0)

    def emit(self, record):
        _tqdm.tqdm.write(self.format(record))


def tqdm(*args, **kwargs):
    """Wrapper around TQDM handling disable

    Logging is disabled if:

    - ``TERM`` is set to ``dumb``
    - ``CIRCLECI`` is set to ``true``
    - the effective log level of the is lower than set via ``loglevel``

    Args:
      loglevel: logging loglevel (the number, so logging.INFO)
      logger: local logger (in case it has different effective log level)
    """
    term_ok = (
        sys.stderr.isatty()
        and os.environ.get("TERM", "") != "dumb"
        and os.environ.get("CIRCLECI", "") != "true"
        and os.environ.get("CI", "") != "true"
    )
    loglevel_ok = kwargs.get("logger", logger).getEffectiveLevel() <= kwargs.get(
        "loglevel", logging.INFO
    )
    kwargs["disable"] = bool(kwargs.get("disable")) or not (term_ok and loglevel_ok)
    return _tqdm.tqdm(*args, **kwargs)


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
    prefix: str = "BIOCONDA ",
    msgfmt: str = (
        "%(asctime)s %(log_color)s%(name)s %(levelname)s%(reset)s %(message)s"
    ),
    datefmt: str = "%H:%M:%S",
) -> logging.Logger:
    """Set up logging for bioconda-utils

    Args:
      name: Module name for which to get a logger (``__name__``)
      loglevel: Log level, can be name or int level
      logfile: File to log to as well
      logfile_level: Log level for file logging
      prefix: Prefix to add to our log messages
      msgfmt: Format for messages
      datefmt: Format for dates

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
        log_file_formatter = logging.Formatter(
            msgfmt.replace("%(log_color)s", "")
            .replace("%(reset)s", "")
            .format(prefix=prefix),
            datefmt=None,
        )
        log_file_handler.setFormatter(log_file_formatter)
        root_logger.addHandler(log_file_handler)
    else:
        logfile_level = logging.FATAL

    if isinstance(loglevel, str):
        loglevel = getattr(logging, loglevel.upper())

    # Base logger is set to the lowest of console or file logging
    root_logger.setLevel(min(loglevel, logfile_level))

    # Console logging is passed through TqdmHandler so that the progress bar does not
    # get broken by log lines emitted.
    log_stream_handler = TqdmHandler()
    if loglevel:
        log_stream_handler.setLevel(loglevel)

    log_stream_handler.setFormatter(
        ColoredFormatter(
            msgfmt.format(prefix=prefix),
            datefmt=datefmt,
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red",
            },
        )
    )
    log_stream_handler.addFilter(LoggingSourceRenameFilter())
    root_logger.addHandler(log_stream_handler)

    # Add filter for `utils.run` to truncate after n lines emitted.
    # We do this here rather than in `utils.run` so that it can be configured
    # from the CLI more easily
    if log_command_max_lines is not None:
        log_filter = LogFuncFilter(
            run, "Command output truncated", log_command_max_lines
        )
        log_stream_handler.addFilter(log_filter)

    return new_logger


def ellipsize_recipes(
    recipes: Collection[Path], recipe_folder: Path, n: int = 5, m: int = 50
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
    if len(recipes) > n:
        recipes = list(recipes)[:n]
        append = ", ..."
    else:
        append = ""
    return (
        " ("
        + ", ".join(os.fspath(recipe.relative_to(recipe_folder)) for recipe in recipes)
        + append
        + ")"
    )


class Progress:
    def __init__(self):
        self.thread = Thread(target=self.progress)
        self.stop = Event()

    def progress(self):
        while not self.stop.wait(60):
            print(".", end="")
            sys.stdout.flush()
        print()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop.set()
        self.thread.join()
