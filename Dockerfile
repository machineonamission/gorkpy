# Using slim to save space
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Optimize uv and Python settings
ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1
# Change the link mode to copy to silence warnings about cross-device links with cache mounts
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# 1. Install dependencies ONLY
# This leverages the Docker cache. It only rebuilds if pyproject.toml or uv.lock change.
# --no-editable installs it as a standard package rather than symlinking
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

# 2. Copy source code and install the project
# This layer rebuilds whenever your code changes, but skips re-downloading dependencies
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# Place the virtual environment at the front of the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Execute the application directly!
# Bypassing `uv run` completely skips the runtime check that was causing bytecode compilation on launch.
ENTRYPOINT ["python", "main.py"]
