FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        php-cli \
        php-mbstring \
        php-sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY tools/validate/requirements.txt /tmp/pardubicko-requirements.txt
RUN pip install --no-cache-dir -r /tmp/pardubicko-requirements.txt

WORKDIR /app
CMD ["python3", "tools/run_tests.py"]
