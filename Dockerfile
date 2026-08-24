# ─── Dockerfile para el Agente ADK (asistente_correos) ───
# Despliega como microservicio independiente en Google Cloud Run

FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código dentro del directorio asistente_correos para preservar el appName
COPY . ./asistente_correos

# Cloud Run inyecta PORT=8080 automáticamente
ENV PORT=8080

# Iniciar el servidor web del agente ADK especificando la carpeta asistente_correos
CMD python -m google.adk.cli web --host 0.0.0.0 --port $PORT asistente_correos
