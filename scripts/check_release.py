"""Validate a release tag against static source and distribution versions."""

import argparse
import ast
import re
import tomllib
from pathlib import Path


def check_release(root: Path, tag: str, ref_type: str) -> str:
    if ref_type != "tag" or not re.fullmatch(r"v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", tag):
        raise ValueError("select a vMAJOR.MINOR.PATCH tag with optional a/b/rc suffix")
    version = tag[1:]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    tree = ast.parse((root / "src/rewind/version.py").read_text())
    source_versions = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    ]
    if metadata["name"] != "jaysoft-rewind":
        raise ValueError("unexpected distribution name")
    if metadata["version"] != version or source_versions != [version]:
        raise ValueError("tag, pyproject.toml, and source version must match exactly")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--ref-type", required=True)
    args = parser.parse_args()
    print(check_release(Path.cwd(), args.tag, args.ref_type))


if __name__ == "__main__":
    main()
