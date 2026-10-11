import pytest

from bioconda_utils import bioconductor_skeleton, cran_skeleton
from bioconda_utils._types import Config
from bioconda_utils.conda import repodata
from bioconda_utils.conda.conda_build_bridge import load_first_metadata

config = {"channels": ["conda-forge", "bioconda"]}


def test_write_recipe_normalizes_raw_config_at_boundary(monkeypatch, tmp_path):
    class NormalizationObserved(Exception):
        pass

    captured = {}

    def register_config(config):
        captured["config"] = config

    def observe_config(*_args, **_kwargs):
        assert isinstance(captured["config"], Config)
        assert captured["config"]["requirements"] is None
        raise NormalizationObserved

    monkeypatch.setattr(repodata.RepoData, "register_config", register_config)
    monkeypatch.setattr(bioconductor_skeleton, "BioCProjectPage", observe_config)

    with pytest.raises(NormalizationObserved):
        bioconductor_skeleton.write_recipe(
            "example",
            tmp_path,
            {"channels": []},
        )


def test_cran_write_recipe(tmp_path):
    cran_skeleton.write_recipe("locfit", recipe_dir=tmp_path, recursive=False)
    assert (tmp_path / "r-locfit" / "meta.yaml").exists()
    assert (tmp_path / "r-locfit" / "build.sh").exists()
    assert (tmp_path / "r-locfit" / "bld.bat").exists()


def test_cran_write_recipe_no_windows(tmp_path):
    cran_skeleton.write_recipe(
        "locfit", recipe_dir=tmp_path, recursive=False, no_windows=True
    )
    assert (tmp_path / "r-locfit" / "meta.yaml").exists()
    assert (tmp_path / "r-locfit" / "build.sh").exists()
    assert not (tmp_path / "r-locfit" / "bld.bat").exists()
    for line in (tmp_path / "r-locfit" / "meta.yaml").read_text().splitlines():
        if "skip: True" in line:
            assert "[win]" in line


@pytest.fixture(scope="module")
def bioc_fetch():
    release = bioconductor_skeleton.latest_bioconductor_release_version()
    return bioconductor_skeleton.fetchPackages(release)


@pytest.mark.skip(reason="Does not work since new bioconductor release?")
def test_bioc_write_recipe_skip_in_condaforge(tmp_path, bioc_fetch):
    bioconductor_skeleton.write_recipe(
        "edgeR",
        recipe_dir=tmp_path,
        config=config,
        recursive=True,
        packages=bioc_fetch,
        skip_if_in_channels=["conda-forge"],
    )

    for pkg in [
        "bioconductor-edger",
        "bioconductor-limma",
    ]:
        assert (tmp_path / pkg).exists()

    for pkg in ["r-cpp", "r-lattice", "r-locfit"]:
        assert not (tmp_path / pkg).exists()


@pytest.mark.skip(reason="Does not work since new bioconductor release?")
def test_bioc_write_recipe_no_skipping(tmp_path, bioc_fetch):
    bioconductor_skeleton.write_recipe(
        "edgeR",
        recipe_dir=tmp_path,
        config=config,
        recursive=True,
        packages=bioc_fetch,
        skip_if_in_channels=None,
    )

    for pkg in [
        "bioconductor-edger",
        "bioconductor-limma",
        "r-rcpp",
        # sometime locfit and lattice don't build correctly, but we should
        # eventually ensure they are here as well.
        # 'r-locfit',
        # 'r-lattice',
    ]:
        assert (tmp_path / pkg).exists()


@pytest.mark.skip(reason="Does not work since new bioconductor release?")
def test_meta_contents(tmp_path, bioc_fetch):
    config = {"channels": ["conda-forge", "bioconda"]}
    bioconductor_skeleton.write_recipe(
        "edgeR",
        recipe_dir=tmp_path,
        config=config,
        recursive=False,
        packages=bioc_fetch,
    )

    edger_meta = load_first_metadata(tmp_path / "bioconductor-edger").meta
    assert "r-rcpp" in edger_meta["requirements"]["run"]

    # The rendered meta has {{ compiler('c') }} filled in, so we need to check
    # for one of those filled-in values.
    names = [i.split()[0] for i in edger_meta["requirements"]["build"]]
    assert "libstdcxx-ng" in names or "clang_osx-64" in names

    # bioconductor, bioarchive, and cargoport
    assert len(edger_meta["source"]["url"]) == 3


@pytest.mark.skip(
    reason="Does not currently work inside of the CI (cannot find the release) although it seems to work fine locally."
)
def test_find_best_bioc_version():
    assert bioconductor_skeleton.find_best_bioc_version("DESeq2", "1.40.1") == "3.17"

    # Non-existent version:
    with pytest.raises(bioconductor_skeleton.PackageNotFoundError):
        bioconductor_skeleton.find_best_bioc_version("DESeq2", "5000")

    # Version existed at some point in the past, but only exists now on
    # bioaRchive:
    with pytest.raises(bioconductor_skeleton.PackageNotFoundError):
        bioconductor_skeleton.BioCProjectPage("BioBase", pkg_version="2.37.2")


