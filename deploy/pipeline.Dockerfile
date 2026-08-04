FROM python:3.12-slim

WORKDIR /app
COPY . /app
COPY deploy/pipeline-entrypoint.sh /usr/local/bin/pardubicko-pipeline-entrypoint
RUN chmod 0755 /usr/local/bin/pardubicko-pipeline-entrypoint

ENV PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Prague
ENTRYPOINT ["pardubicko-pipeline-entrypoint"]
CMD ["python3", "tools/pipeline/pipeline.py", "stats"]
