FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY pilltalk ./pilltalk
RUN pip install --no-cache-dir .
CMD ["uvicorn", "pilltalk.api:app", "--host", "0.0.0.0", "--port", "8000"]
