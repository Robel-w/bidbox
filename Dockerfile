FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY . .

CMD ["python", "scripts/run_demo.py"]
