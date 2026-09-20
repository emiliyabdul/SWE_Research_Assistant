# Async Research Assistant — build once, run the CLI or the tests (Topic 4).
#
# Build:  docker build -t researcher .
# Run:    docker run --rm researcher                                   # offline demo, no keys
#         docker run --rm --env-file .env researcher \
#             python -m researcher ask "your question"                # live mode
# Tests:  docker run --rm researcher python -m pytest tests -q

# ---- Stage 1: builder — install deps into a venv ---------------------------
FROM python:3.14-alpine AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml .
COPY ai/ ai/
COPY src/ src/
RUN pip install --no-deps .

# ---- Stage 2: runtime — venv + source, nothing else -------------------------
FROM python:3.14-alpine

LABEL org.opencontainers.image.title="Async Research Assistant (AI-ENG-110 Final Project)"
LABEL org.opencontainers.image.source="https://github.com/emiliyabdul/SWE_Research_Assistant"
LABEL org.opencontainers.image.version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

COPY ai/ ai/
COPY src/ src/
COPY data/ data/
COPY tests/ tests/
COPY scripts/ scripts/
COPY pyproject.toml .

RUN adduser -D appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "researcher", "ask", "--offline", \
     "What is photosynthesis and what are its main stages?"]
