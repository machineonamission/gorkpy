FROM ghcr.io/astral-sh/uv:python3.14-bookworm

RUN apt-get update && apt-get install -y git

COPY . /app
WORKDIR /app

CMD git pull origin master && uv run main.py
