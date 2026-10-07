"""Logging setup and subprocess output routing."""

import logging
import sys
from io import StringIO

import pytest
from rich.console import Console

from bioconda_utils.support import logsetup
from bioconda_utils.support.subproc import run


@pytest.mark.parametrize("custom_logger", [False, True])
def test_command_output_is_logged_in_full(monkeypatch, tmp_path, custom_logger):
    output = StringIO()
    monkeypatch.setattr(logsetup, "err_console", Console(file=output, width=120))
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    logfile = tmp_path / "commands.log"
    try:
        logsetup.setup_logger(logfile=logfile)
        kwargs = (
            {"mylogger": logging.getLogger("custom-command")} if custom_logger else {}
        )
        for _ in range(2):
            result = run(
                [sys.executable, "-c", "for i in range(5): print(f'line-{i}')"],
                live=True,
                **kwargs,
            )
            assert "line-4" in result.stdout
        rendered = output.getvalue()
        assert rendered.count("(COMMAND)") == 2
        for i in range(5):
            assert rendered.count(f"(OUT) line-{i}") == 2
            assert logfile.read_text().count(f"(OUT) line-{i}") == 2
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)


def test_setup_replaces_owned_handlers_and_preserves_external_handlers(
    monkeypatch, tmp_path
):
    output, external_output = StringIO(), StringIO()
    monkeypatch.setattr(logsetup, "err_console", Console(file=output, width=120))
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    external = logging.StreamHandler(external_output)
    root.addHandler(external)
    try:
        logfile = tmp_path / "first.log"
        logsetup.setup_logger(logfile=logfile)
        old_file_handler = next(
            h for h in root.handlers if isinstance(h, logsetup._FileHandler)
        )
        logsetup.setup_logger()
        assert external in root.handlers
        assert old_file_handler not in root.handlers
        assert old_file_handler.stream is None
        root.info("original %s", "output")
        assert output.getvalue().count("original output") == 1
        assert external_output.getvalue() == "original output\n"
        assert logfile.read_text() == ""
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
