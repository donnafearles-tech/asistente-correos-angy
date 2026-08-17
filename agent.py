import os
import requests
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

from google.adk.agents.llm_agent import Agent

# Lista negra de teléfonos de empresa corporativos
COMPANY_PHONES = {"8882494189", "18882494189"}

def buscar_y_sanitizar_cliente(invoice: str) -> dict:
    """
    Busca los datos de un cliente en la base de datos por su número de invoice,
    limpia y sanitiza la información distinguiendo datos de empresa de datos de cliente,
    y bloquea estrictamente los teléfonos y correos corporativos (ej. 8882494189).
    """
    url = f"http://localhost:3000/api/sheets/record-by-invoice?invoice={invoice}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return {"error": f"No se pudo consultar el backend. Código: {response.status_code}"}
        
        data = response.json()
        if not data.get("success") or not data.get("record"):
            return {"error": f"No se encontró registro para la factura {invoice}"}
        
        record = data["record"]
        
        # Sanitizar teléfono
        raw_phone = str(record.get("phone") or "").strip()
        clean_phone = "".join(filter(str.isdigit, raw_phone))
        if len(clean_phone) > 10:
            clean_phone = clean_phone[-10:]
            
        # Bloquear teléfono corporativo de la empresa
        if clean_phone in COMPANY_PHONES:
            phone_status = "BLOQUEADO (Es el número corporativo de la empresa)"
            display_phone = ""
        elif len(clean_phone) < 10:
            phone_status = "Inválido (menos de 10 dígitos)"
            display_phone = ""
        else:
            phone_status = "Válido"
            display_phone = clean_phone
            
        # Sanitizar Email (Bloquear correos corporativos de soporte)
        raw_email = str(record.get("email") or "").strip().lower()
        is_corporate_email = any(word in raw_email for word in [
            "customerservice", "noreply", "info@", "support@", "hello@", "contact@", "admin@", "sales@", "cs"
        ])
        
        if is_corporate_email:
            email_status = "BLOQUEADO (Correo corporativo de soporte)"
            display_email = ""
        else:
            email_status = "Válido"
            display_email = raw_email

        return {
            "invoice": invoice,
            "nombre": record.get("name") or "",
            "apellido": record.get("lastName") or "",
            "tienda": record.get("storeName") or "",
            "region": record.get("region") or "",
            "telefono_original": raw_phone,
            "telefono_sanitizado": display_phone,
            "telefono_estado": phone_status,
            "email_original": raw_email,
            "email_sanitizado": display_email,
            "email_estado": email_status,
            "shipping": record.get("shipping") or ""
        }
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}

def procesar_caso_zendesk(invoice: str, nombre: str, email: str, telefono: str, tienda: str, region: str) -> dict:
    """
    Registra o actualiza un caso en Zendesk para el cliente con la información proporcionada.
    Valida y bloquea automáticamente teléfonos de empresa (8882494189) antes de enviarlo.
    """
    clean_phone = "".join(filter(str.isdigit, telefono or ""))
    if len(clean_phone) > 10:
        clean_phone = clean_phone[-10:]
    if clean_phone in COMPANY_PHONES:
        return {"error": "No se puede crear el caso de Zendesk: El teléfono es el número corporativo de la empresa (8882494189) y está prohibido usarlo."}
        
    url = "http://localhost:3000/api/process-zendesk-case"
    payload = {
        "invoice": invoice,
        "name": nombre,
        "email": email,
        "phone": telefono,
        "comment": "",
        "store_location": tienda,
        "region": region
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Error al procesar en Zendesk. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión al procesar Zendesk: {str(e)}"}

def obtener_textos_de_archivos(invoice: str) -> dict:
    """
    Busca todos los archivos (VIP, Shipping, ID, etc.) relacionados con el invoice en ShareFile,
    realiza el escaneo OCR y devuelve el nombre del archivo y el texto completo (transcripción) de cada uno.
    Utilízalo para analizar el contenido real de los archivos del cliente de manera inteligente.
    """
    url = f"http://localhost:3000/api/invoice-raw-texts?invoice={invoice}"
    try:
        response = requests.get(url, timeout=90)  # El OCR de múltiples archivos puede tardar un poco
        if response.status_code != 200:
            return {"error": f"Error al recuperar textos. Código: {response.status_code}"}
        return response.json()
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}

root_agent = Agent(
    model='gemini-3.6-flash',
    name='root_agent',
    description='Asistente experto para Shipping Autofill y redacción de correos.',
    instruction="""Eres un asistente experto para el sistema Shipping Autofill.
Sigue estrictamente estas reglas al responder preguntas, procesar archivos o utilizar tus herramientas:

1. Bloqueo de Teléfono Corporativo: El número '8882494189' (y '18882494189') es el teléfono de la propia empresa. Está ESTRICTAMENTE PROHIBIDO utilizarlo como dato de contacto del cliente. Si la herramienta 'buscar_y_sanitizar_cliente' te lo devuelve bloqueado o vacío, nunca intentes deducirlo o usarlo.
2. Prioridad de Documentos: Para extraer el nombre, apellido, correo o teléfono del cliente, prioriza siempre los archivos con 'VIP' o '_V' en el nombre. Si no existen, usa archivos con 'shippin' o 'shipping'. Si tampoco existen, usa archivos con 'PASS', 'ID' o 'PP'.
3. Extracción de Email: Pon especial atención en extraer con precisión el correo electrónico del cliente desde los archivos VIP, ya que son la fuente primaria de verdad.
4. Exclusiones de Archivos: Ignora por completo cualquier archivo que contenga 'gbk' o '5cc' en el nombre, ya que representan material promocional de la empresa o copias de tarjetas bancarias, no datos de contacto del cliente.
5. Análisis inteligente de archivos: Si el usuario te pide analizar o extraer información de los archivos de una factura, usa la herramienta 'obtener_textos_de_archivos' para obtener sus transcripciones. Luego, analiza detalladamente el texto de todos los archivos juntos, distinguiendo la información del cliente del texto de la empresa, y descarta cualquier número de teléfono o correo corporativo (ej. 8882494189) antes de responder.""",
    tools=[buscar_y_sanitizar_cliente, procesar_caso_zendesk, obtener_textos_de_archivos],
)

