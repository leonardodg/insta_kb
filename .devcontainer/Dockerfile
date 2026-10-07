FROM python:3.14-slim-bookworm AS development

# only-system (not only-managed): the base image already ships Python 3.14,
# so there's no need for uv to install its own managed copy under /root --
# that copy was unreadable to appuser after the USER switch below, which
# silently broke `python`/`uv run` for the non-root user (found 2026-10-07).
ENV PYTHONUNBUFFERED=1 \
  UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_PYTHON_PREFERENCE=only-system

# venv lives entirely INSIDE the image, never on the host-bind-mounted /app.
# It used to be /app/.venv (a symlink into the host's ~/.cache/uv), which
# broke two independent ways (found 2026-10-07, see docs/HANDOFF.md):
#   1. `uv run` inside the container "fixed" the then-dangling symlink by
#      repointing it at a container-only cache path, silently breaking the
#      host's own venv on the next host-side `uv` command.
#   2. Even with the symlink target made reachable (tried: bind-mounting the
#      host's ~/.cache/uv at an identical path), the venv's pyvenv.cfg pins
#      an absolute interpreter path from the HOST's system Python -- it
#      never resolves inside any container, and the venv also carries
#      compiled extensions (psycopg, av/PyAV, ctranslate2, pydantic-core...)
#      built against the host's libs, not portable to a different base image
#      regardless. A venv genuinely shared between host and container isn't
#      safe to build this way -- isolate it instead.
ENV VIRTUAL_ENV=/opt/venv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
# Ensure system directories are present on PATH so core utilities (sleep, sh, etc.) are available.
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Install OS packages we need for building and runtime
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    git \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


WORKDIR /app

RUN pip install uv
COPY pyproject.toml uv.lock* ./

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv lock && \
    uv sync --no-install-project --no-dev

# Create non-root user and ensure ownership of persistent folders. Also make the venv accessible.
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /commandhistory \
  && echo 'export HISTFILE=/tmp/appuser.bash_history' >> /home/appuser/.bashrc \
    && chown -R appuser:appuser ${VIRTUAL_ENV} /commandhistory /app || true


# Switch to non-root user for interactive sessions
USER appuser
