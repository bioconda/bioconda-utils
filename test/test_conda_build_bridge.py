from unittest.mock import Mock

import jinja2
import pytest

from bioconda_utils.conda import conda_build_bridge as bridge


@pytest.mark.parametrize("convert", [int, float, complex])
def test_undefined_numeric_conversion_reports_missing_value(convert):
    with pytest.raises(jinja2.UndefinedError, match="missing.*undefined"):
        convert(bridge.JinjaSilentUndefined(name="missing"))


def test_missing_template_text_still_renders_empty():
    template = bridge.jinja_silent_undef.from_string(
        "{{ missing }}|{{ missing + 'suffix' }}|{{ missing('argument') }}"
    )
    assert template.render() == "||"


def test_fast_metadata_render_reports_undefined_numeric_value(tmp_path):
    (tmp_path / "meta.yaml").write_text("package:\n  version: {{ missing|int }}\n")
    with pytest.raises(ValueError, match="Problem inspecting") as exc:
        bridge.load_meta_fast(tmp_path)
    assert isinstance(exc.value.__cause__, jinja2.UndefinedError)


def test_load_first_metadata_rejects_empty_render(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.api, "render", lambda *_args, **_kwargs: [])
    config = Mock()
    assert bridge.load_all_meta(tmp_path, config=config, finalize=False) == []
    with pytest.raises(ValueError, match=f"No metadata rendered for recipe {tmp_path}"):
        bridge.load_first_metadata(tmp_path, config=config, finalize=False)


def test_load_first_metadata_returns_first_variant(monkeypatch, tmp_path):
    first, second = Mock(), Mock()
    monkeypatch.setattr(
        bridge.api,
        "render",
        lambda *_args, **_kwargs: [(first, False, False), (second, False, False)],
    )
    assert bridge.load_first_metadata(tmp_path, config=Mock(), finalize=False) is first
