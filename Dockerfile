FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE=0
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY LICENSE ./LICENSE
COPY src/ src/
RUN uv sync --locked --no-dev
COPY examples/ examples/
COPY tools/init_demo_repo.py tools/init_demo_repo.py
RUN uv run --no-sync python tools/init_demo_repo.py \
    && useradd --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser
ENV COPILOT_MODE=cloud-demo REPO_ROOT=/app/var/demo-repo \
    ENABLE_LOCAL_TESTS=false ENABLE_PROPOSALS=false ENABLE_CODEX=false
CMD ["uv", "run", "--no-sync", "src/agent.py", "start"]
