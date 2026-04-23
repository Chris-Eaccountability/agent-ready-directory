FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create data directory for SQLite volume
RUN mkdir -p /data

# Expose port
EXPOSE 8080

# Start server
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080"]
