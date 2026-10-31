FROM python:3.13-alpine
WORKDIR /app
COPY server.py .
USER nobody
EXPOSE 8090
HEALTHCHECK --interval=5s --timeout=2s --retries=10 CMD wget -qO- http://127.0.0.1:8090/health >/dev/null || exit 1
CMD ["python", "-u", "server.py"]
