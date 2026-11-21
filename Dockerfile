FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY csrc ./csrc
COPY src ./src

RUN uv sync --frozen --no-dev

EXPOSE 8200
CMD ["uv", "run", "verispec", "serve", "--host", "0.0.0.0", "--port", "8200"]
