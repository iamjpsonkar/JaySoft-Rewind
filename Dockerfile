FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY docs/pypi.md ./docs/pypi.md
COPY src ./src
COPY examples ./examples
COPY scripts ./scripts
RUN python -m pip install --no-cache-dir '.[all]'

USER 10001:10001
CMD ["python", "-m", "rewind", "--help"]
