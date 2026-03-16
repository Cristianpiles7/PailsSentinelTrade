# PST API Service
# Usar Docker es la mejor forma de asegurar que el sistema funcione igual en cualquier PC.

FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema para MT5 (si aplica en Linux) o para la API
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_vNext.txt .
RUN pip install --no-cache-dir -r requirements_vNext.txt

COPY . .

# Variables de entorno por defecto
ENV DB_PATH=PST_Core/data/pst_trading.db
ENV API_HOST=0.0.0.0
ENV API_PORT=8000

EXPOSE 8000

CMD ["uvicorn", "PST_API.main:app", "--host", "0.0.0.0", "--port", "8000"]
