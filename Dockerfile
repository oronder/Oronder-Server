FROM python:3.13-slim
WORKDIR /app

# Install system dependencies and curl (for uv installer)
RUN apt-get update && \
    apt-get -y install git make libpq-dev gcc g++ python3-dev libffi-dev libjpeg-dev zlib1g-dev curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv (Python package manager) and project dependencies using pyproject.toml
ENV UV_INSTALL_DIR=/root/.local/bin \
    PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy project metadata first for better layer caching
COPY pyproject.toml uv.lock /app/

# Sync dependencies into a local venv (no dev deps), exactly as locked. --frozen
# fails the build if uv.lock is out of date with pyproject.toml: run `uv lock`.
RUN uv sync --frozen --no-dev

# Ensure the venv is used by default
ENV VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH

# Copy source code last since it changes most frequently
COPY src /app/
