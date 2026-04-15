FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema para mysqlclient
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    pydantic \
    httpx \
    sqlalchemy \
    pymysql \
    cryptography

# Copiar código
COPY ex_app/lib/main.py .
COPY ex_app/lib/database.py .

EXPOSE 8080

CMD ["python", "main.py"]