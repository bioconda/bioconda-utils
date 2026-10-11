import os
import shutil

import pytest

from bioconda_utils.containers import oci


def test_skopeo_bin_resolves_packaged_executable(monkeypatch, tmp_path):
    env_root = tmp_path / "env"
    packaged = env_root / "bin" / "skopeo"
    packaged.parent.mkdir(parents=True)
    packaged.touch()
    monkeypatch.setattr(oci, "env_root", lambda: env_root)

    assert oci.skopeo_bin() == str(packaged)


def test_skopeo_bin_falls_back_to_path(monkeypatch, tmp_path):
    on_path = tmp_path / "skopeo"
    on_path.touch()
    monkeypatch.setattr(oci, "env_root", lambda: tmp_path / "missing")
    monkeypatch.setattr(shutil, "which", lambda _: str(on_path))

    assert oci.skopeo_bin() == str(on_path)


def test_skopeo_bin_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(oci, "env_root", lambda: tmp_path / "missing")
    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(FileNotFoundError, match="skopeo"):
        oci.skopeo_bin()


def test_skopeo_env_sets_ssl_dir_from_env_root(monkeypatch, tmp_path):
    env_root = tmp_path / "env"
    (env_root / "ssl").mkdir(parents=True)
    monkeypatch.setattr(oci, "env_root", lambda: env_root)

    env = oci.skopeo_env()

    assert env["SSL_CERT_DIR"] == str(env_root / "ssl")


def test_skopeo_env_leaves_ssl_dir_unset_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(oci, "env_root", lambda: tmp_path / "missing")

    assert "SSL_CERT_DIR" not in oci.skopeo_env()


def test_skopeo_env_keeps_surrounding_environment():
    env = oci.skopeo_env()

    assert env.items() >= os.environ.items()
