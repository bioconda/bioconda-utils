"""Console subprocess output limits."""

import logging
import sys
from io import StringIO

import pytest
from rich.console import Console

from bioconda_utils.support import logsetup
from bioconda_utils.support.subproc import run


@pytest.mark.parametrize("custom_logger", [False, True])
@pytest.mark.parametrize("limit", [0, 2])
def test_command_output_limit(monkeypatch, tmp_path, custom_logger, limit):
    output = StringIO()
    monkeypatch.setattr(logsetup, "err_console", Console(file=output, width=120))
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    logfile = tmp_path / "commands.log"
    try:
        logsetup.setup_logger(logfile=logfile, log_command_max_lines=limit)
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
        assert rendered.count("Command output truncated") == 2
        assert rendered.count("(COMMAND)") == 2
        for i in range(5):
            assert rendered.count(f"(OUT) line-{i}") == (2 if i < limit else 0)
            assert logfile.read_text().count(f"(OUT) line-{i}") == 2
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
