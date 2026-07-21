FROM python:3.13-slim

RUN pip install --no-cache-dir sqlite-web

WORKDIR /data
EXPOSE 8080

ENTRYPOINT ["sqlite_web", "-H", "0.0.0.0"]
