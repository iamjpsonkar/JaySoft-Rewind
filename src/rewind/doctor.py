"""Data-only runtime inventory; never imports optional frameworks or user code."""

import platform
import shutil
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .version import __version__


def diagnose() -> dict[str, Any]:
    dependencies: dict[str, str | None] = {}
    for name in (
        "httpx",
        "fastapi",
        "starlette",
        "flask",
        "psycopg",
        "PyMySQL",
        "kafka-python",
        "celery",
        "boto3",
        "botocore",
        "sqlalchemy",
        "redis",
    ):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    return {
        "rewind": __version__,
        "python": platform.python_version(),
        "dependencies": dependencies,
        "docker_available": shutil.which("docker") is not None,
        "isolation": "python-guard is not an OS sandbox; use network-disabled containers",
        "scope": "explicit configured adapters; installed packages do not imply interception",
    }
