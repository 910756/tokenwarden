FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" "weasyprint==63.1"
COPY report.py .
ENTRYPOINT ["python", "report.py"]
