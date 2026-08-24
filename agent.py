import os
import requests
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

from google.adk.agents.llm_agent import Agent

# URL del backend Node.js (auto-detecta si está en Cloud Run o local)
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None
DEFAULT_BACKEND = "https://customer-information-720693669884.us-central1.run.app" if IS_CLOUD_RUN else "http://localhost:3000"
BACKEND_URL = os.environ.get("BACKEND_URL", DEFAULT_BACKEND)

# Lista negra de teléfonos de empresa corporativos
COMPANY_PHONES = {"8882494189", "18882494189"}

def buscar_y_sanitizar_cliente(invoice: str) -> dict:
    """
    Busca los datos de un cliente en la base de datos por su número de invoice,
    limpia y sanitiza la información distinguiendo datos de empresa de datos de cliente,
    y bloquea estrictamente los teléfonos y correos corporativos (ej. 8882494189).
    """
    url = f"{BACKEND_URL}/api/sheets/record-by-invoice?invoice={invoice}"
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
        
    url = f"{BACKEND_URL}/api/process-zendesk-case"
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
    url = f"{BACKEND_URL}/api/invoice-raw-texts?invoice={invoice}"
    try:
        response = requests.get(url, timeout=90)  # El OCR de múltiples archivos puede tardar un poco
        if response.status_code != 200:
            return {"error": f"Error al recuperar textos. Código: {response.status_code}"}
        return response.json()
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}

def actualizar_registro_sheets(
    invoice: str,
    nombre: str = None,
    apellido: str = None,
    telefono: str = None,
    email: str = None,
    region: str = None,
    zipCode: str = None,
    productos: str = None,
    shipping: str = None,
    comentario: str = None
) -> dict:
    """
    Actualiza los datos de un cliente en Google Sheets para una factura específica.
    Usa esta herramienta cuando el usuario pida explícitamente guardar, corregir o actualizar datos en la hoja de cálculo.
    
    Args:
        invoice: El número de factura del cliente (ej: 'MM-12345' o '12345').
        nombre: El primer nombre del cliente (ej: 'John').
        apellido: El apellido del cliente (ej: 'Doe').
        telefono: El número de teléfono de contacto (ej: '3051234567'). No se permiten números corporativos.
        email: El correo electrónico del cliente (ej: 'john.doe@gmail.com'). No se permiten correos corporativos.
        region: El estado, región o ciudad del cliente (ej: 'FL' o 'Miami').
        zipCode: El código postal de 5 dígitos (ej: '33101').
        productos: La lista de productos comprados (ej: 'C.TOX Moisturizer').
        shipping: Estado de envío del cliente ('SI' o 'NO').
        comentario: Comentario opcional que se guardará en la columna de notas (columna NOTES).
    """
    columns_ek = {}
    if nombre is not None: columns_ek["E"] = str(nombre).strip()
    if apellido is not None: columns_ek["F"] = str(apellido).strip()
    
    if telefono is not None:
        clean_phone = "".join(filter(str.isdigit, str(telefono)))
        if len(clean_phone) > 10:
            clean_phone = clean_phone[-10:]
        if clean_phone in COMPANY_PHONES:
            return {"error": "No se puede actualizar en Sheets: El teléfono es el número corporativo de la empresa y está prohibido."}
        columns_ek["G"] = str(telefono).strip()
        
    if email is not None:
        raw_email = str(email).strip().lower()
        is_corporate_email = any(word in raw_email for word in [
            "customerservice", "noreply", "info@", "support@", "hello@", "contact@", "admin@", "sales@", "cs"
        ])
        if is_corporate_email:
            return {"error": "No se puede actualizar en Sheets: El correo es de tipo corporativo/soporte y está prohibido."}
        columns_ek["H"] = raw_email
        
    if region is not None: columns_ek["I"] = str(region).strip()
    if zipCode is not None: columns_ek["J"] = str(zipCode).strip()
    if productos is not None: columns_ek["K"] = str(productos).strip()
    if shipping is not None:
        clean_shipping = str(shipping).strip().upper()
        if clean_shipping in ["SI", "YES", "S"]:
            clean_shipping = "SI"
        elif clean_shipping in ["NO", "N"]:
            clean_shipping = "NO"
        columns_ek["M"] = clean_shipping

    payload = {
        "invoice": invoice,
        "comment": comentario,
        "extractionData": {
            "columnsEK": columns_ek
        }
    }
    
    url = f"{BACKEND_URL}/api/sheets/update"
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Error al actualizar Sheets. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión al actualizar Sheets: {str(e)}"}

