FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies and code (TA-Lib ships prebuilt Linux wheels, so no compiler is needed)
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Run as an unprivileged user
RUN useradd --create-home --uid 1000 bot
USER bot

# Secrets come from the environment (docker compose env_file), never from the image
CMD ["python", "-m", "jevtrade.run", "--loop"]
