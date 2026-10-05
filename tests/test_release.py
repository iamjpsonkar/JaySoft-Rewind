import tomllib
from pathlib import Path

import pytest

from scripts.check_release import check_release


@pytest.fixture
def release_tree(tmp_path):
    (tmp_path / "src/rewind").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "jaysoft-rewind"\nversion = "0.1.0a2"\n'
    )
    (tmp_path / "src/rewind/version.py").write_text('__version__ = "0.1.0a2"\n')
    return tmp_path


def test_release_identity_requires_exact_tag_and_both_versions(release_tree):
    assert check_release(release_tree, "v0.1.0a2", "tag") == "0.1.0a2"
    for tag, kind in [("v0.1.0a2", "branch"), ("0.1.0a2", "tag"), ("v0.1.0", "tag")]:
        with pytest.raises(ValueError):
            check_release(release_tree, tag, kind)
    (release_tree / "src/rewind/version.py").write_text('__version__ = "0.1.0a1"\n')
    with pytest.raises(ValueError, match="match exactly"):
        check_release(release_tree, "v0.1.0a2", "tag")


def test_release_validation_never_executes_version_module(release_tree):
    marker = release_tree / "executed"
    source = release_tree / "src/rewind/version.py"
    source.write_text(source.read_text() + f"\nopen({str(marker)!r}, 'w').close()\n")
    assert check_release(release_tree, "v0.1.0a2", "tag") == "0.1.0a2"
    assert not marker.exists()


def test_repository_release_metadata_is_consistent():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert check_release(root, f"v{version}", "tag") == version
