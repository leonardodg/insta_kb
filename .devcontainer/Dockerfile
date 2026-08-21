FROM python:3.14-slim-bookworm AS development

ENV PYTHONUNBUFFERED=1 \
  UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_PYTHON_PREFERENCE=only-managed \
  UV_NO_DEV=1

# Expose the venv to non-root user and make it the default python environment
ENV VIRTUAL_ENV=/app/.venv
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