def test_pkg_version():
    # version specified, but not bioc version
    b = bioconductor_skeleton.BioCProjectPage("DESeq2", pkg_version="1.14.1")
    assert b.version == "1.14.1"
    assert b.bioc_version == "3.4"
    assert b.bioconductor_tarball_url == (
        "https://bioconductor.org/packages/3.4/bioc/src/contrib/DESeq2_1.14.1.tar.gz"
    )
    assert b.bioarchive_url is None
    assert b.cargoport_url == (
        "https://depot.galaxyproject.org/software/bioconductor-deseq2/bioconductor-deseq2_1.14.1_src_all.tar.gz"
    )

    # bioc version specified, but not package version
    b = bioconductor_skeleton.BioCProjectPage("edgeR", bioc_version="3.5")
    assert b.version == "3.18.1"
    assert b.bioc_version == "3.5"
    assert b.bioconductor_tarball_url == (
        "https://bioconductor.org/packages/3.5/bioc/src/contrib/edgeR_3.18.1.tar.gz"
    )
    assert b.bioarchive_url is None
    assert b.cargoport_url == (
        "https://depot.galaxyproject.org/software/bioconductor-edger/bioconductor-edger_3.18.1_src_all.tar.gz"
    )


def test_bioarchive_exists_but_not_bioconductor():
    """
    BioCProjectPage init tries to find the package on the bioconductor site.
    Sometimes bioaRchive has cached the tarball but it no longer exists on the
    bioconductor site. In those cases, we're raising a PackageNotFoundError.

    It's possible to build a recipe based on a package only found in
    bioarchive, but I'm not sure we want to support that in an automated
    fashion. In those cases it would be best to build the recipe manually.
    """
    with pytest.raises(bioconductor_skeleton.PackageNotFoundError):
        bioconductor_skeleton.BioCProjectPage("BioBase", pkg_version="2.37.2")


def test_bioarchive_exists():
    # package found on both bioconductor and bioarchive.
    b = bioconductor_skeleton.BioCProjectPage("DESeq", pkg_version="1.26.0")
    assert (
        b.bioarchive_url == "https://bioarchive.galaxyproject.org/DESeq_1.26.0.tar.gz"
    )


def test_annotation_data(tmp_path, bioc_fetch):
    bioconductor_skeleton.write_recipe(
        "AHCytoBands", tmp_path, config, recursive=False, packages=bioc_fetch
    )
    recipe_dir = tmp_path / "bioconductor-ahcytobands"
    meta = load_first_metadata(recipe_dir, finalize=False).meta
    assert "curl" in {dep.split()[0] for dep in meta["requirements"]["run"]}
    assert len(meta["source"]["url"]) == 4
    assert not (recipe_dir / "build.sh").exists()
    assert (recipe_dir / "post-link.sh").exists()
    assert (recipe_dir / "pre-unlink.sh").exists()


def test_experiment_data(tmp_path, bioc_fetch):
    bioconductor_skeleton.write_recipe(
        "Affyhgu133A2Expr",
        tmp_path,
        config,
        recursive=False,
        packages=bioc_fetch,
    )
    recipe_dir = tmp_path / "bioconductor-affyhgu133a2expr"
    meta = load_first_metadata(recipe_dir, finalize=False).meta
    assert "curl" in {dep.split()[0] for dep in meta["requirements"]["run"]}
    assert len(meta["source"]["url"]) == 4
    assert not (recipe_dir / "build.sh").exists()
    assert (recipe_dir / "post-link.sh").exists()
    assert (recipe_dir / "pre-unlink.sh").exists()


def test_nonexistent_pkg(tmp_path, bioc_fetch):
    # no such package exists in the current bioconductor
    with pytest.raises(bioconductor_skeleton.PackageNotFoundError):
        bioconductor_skeleton.write_recipe(
            "nonexistent",
            tmp_path,
            config,
            recursive=True,
            packages=bioc_fetch,
        )

    # package exists, but not this version
    with pytest.raises(bioconductor_skeleton.PackageNotFoundError):
        bioconductor_skeleton.write_recipe(
            "DESeq",
            tmp_path,
            config,
            recursive=True,
            pkg_version="5000",
            packages=bioc_fetch,
        )


@pytest.mark.skip(reason="Does not work since new bioconductor release?")
def test_overwrite(tmp_path, bioc_fetch):
    bioconductor_skeleton.write_recipe(
        "edgeR",
        recipe_dir=tmp_path,
        config=config,
        recursive=False,
        packages=bioc_fetch,
    )

    # Same thing with force=False returns ValueError
    with pytest.raises(ValueError):
        bioconductor_skeleton.write_recipe(
            "edgeR",
            recipe_dir=tmp_path,
            config=config,
            recursive=False,
            packages=bioc_fetch,
        )

    # But same thing with force=True is OK
    bioconductor_skeleton.write_recipe(
        "edgeR",
        recipe_dir=tmp_path,
        config=config,
        recursive=False,
        force=True,
        packages=bioc_fetch,
    )


def test_fetch_packages_invalid_version():
    with pytest.raises(RuntimeError) as excinfo:
        bioconductor_skeleton.fetchPackages("99.99")
    assert "Could not fetch any Bioconductor package metadata files" in str(
        excinfo.value
    )
