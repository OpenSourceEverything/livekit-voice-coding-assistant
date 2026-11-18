# Minimal Linux image with Python 3.12 installed.
FROM python:3.12-slim-bookworm

# Copy uv (Python dependency manager) from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

# Configure Python, uv, and disable problematic native AI preloading.
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE=0

# Install Linux utilities needed by our application.
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container.
WORKDIR /app

# Copy dependency definitions before source to improve Docker build caching.
COPY pyproject.toml uv.lock ./
COPY LICENSE ./LICENSE
COPY src/ src/

# Install exact locked dependencies, excluding development packages.
RUN uv sync --locked --no-dev

# Include examples and create a synthetic repository for demonstration.
COPY examples/ examples/
COPY tools/init_demo_repo.py tools/init_demo_repo.py
RUN uv run --no-sync python tools/init_demo_repo.py \
    && useradd --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app

# Run as an unprivileged Linux user instead of root.
USER appuser

# Restrict cloud demonstration to synthetic source and disable risky tools.
ENV COPILOT_MODE=cloud-demo REPO_ROOT=/app/var/demo-repo \
    ENABLE_LOCAL_TESTS=false ENABLE_PROPOSALS=false ENABLE_CODEX=false

# Command executed when the container starts.
CMD ["uv", "run", "--no-sync", "src/agent.py", "start"]