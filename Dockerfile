FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for weasyprint (PDF generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory
RUN mkdir -p .data

EXPOSE 8080

# Start the FastAPI dashboard
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8080"]
