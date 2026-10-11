"""Build summaries with mocked rattler execution."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bioconda_utils import build
from bioconda_utils._types import RATTLER, RecipePath


@pytest.mark.parametrize("variant_count", [1, 2])
def test_rattler_logs_one_success_summary(monkeypatch, tmp_path, caplog, variant_count):
    variants = [
        SimpleNamespace(
            run_build=Mock(
                return_value=SimpleNamespace(
                    packages=[str(tmp_path / f"package-{i}.conda")]
                )
            )
        )
        for i in range(variant_count)
    ]
    stage0 = SimpleNamespace(
        from_file=lambda _path: SimpleNamespace(render=lambda *_args: variants)
    )
    monkeypatch.setattr(build, "rb", SimpleNamespace(Stage0Recipe=stage0))
    monkeypatch.setattr(build, "report_resources", lambda *_args: None)
    caplog.set_level(logging.INFO, logger=build.__name__)
    build.build(
        RecipePath(path=tmp_path, build_system=RATTLER),
        global_variants=Mock(),
        tool_config=Mock(),
        render_config=Mock(),
        rattler_output_dir=tmp_path,
        force=False,
        mulled_build_and_test=False,
    )
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("BUILD SUCCESS")
    ]
    assert messages == [
        "BUILD SUCCESS " + " ".join(f"package-{i}.conda" for i in range(variant_count))
    ]
    for variant in variants:
        variant.run_build.assert_called_once()
