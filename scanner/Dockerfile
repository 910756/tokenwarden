FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"
COPY scan.py .
RUN useradd -r -u 10001 tw
USER tw
ENTRYPOINT ["python", "scan.py"]
