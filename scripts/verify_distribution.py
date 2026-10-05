"""Check built distribution contents and import the wheel outside the source tree."""

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path


def verify(directory: Path) -> dict[str, str]:
    wheels = sorted(directory.glob("jaysoft_rewind-*.whl"))
    archives = sorted(directory.glob("jaysoft_rewind-*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise ValueError("use a clean output directory containing one wheel and one source archive")
    wheel, source = wheels[0].resolve(), archives[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        required = {"rewind/py.typed", "rewind/persistence.py", "rewind/sources.py"}
        if not required <= names:
            raise ValueError("wheel is missing required library files")
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("wheel must contain one metadata document")
        metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode())
        if metadata["Name"] != "jaysoft-rewind":
            raise ValueError("unexpected distribution name")
        if metadata["Author-email"] != "Jay Prakash Sonkar <iamjpsonkar@gmail.com>":
            raise ValueError("unexpected distribution author")
        if any(name.startswith(("tests/", "examples/", "docs/")) for name in names):
            raise ValueError("wheel contains development-only files")
        version = metadata["Version"]
    with tarfile.open(source) as archive:
        names = {name.partition("/")[2] for name in archive.getnames()}
        required = {
            "README.md", "pyproject.toml", "tests/__init__.py", "tests/runner_targets.py",
            "examples/background_capture.py", "examples/sources_failure.py",
            "docs/background-persistence.md", "docs/operations.md", "docs/performance.md",
            "scripts/benchmark_capture.py", "scripts/verify_offline.sh",
        }
        if not required <= names:
            raise ValueError("source archive is missing documentation, helpers, or examples")
        if any(name.endswith(".rewind.json") or "/.env" in f"/{name}" for name in names):
            raise ValueError("source archive contains local recordings or environment files")
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
import rewind
assert hasattr(rewind, "BackgroundWriter")
assert hasattr(rewind.Rewind, "aclose")
assert rewind.__version__ == sys.argv[2]
assert str(rewind.__file__).startswith(sys.argv[1])
print(json.dumps({"version": rewind.__version__}))
"""
    with tempfile.TemporaryDirectory(prefix="rewind-wheel-check-") as directory_name:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(wheel), version],
            cwd=directory_name, capture_output=True, text=True, timeout=30, check=True,
        )
    return {"wheel": wheel.name, "source": source.name, **json.loads(result.stdout)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="clean build output directory")
    print(json.dumps(verify(parser.parse_args().directory)))


if __name__ == "__main__":
    main()