def buscar_clientes(filtro: str = None, campo: str = None, valor: str = None) -> dict:
    """
    Busca y filtra clientes en Google Sheets que cumplan con ciertas condiciones o características.
    Usa esta herramienta cuando el usuario pregunte por listas de clientes de una tienda, región,
    con disputas/notas o que compraron un producto específico.
    
    Args:
        filtro: Búsqueda de texto libre general que coincida con cualquier campo del registro (nombre, productos, notas, etc.).
        campo: Filtrar por un campo específico ('invoice', 'nombre', 'tienda', 'region', 'email', 'telefono', 'productos', 'notas', 'shipping').
        valor: El valor exacto o parcial a buscar dentro del campo especificado.
    """
    url = f"{BACKEND_URL}/api/sheets"
    try:
        response = requests.get(url, timeout=20)
        if response.status_code != 200:
            return {"error": f"No se pudo consultar la lista de clientes. Código: {response.status_code}"}
        
        data = response.json()
        if not data.get("success") or "data" not in data:
            return {"error": "No se obtuvieron registros de Sheets."}
            
        records = data["data"]
        resultados = []
        
        # Mapeo de campos a llaves de la respuesta del backend
        key_mapping = {
            "invoice": "invoice",
            "nombre": "customerName",
            "tienda": "storeName",
            "region": "region",
            "email": "email",
            "telefono": "phone",
            "productos": "products",
            "notas": "csComment",
            "shipping": "shipping"
        }
        
        # Normalizar filtros
        search_campo = str(campo).strip().lower() if campo else None
        search_valor = str(valor).strip().lower() if valor else None
        search_filtro = str(filtro).strip().lower() if filtro else None
        
        for r in records:
            match = True
            
            # 1. Filtro exacto por campo/valor
            if search_campo and search_valor:
                target_key = key_mapping.get(search_campo, search_campo)
                field_val = str(r.get(target_key) or "").lower()
                if search_valor not in field_val:
                    match = False
                    
            # 2. Filtro de búsqueda libre
            if match and search_filtro:
                combined_text = " ".join([
                    r.get("invoice") or "",
                    r.get("customerName") or "",
                    r.get("storeName") or "",
                    r.get("region") or "",
                    r.get("email") or "",
                    r.get("phone") or "",
                    r.get("products") or "",
                    r.get("csComment") or ""
                ]).lower()
                if search_filtro not in combined_text:
                    match = False
                    
            if match:
                resultados.append({
                    "invoice": r.get("invoice") or "",
                    "nombre": r.get("customerName") or "",
                    "tienda": r.get("storeName") or "",
                    "region": r.get("region") or "",
                    "email": r.get("email") or "",
                    "telefono": r.get("phone") or "",
                    "productos": r.get("products") or "",
                    "notas": r.get("csComment") or "",
                    "shipping": r.get("shipping") or ""
                })
                
        return {"total_coincidencias": len(resultados), "resultados": resultados[:30]}
        
    except Exception as e:
        return {"error": f"Error de conexión al buscar clientes: {str(e)}"}

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
5. Análisis inteligente de archivos: Si el usuario te pide analizar o extraer información de los archivos de una factura, usa la herramienta 'obtener_textos_de_archivos' para obtener sus transcripciones. Luego, analiza detalladamente el texto de todos los archivos juntos, distinguiendo la información del cliente del texto de la empresa, y descarta cualquier número de teléfono o correo corporativo (ej. 8882494189) antes de responder.
6. Actualización de campos del formulario: Si el usuario te pide corregir, cambiar o actualizar un dato del cliente en el formulario (nombre, apellido, teléfono, email, ciudad, estado, código postal o productos), incluye en tu respuesta un bloque JSON con el siguiente formato EXACTO (sin backticks ni markdown):
FIELD_UPDATE:{"name":"valor","lastName":"valor","telephone":"valor","email":"valor","city":"valor","state":"valor","zipCode":"valor","products":"valor"}
Incluye SOLO los campos que el usuario quiere modificar. Por ejemplo, si solo quiere cambiar el email:
FIELD_UPDATE:{"email":"nuevo@correo.com"}
Si el usuario dice algo como "el correo es maria@gmail.com", "cambia el nombre a Juan", "el teléfono correcto es 3051234567", o "agrega producto X", responde confirmando el cambio Y emite el bloque FIELD_UPDATE correspondiente.
7. Consulta y búsqueda de clientes: Si el usuario te pregunta por listas de clientes que cumplan con ciertas condiciones (por ejemplo, clientes de una tienda, una región, compras de un producto específico, o notas como disputas), utiliza la herramienta 'buscar_clientes' para obtener los registros y respóndele de forma resumida y profesional.
8. Guardado y actualización en Google Sheets: Si el usuario te solicita guardar, registrar o aplicar cambios en la hoja de cálculo o Google Sheets (por ejemplo: "guarda los cambios en sheets", "registra el teléfono en sheets"), utiliza la herramienta 'actualizar_registro_sheets' para aplicar los cambios directamente en la base de datos.""",
    tools=[buscar_y_sanitizar_cliente, procesar_caso_zendesk, obtener_textos_de_archivos, actualizar_registro_sheets, buscar_clientes],
)

