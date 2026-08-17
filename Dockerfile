# ─── Dockerfile para el Agente ADK (asistente_correos) ───
# Despliega como microservicio independiente en Google Cloud Run

FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del agente
COPY . .

# Puerto que expone el servidor web de ADK
EXPOSE 8000

# Variables de entorno requeridas (se configuran en Cloud Run):
# - GOOGLE_CLOUD_PROJECT: tu proyecto de GCP
# - GOOGLE_CLOUD_LOCATION: región (ej. us-central1)
# - BACKEND_URL: URL de tu servidor Node.js en Cloud Run
#   (ej. https://customer-information-720693669884.us-central1.run.app)

# Iniciar el servidor web del agente ADK
CMD ["python", "-m", "google.adk.cli", "web", "--host", "0.0.0.0", "--port", "8000", "."]
