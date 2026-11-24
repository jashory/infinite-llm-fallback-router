FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY llm_fallback_router.py pyproject.toml README.md LICENSE ./
ENTRYPOINT ["python3", "-u", "/app/llm_fallback_router.py", "--mcp"]
