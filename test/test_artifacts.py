import zipfile
from pathlib import Path

from bioconda_utils import artifacts


class _TotalList(list):
    @property
    def totalCount(self):
        return len(self)


class _Artifact:
    def __init__(self, name, url):
        self.name = name
        self.archive_download_url = url


class _Run:
    def __init__(self, artifacts):
        self._artifacts = artifacts

    def get_artifacts(self):
        return self._artifacts


class _CheckRun:
    details_url = (
        "https://github.com/bioconda/bioconda-recipes/actions/runs/123/jobs/456"
    )


class _FakeClient:
    def get_repo(self, _repo_name):
        return self

    def get_commit(self, _git_sha):
        return self

    def get_pulls(self):
        return _TotalList([self])


def _write_artifact_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as artifact_zip:
        for member_path, data in members.items():
            artifact_zip.writestr(member_path, data)


def test_get_gha_artifacts_filters_to_package_platform():
    repo = type(
        "Repo",
        (),
        {
            "get_workflow_run": lambda self, _run_id: _Run(
                [
                    _Artifact("linux-packages", "linux-url"),
                    _Artifact("linux-arm64-packages", "linux-arm64-url"),
                    _Artifact("osx-packages", "osx-url"),
                ]
            )
        },
    )()

    urls = list(artifacts.get_gha_artifacts(_CheckRun(), "linux-aarch64", repo))

    assert urls == ["linux-arm64-url"]


def test_get_gha_artifacts_accepts_legacy_x86_names():
    repo = type(
        "Repo",
        (),
        {
            "get_workflow_run": lambda self, _run_id: _Run(
                [
                    _Artifact("linux-packages", "linux-url"),
                    _Artifact("linux-64-packages", "linux-64-url"),
                    _Artifact("linux-aarch64-packages", "linux-aarch64-url"),
                ]
            )
        },
    )()

    urls = list(artifacts.get_gha_artifacts(_CheckRun(), "linux-64", repo))

    assert urls == ["linux-url", "linux-64-url"]


def test_upload_pr_artifacts_filters_packages_and_arm64_images(monkeypatch, tmp_path):
    archive = tmp_path / "artifact.zip"
    _write_artifact_zip(
        archive,
        {
            "packages/linux-aarch64/samtools-1.0-0.conda": b"arm package",
            "packages/noarch/helper-1.0-0.conda": b"noarch package",
            "packages/linux-64/samtools-1.0-0.conda": b"x86 package",
            "images/samtools---1.0--0.tar.gz": b"x86 image",
            "images/samtools---1.0--0-arm64.tar.gz": b"arm image",
        },
    )
    uploaded_packages = []
    uploaded_images = []

    monkeypatch.setattr(artifacts.utils, "load_config", lambda _config: {})
    monkeypatch.setattr(artifacts.utils, "get_github_client", _FakeClient)
    monkeypatch.setattr(
        artifacts,
        "fetch_artifacts",
        lambda *_args, **_kwargs: ["https://example.test/artifact.zip"],
    )
    monkeypatch.setenv("QUAY_LOGIN", "login:password")

    def download_artifact(_url, to_path, _artifact_source):
        Path(to_path).write_bytes(archive.read_bytes())

    monkeypatch.setattr(artifacts, "download_artifact", download_artifact)
    monkeypatch.setattr(
        artifacts,
        "anaconda_upload",
        lambda pkg, label=None: uploaded_packages.append((pkg, label)) or True,
    )
    monkeypatch.setattr(
        artifacts.utils,
        "run",
        lambda cmd, **_kwargs: type("R", (), {"stdout": "sha256:" + "a" * 64})(),
    )
    monkeypatch.setattr(
        artifacts,
        "skopeo_upload",
        lambda img, target, creds=None: (
            uploaded_images.append((img, target, creds)) or True
        ),
    )

    result = artifacts.upload_pr_artifacts(
        str(tmp_path / "config.yaml"),
        "bioconda/bioconda-recipes",
        "abc123",
        mulled_upload_target="biocontainers",
        artifact_source="github-actions",
        package_platform="linux-aarch64",
        container_platforms=["linux/arm64"],
    )

    assert result == artifacts.UploadResult.SUCCESS
    assert [Path(pkg).name for pkg, _label in uploaded_packages] == [
        "samtools-1.0-0.conda",
        "helper-1.0-0.conda",
    ]
    assert uploaded_images == [
        (
            uploaded_images[0][0],
            "biocontainers/samtools:1.0--0-arm64",
            "login:password",
        )
    ]


def test_upload_pr_artifacts_returns_no_artifacts_when_nothing_matches(
    monkeypatch, tmp_path
):
    archive = tmp_path / "artifact.zip"
    _write_artifact_zip(
        archive,
        {"packages/linux-64/samtools-1.0-0.conda": b"x86 package"},
    )

    monkeypatch.setattr(artifacts.utils, "load_config", lambda _config: {})
    monkeypatch.setattr(artifacts.utils, "get_github_client", _FakeClient)
    monkeypatch.setattr(
        artifacts,
        "fetch_artifacts",
        lambda *_args, **_kwargs: ["https://example.test/artifact.zip"],
    )

    def download_artifact(_url, to_path, _artifact_source):
        Path(to_path).write_bytes(archive.read_bytes())

    monkeypatch.setattr(artifacts, "download_artifact", download_artifact)

    result = artifacts.upload_pr_artifacts(
        str(tmp_path / "config.yaml"),
        "bioconda/bioconda-recipes",
        "abc123",
        artifact_source="github-actions",
        package_platform="linux-aarch64",
    )

    assert result == artifacts.UploadResult.NO_ARTIFACTS


def test_upload_pr_artifacts_dryrun_counts_matching_artifacts(monkeypatch, tmp_path):
    archive = tmp_path / "artifact.zip"
    _write_artifact_zip(
        archive,
        {"packages/linux-aarch64/samtools-1.0-0.conda": b"arm package"},
    )

    monkeypatch.setattr(artifacts.utils, "load_config", lambda _config: {})
    monkeypatch.setattr(artifacts.utils, "get_github_client", _FakeClient)
    monkeypatch.setattr(
        artifacts,
        "fetch_artifacts",
        lambda *_args, **_kwargs: ["https://example.test/artifact.zip"],
    )

    def download_artifact(_url, to_path, _artifact_source):
        Path(to_path).write_bytes(archive.read_bytes())

    monkeypatch.setattr(artifacts, "download_artifact", download_artifact)

    result = artifacts.upload_pr_artifacts(
        str(tmp_path / "config.yaml"),
        "bioconda/bioconda-recipes",
        "abc123",
        dryrun=True,
        artifact_source="github-actions",
        package_platform="linux-aarch64",
    )

    assert result == artifacts.UploadResult.SUCCESS
