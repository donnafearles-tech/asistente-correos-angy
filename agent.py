import os
import requests
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

from google.adk.agents.llm_agent import Agent

# URL del backend Node.js (auto-detecta si está en Cloud Run o local)
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None
DEFAULT_BACKEND = "https://customer-information-720693669884.us-central1.run.app" if IS_CLOUD_RUN else "http://localhost:3000"
BACKEND_URL = os.environ.get("BACKEND_URL", DEFAULT_BACKEND)
SELLER_ASSISTANT_URL = os.environ.get("SELLER_ASSISTANT_URL", "https://seller-assitant-720693669884.europe-west1.run.app")

# Lista negra de teléfonos de empresa corporativos
COMPANY_PHONES = {"8882494189", "18882494189"}

import re

REGEX_VIP = re.compile(r"(?:^|[\s_-])(?:v|vip)(?:[\s_.-]|$)", re.IGNORECASE)
REGEX_SHIPPING = re.compile(r"shippin(?:g)?(?:\s*form)?", re.IGNORECASE)
REGEX_PURCHASE_INFO = re.compile(r"purchase\s+inf[o0]rmati[o0]n", re.IGNORECASE)
REGEX_TICKET_NOVA = re.compile(r"(?:_(?:n|nov)\b|[\s_-]n\.jpe?g$|\bnova\b|\binvoice[\s_-]*\d{4,6}\.jpe?g$)", re.IGNORECASE)
REGEX_ID = re.compile(r"(?:^|[\s_-])(?:id|pass|passport|pp|dl|license)(?:[\s_.-]|$)", re.IGNORECASE)
REGEX_EXCLUDED = re.compile(r"(?:gbk|5cc|50c|ccid|(?<![a-z])cid(?![a-z]))", re.IGNORECASE)
REGEX_RETURN = re.compile(r"(?:^|[_\s.-])(?:r|ret|returns?|refunds?|devoluci[oó]n)(?:[_\s.-]\d*|\d+|$)", re.IGNORECASE)


def sanitizar_producto_devolucion(nombre: str) -> str:
    """
    Sanitiza el nombre de un producto devuelto o negativo eliminando:
    - Signos aritméticos (+, -, parentesis)
    - Cantidades negativas (-1, -2, Qty -1)
    - Precios (-$100.00, $ -50.00)
    - Códigos de producto, IDs y SKUs (ID-12345, SKU 99, ITEM #12)
    """
    if not nombre:
        return ""
    s = str(nombre).strip()
    s = re.sub(r"\(\s*-\s*\$?\s*\d+(?:\.\d+)?\s*\)", "", s)
    s = re.sub(r"\(\s*\$?\s*\d+(?:\.\d+)?\s*-\s*\)", "", s)
    s = re.sub(r"\(\s*-\s*\d+\s*\)", "", s)
    s = re.sub(r"-\s*\$\s*\d+(?:\.\d+)?", "", s)
    s = re.sub(r"\$\s*-\s*\d+(?:\.\d+)?", "", s)
    s = re.sub(r"\$\s*\d+(?:\.\d+)?", "", s)
    s = re.sub(r"^[+-]\s*\d+\s*(?:x|ea|pc)?\s*", "", s, flags=re.I)
    s = re.sub(r"\b(?:qty|cant|cantidad)\s*[:x-]?\s*-?\d+\b", "", s, flags=re.I)
    s = re.sub(r"\b(?:price|precio|total|subtotal|amount)\b[^\n]*", "", s, flags=re.I)
    s = re.sub(r"\b(?:ID|SKU|REF|ITEM|CODE|COD|ART)[-:\s#]*[A-Za-z0-9_-]+\b", "", s, flags=re.I)
    s = re.sub(r"\bID-\d+\b", "", s, flags=re.I)
    s = re.sub(r"#\s*\d+\b", "", s)
    s = re.sub(r"\b\d{6,}\b", "", s)
    s = re.sub(r"^[-+*•±/\\–—\s\"'`]+", "", s)
    s = re.sub(r"[-+*•±/\\–—\s\"'`]+$", "", s)
    s = re.sub(r"[:;|,]+$", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clasificar_archivo_nomenclatura(file_name: str, text: str = "", is_letter_size: bool = None) -> dict:
    """
    Clasifica un archivo según las reglas Regex oficiales de Shipping Autofill y la regla
    imbricada de Tamaño Carta + 'Purchase information'.
    """
    fname = str(file_name or "").strip()
    raw_text = str(text or "")
    has_purchase_info = bool(REGEX_PURCHASE_INFO.search(raw_text))
    letter_ok = True if is_letter_size is None else bool(is_letter_size)
    is_purchase_info_shipping_doc = has_purchase_info and letter_ok
    is_return_doc = bool(REGEX_RETURN.search(fname)) or fname.lower().endswith("_r.jpg") or fname.lower().endswith("-r.jpg")

    if REGEX_EXCLUDED.search(fname):
        categoria = "EXCLUIDO_EMPRESA_DEUDOR (GBK/5CC/50C/CID)"
    elif is_return_doc:
        categoria = "DEVOLUCION_RETURN (_R / RETURN)"
    elif REGEX_VIP.search(fname) or "vip" in fname.lower():
        categoria = "VIP_FORM (_V / VIP)"
    elif REGEX_SHIPPING.search(fname):
        categoria = "SHIPPING_FORM (Nombre estándar)"
    elif is_purchase_info_shipping_doc:
        categoria = "SHIPPING_FORM_POR_CONTENIDO (Tamaño Carta + 'Purchase information')"
    elif REGEX_TICKET_NOVA.search(fname):
        categoria = "TICKET_COMPRA_NOVA (_N / _NOV / NOVA / INVOICE)"
    elif REGEX_ID.search(fname):
        categoria = "DOCUMENTO_IDENTIDAD (_ID / PASS / PP)"
    else:
        categoria = "OTRO_DOCUMENTO"

    return {
        "categoria": categoria,
        "isLetterSize": is_letter_size,
        "hasPurchaseInformation": has_purchase_info,
        "isPurchaseInfoShippingDoc": is_purchase_info_shipping_doc,
        "isReturnDocument": is_return_doc,
    }


def buscar_y_sanitizar_cliente(invoice: str) -> dict:
    """
    Busca los datos de un cliente en Google Sheets (tanto en Customer Information como en MASTER SHIPPINGS
    en todas las pestañas 2023, 2024, 2025, 2026) por su número de invoice, limpia y sanitiza la información
    y bloquea estrictamente los teléfonos y correos corporativos (ej. 8882494189).
    """
    clean_inv = str(invoice or "").strip()
    url = f"{BACKEND_URL}/api/sheets/record-by-invoice?invoice={clean_inv}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return {"error": f"No se pudo consultar el backend. Código: {response.status_code}"}
        
        data = response.json()
        if not data.get("success") or not data.get("record"):
            return {"error": f"No se encontró registro en Google Sheets para la factura {clean_inv}. Puedes usar 'escanear_y_rellenar_master_shippings' o 'obtener_textos_de_archivos' para buscarla directamente en ShareFile."}
        
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
            "invoice": record.get("invoice") or clean_inv,
            "year_tab": record.get("year") or "",
            "nombre": record.get("name") or "",
            "apellido": record.get("lastName") or "",
            "tienda": record.get("storeName") or "",
            "fecha_venta": record.get("fecha") or "",
            "vendedor_sales_rep": record.get("salesRep") or "",
            "direccion_address_1": record.get("address") or "",
            "ciudad_city": record.get("city") or "",
            "region_state": record.get("region") or "",
            "codigo_postal_zip": record.get("zipCode") or "",
            "pais_country": record.get("country") or "",
            "productos": record.get("products") or "",
            "telefono_original": raw_phone,
            "telefono_sanitizado": display_phone,
            "telefono_estado": phone_status,
            "email_original": raw_email,
            "email_sanitizado": display_email,
            "email_estado": email_status,
            "shipping": record.get("shipping") or "",
            "notas": record.get("csComment") or "",
            # Distinción de Hojas y Datos de MASTER SHIPPINGS
            "found_in_master": record.get("foundInMaster", False),
            "master_shippings_products": record.get("masterProducts") or "",
            "master_shippings_notes": record.get("masterNotes") or "",
            "is_shipping_no": record.get("isShippingNo", False),
            "has_pending_products": record.get("hasPendingProducts", False),
            "pending_reason": record.get("pendingReason") or ""
        }
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}

def consultar_productos_pendientes(invoice: str, buscar_compensaciones_o_regalos: bool = False) -> dict:
    """
    Consulta si un cliente tiene productos o envíos pendientes por recibir siguiendo el protocolo estricto:
    
    1. Verificación previa en Customer Information - Shippings (Columna M 'SHIPPING'):
       - Si la Columna M (SHIPPING) dice 'SI' o 'YES':
         * Extrae los productos de la Columna M (PRODUCTS) de MASTER SHIPPINGS.
         * Informa: 'Hay productos pendientes de envío' y lista esos productos.
         
       - Si la Columna M (SHIPPING) dice 'NO':
         * NUNCA los menciona como 'productos para enviar'.
         * Respuesta inicial: 'No hay productos pendientes de envío para este invoice.'
         * Verificación de Retornos: Revisa si esos mismos productos están asociados a algún invoice clasificado como 'retorno' (archivos _R.jpg, RETURN.jpg o cantidades negativas).
         * Si se encuentran como parte de un retorno en otro invoice: Lo hace saber explícitamente, indicando que son productos que el cliente ha retornado o que se le deben regresar.
         * Si NO se encuentran como parte de un retorno: Pregunta obligatoriamente: '¿Deseas que haga una búsqueda de productos pedidos por algún agente por compensación o regalo?'
         * Si buscar_compensaciones_o_regalos=True: Realiza la búsqueda en MASTER SHIPPINGS de compensaciones o regalos agregados por agentes.
    """
    clean_inv = str(invoice or "").strip()
    data = buscar_y_sanitizar_cliente(clean_inv)
    if "error" in data:
        return data

    cliente_nombre = f"{data.get('nombre', '')} {data.get('apellido', '')}".strip()
    shipping_col_m_cust = str(data.get("shipping") or "").strip()
    is_shipping_no = shipping_col_m_cust.upper() in ["NO", "N"]
    is_shipping_yes = shipping_col_m_cust.upper() in ["YES", "SI"] or (bool(shipping_col_m_cust) and not is_shipping_no)

    # CASO 1: SHIPPING == "SI" / "YES"
    if is_shipping_yes:
        master_prod = str(data.get("master_shippings_products") or "").strip()
        cust_prods = str(data.get("productos") or "").strip()
        prods_extraidos = master_prod or cust_prods or "Envío registrado"

        return {
            "invoice": clean_inv,
            "cliente": cliente_nombre,
            "columna_m_shipping": "SI",
            "tiene_productos_pendientes": True,
            "estado": "PENDIENTES_DE_ENVIO",
            "respuesta": f"Hay productos pendientes de envío para este invoice: {prods_extraidos}",
            "productos_pendientes": prods_extraidos,
            "columna_m_master_shippings": master_prod or "Vacía"
        }

    # CASO 2: SHIPPING == "NO"
    # 2.1 Búsqueda de retorno asociado en otros invoices del cliente
    invoices_a_revisar = []
    
    # Revisar sesión activa de frontend
    try:
        sesion = obtener_sesion_activa_frontend()
        if sesion.get("hay_sesion_activa"):
            invs_sesion = sesion.get("invoices_detectados") or []
            for inv in invs_sesion:
                if str(inv).strip() and str(inv).strip() != clean_inv:
                    invoices_a_revisar.append(str(inv).strip())
    except Exception:
        pass

    # Si no hay invoices en sesión activa, buscar otros invoices del mismo cliente en Sheets
    if not invoices_a_revisar and (cliente_nombre or data.get("email_sanitizado") or data.get("telefono_sanitizado")):
        filtro = cliente_nombre or data.get("email_sanitizado") or clean_inv
        try:
            busq = buscar_clientes(filtro=filtro)
            for r in busq.get("resultados", []):
                inv_r = str(r.get("invoice") or "").strip()
                if inv_r and inv_r != clean_inv and inv_r not in invoices_a_revisar:
                    invoices_a_revisar.append(inv_r)
        except Exception:
            pass

    # Analizar si alguno de los invoices relacionados tiene retorno (_R.jpg o negativo)
    retorno_encontrado = None
    if invoices_a_revisar:
        try:
            analisis_dev = analizar_multiples_invoices_devolucion([clean_inv] + invoices_a_revisar)
            if analisis_dev.get("hay_devolucion"):
                inv_dev = analisis_dev.get("invoice_con_devolucion")
                prods_dev = analisis_dev.get("productos_devueltos_sanitizados") or []
                if inv_dev and inv_dev != clean_inv:
                    retorno_encontrado = {
                        "invoice_retorno": inv_dev,
                        "productos_retornados": ", ".join(prods_dev) if prods_dev else "Productos en retorno"
                    }
        except Exception:
            pass

    # Si se encontró retorno
    if retorno_encontrado:
        prods_ret = retorno_encontrado["productos_retornados"]
        inv_ret = retorno_encontrado["invoice_retorno"]
        return {
            "invoice": clean_inv,
            "cliente": cliente_nombre,
            "columna_m_shipping": "NO",
            "tiene_productos_pendientes": False,
            "estado": "PRODUCTOS_EN_RETORNO",
            "respuesta_inicial": "No hay productos pendientes de envío para este invoice.",
            "detalle_retorno": f"Los productos ({prods_ret}) se encuentran registrados como parte de un 'retorno' en el invoice {inv_ret} (archivos de devolución _R.jpg / cantidades negativas). Son productos que el cliente ha retornado o que se le deben regresar.",
            "invoice_retorno": inv_ret,
            "productos_retornados": prods_ret
        }

    # Si NO se encontró retorno pero se pidió buscar compensación/regalo
    if buscar_compensaciones_o_regalos:
        master_prod = str(data.get("master_shippings_products") or "").strip()
        if master_prod:
            return {
                "invoice": clean_inv,
                "cliente": cliente_nombre,
                "columna_m_shipping": "NO",
                "tiene_productos_pendientes": True,
                "es_compensacion_o_regalo": True,
                "respuesta": f"Se encontraron productos registrados por un agente en MASTER SHIPPINGS por compensación o regalo: {master_prod}",
                "productos_compensacion": master_prod
            }
        else:
            return {
                "invoice": clean_inv,
                "cliente": cliente_nombre,
                "columna_m_shipping": "NO",
                "tiene_productos_pendientes": False,
                "es_compensacion_o_regalo": False,
                "respuesta": "No se encontraron compensaciones ni regalos agregados por agentes en MASTER SHIPPINGS."
            }

    # Si NO hay retorno y NO se ha pedido compensación:
    return {
        "invoice": clean_inv,
        "cliente": cliente_nombre,
        "columna_m_shipping": "NO",
        "tiene_productos_pendientes": False,
        "estado": "SIN_ENVIOS_NI_RETORNOS",
        "respuesta_inicial": "No hay productos pendientes de envío para este invoice.",
        "pregunta_obligatoria": "¿Deseas que haga una búsqueda de productos pedidos por algún agente por compensación o regalo?"
    }

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

def consultar_usuario_zendesk(email: str = None, telefono: str = None, nombre: str = None, invoice: str = None) -> dict:
    """
    Consulta si un cliente/usuario existe en Zendesk buscando por correo electrónico, teléfono, nombre o número de invoice (external_id).
    Devuelve los datos del perfil en Zendesk (ID, nombre, email, teléfono, external_id, zona horaria y estado).
    """
    clean_email = str(email or "").strip().lower() if email else None
    
    clean_phone = None
    if telefono:
        raw_digits = "".join(filter(str.isdigit, str(telefono)))
        if len(raw_digits) > 10:
            raw_digits = raw_digits[-10:]
        if raw_digits not in COMPANY_PHONES and len(raw_digits) >= 10:
            clean_phone = raw_digits

    clean_name = str(nombre or "").strip() if nombre else None
    clean_inv = str(invoice or "").strip() if invoice else None

    if not clean_email and not clean_phone and not clean_name and not clean_inv:
        return {"error": "Debes proporcionar al menos un criterio para buscar en Zendesk: email, teléfono, nombre o invoice."}

    url = f"{BACKEND_URL}/api/zendesk/check-user"
    payload = {}
    if clean_email: payload["email"] = clean_email
    if clean_phone: payload["phone"] = clean_phone
    if clean_name: payload["name"] = clean_name
    if clean_inv: payload["invoice"] = clean_inv

    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if data.get("exists"):
                return {
                    "existe_en_zendesk": True,
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "email": data.get("email"),
                    "phone": data.get("phone"),
                    "external_id": data.get("external_id"),
                    "time_zone": data.get("time_zone"),
                    "detalles": data.get("details", {})
                }
            else:
                return {
                    "existe_en_zendesk": False,
                    "mensaje": f"No se encontró ningún usuario en Zendesk con los criterios especificados (email: {clean_email}, teléfono: {clean_phone}, nombre: {clean_name}, invoice: {clean_inv})."
                }
        return {"error": f"Error al consultar usuario en Zendesk. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión con Zendesk: {str(e)}"}

def actualizar_usuario_zendesk(
    user_id: int,
    external_id_invoice: str = None,
    nombre: str = None,
    telefono: str = None,
    zona_horaria: str = None,
    tienda: str = None,
    region: str = None,
    comentario: str = None
) -> dict:
    """
    Actualiza el perfil de un usuario existente en Zendesk por su User ID.
    Permite asignar el número de invoice como 'external_id', así como actualizar nombre, teléfono, zona horaria, tienda, región o notas.
    """
    if not user_id:
        return {"error": "Se requiere el 'user_id' de Zendesk para actualizar el usuario."}

    url = f"{BACKEND_URL}/api/zendesk/update-user"
    payload = {"userId": user_id}
    
    if external_id_invoice:
        payload["invoice"] = str(external_id_invoice).strip()
    if nombre:
        payload["name"] = str(nombre).strip()
    if telefono:
        clean_phone = "".join(filter(str.isdigit, str(telefono)))
        if len(clean_phone) > 10:
            clean_phone = clean_phone[-10:]
        if clean_phone in COMPANY_PHONES:
            return {"error": "No se puede actualizar el teléfono: Es el número corporativo de la empresa (8882494189)."}
        payload["phone"] = clean_phone
    if zona_horaria:
        payload["time_zone"] = zona_horaria
    if tienda:
        payload["store_location"] = tienda
    if region:
        payload["region"] = region
    if comentario:
        payload["comment"] = comentario

    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Error al actualizar usuario en Zendesk. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión al actualizar en Zendesk: {str(e)}"}

def sincronizar_usuario_con_zendesk(
    invoice: str,
    email: str = None,
    telefono: str = None,
    nombre: str = None
) -> dict:
    """
    Busca al usuario en Zendesk por email, teléfono o nombre (obteniendo los datos del invoice si no se especifican)
    y le asigna automáticamente el número de invoice como 'external_id' en su perfil de Zendesk.
    """
    clean_inv = str(invoice or "").strip()
    c_email = email
    c_phone = telefono
    c_name = nombre

    # Si falta información, consultar registro de cliente de Sheets
    if not (c_email and c_phone and c_name) and clean_inv:
        cliente_info = buscar_y_sanitizar_cliente(clean_inv)
        if "error" not in cliente_info:
            if not c_email and cliente_info.get("email_sanitizado"):
                c_email = cliente_info.get("email_sanitizado")
            if not c_phone and cliente_info.get("telefono_sanitizado"):
                c_phone = cliente_info.get("telefono_sanitizado")
            if not c_name and cliente_info.get("nombre"):
                c_name = f"{cliente_info.get('nombre', '')} {cliente_info.get('apellido', '')}".strip()

    # Consultar si existe en Zendesk
    busqueda = consultar_usuario_zendesk(email=c_email, telefono=c_phone, nombre=c_name, invoice=clean_inv)
    if "error" in busqueda:
        return busqueda

    if not busqueda.get("existe_en_zendesk"):
        return {
            "success": False,
            "existe_en_zendesk": False,
            "mensaje": f"El usuario correspondiente a la factura {clean_inv} no existe en Zendesk.",
            "datos_buscados": {"email": c_email, "telefono": c_phone, "nombre": c_name, "invoice": clean_inv}
        }

    user_id = busqueda.get("id")
    external_id_actual = busqueda.get("external_id")

    # Si ya tiene el external_id asignado
    if str(external_id_actual or "").strip() == clean_inv:
        return {
            "success": True,
            "actualizado": False,
            "mensaje": f"El usuario {busqueda.get('name')} (ID: {user_id}) ya tiene asignado el external_id '{clean_inv}'.",
            "user": busqueda
        }

    # Asignar external_id
    res_update = actualizar_usuario_zendesk(user_id=user_id, external_id_invoice=clean_inv)
    return {
        "success": True,
        "actualizado": True,
        "mensaje": f"Se asignó exitosamente el external_id '{clean_inv}' al usuario {busqueda.get('name')} (ID: {user_id}) en Zendesk.",
        "resultado_zendesk": res_update
    }

def obtener_textos_de_archivos(invoice: str) -> dict:
    """
    Busca todos los archivos (VIP, Shipping, Tamaño Carta con 'Purchase information', Ticket _N, ID, etc.)
    relacionados con el invoice en ShareFile (usando autorización perpetua vía seller-assitant / OAuth2 refresh_token),
    ejecuta los 3 momentos de escaneo (1. Descarga + Dimensiones, 2. Inferencia OCR + Detección 'Purchase information',
    3. Escritura en Firestore) y devuelve la clasificación Regex, dimensiones, campos extraídos y transcripción completa.
    """
    url = f"{BACKEND_URL}/api/invoice-raw-texts?invoice={invoice}"
    try:
        response = requests.get(url, timeout=180)
        if response.status_code != 200:
            return {"error": f"Error al recuperar textos. Código: {response.status_code}"}
        data = response.json()
        files = data.get("files") or []
        for f in files:
            clasif = clasificar_archivo_nomenclatura(
                f.get("fileName", ""),
                f.get("text", ""),
                f.get("isLetterSize")
            )
            f["clasificacion_nomenclatura"] = clasif["categoria"]
            f["isPurchaseInfoShippingDoc"] = bool(f.get("isPurchaseInfoShippingDoc") or clasif["isPurchaseInfoShippingDoc"])
        return data
    except requests.exceptions.ReadTimeout:
        return {"error": f"El escaneo de archivos para el invoice {invoice} tomó más de 180 segundos debido al volumen o tamaño de imágenes en ShareFile. Puedes intentar llamar directamente a 'escanear_y_rellenar_master_shippings'."}
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}


def escanear_y_rellenar_master_shippings(invoice: str, guardar_en_sheets: bool = False) -> dict:
    """
    Escanea de forma imbricada la carpeta de ShareFile del invoice pasando por los 3 momentos:
    1. MEDICIÓN DE DESCARGA DE IMAGEN (y verificación de proporción Tamaño Carta 8.5x11)
    2. MEDICIÓN DE INFERENCIA DE IA (Vertex/Gemini OCR + detección de la frase 'Purchase information' cuando un humano renombró mal el archivo shipping.jpg)
    3. MEDICIÓN DE ESCRITURA EN DB (Firestore)
    Extrae con prioridad:
    - 'Address 1 (Street)', 'City', 'Sales rep' y 'Country' desde el archivo Shipping o el archivo Tamaño Carta que contenga 'Purchase information'.
    - REGLA DE PAÍS: El 'Country' extraído explícitamente de este documento tiene PRIORIDAD ABSOLUTA sobre la deducción por número de teléfono.
    - DEVOLUCIONES: Detecta archivos de devolución (R.jpg, RETURN.jpg, etc.) y saldos negativos para enviarlos a la Columna M de MASTER SHIPPINGS.
    Si guardar_en_sheets=True, escribe además los datos en MASTER SHIPPINGS y Customer Information (creando la fila si no existe).
    """
    url = f"{BACKEND_URL}/api/scan-folder-and-process"
    try:
        response = requests.post(url, json={"invoice": str(invoice).strip(), "targetSheetType": "both"}, timeout=180)
        if response.status_code != 200:
            return {"error": f"Error al escanear carpeta en ShareFile. Código: {response.status_code}, Detalle: {response.text}"}
        scan_data = response.json()
        if not scan_data.get("success"):
            return scan_data

        extracted = scan_data.get("data") or {}
        sheet_update_result = None
        if guardar_en_sheets and extracted:
            return_prods = extracted.get("returnProducts")
            return_prods_str = "\n".join(return_prods) if isinstance(return_prods, list) else (str(return_prods) if return_prods else None)
            upd_res = actualizar_registro_sheets(
                invoice=str(invoice).strip(),
                nombre=extracted.get("name"),
                apellido=extracted.get("lastName"),
                telefono=extracted.get("phone"),
                email=extracted.get("email"),
                direccion=extracted.get("address"),
                ciudad=extracted.get("city"),
                region=extracted.get("state"),
                zipCode=extracted.get("zipCode"),
                pais=extracted.get("country"),
                fecha_venta=extracted.get("saleDate"),
                vendedor=extracted.get("salesRep"),
                tienda=extracted.get("storeName"),
                productos=extracted.get("products"),
                productos_devueltos=return_prods_str,
                destino_hoja="ambas",
            )
            sheet_update_result = upd_res

        return {
            "success": True,
            "invoice": invoice,
            "sharefile_auth_mode": "perpetual_oauth2_and_seller_assistant",
            "seller_assistant_url": SELLER_ASSISTANT_URL,
            "datos_extraidos_para_master_shippings": extracted,
            "archivos_procesados": scan_data.get("filesProcessed", []),
            "guardado_en_sheets": sheet_update_result,
        }
    except Exception as e:
        return {"error": f"Error en escanear_y_rellenar_master_shippings: {str(e)}"}


def actualizar_registro_sheets(
    invoice: str,
    nombre: str = None,
    apellido: str = None,
    telefono: str = None,
    email: str = None,
    direccion: str = None,
    ciudad: str = None,
    region: str = None,
    zipCode: str = None,
    pais: str = None,
    fecha_venta: str = None,
    vendedor: str = None,
    tienda: str = None,
    productos: str = None,
    productos_devueltos: str = None,
    shipping: str = None,
    comentario: str = None,
    destino_hoja: str = "ambas"
) -> dict:
    """
    Actualiza los datos de un cliente en Google Sheets (Customer Information y MASTER SHIPPINGS) para una factura específica.
    Si el invoice no existe en MASTER SHIPPINGS, se crea automáticamente una nueva fila al final del año correspondiente.
    Usa esta herramienta cuando el usuario pida explícitamente guardar, corregir o actualizar datos en la hoja de cálculo.
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

    extraction_data = {
        "columnsEK": columns_ek
    }
    if nombre is not None: extraction_data["name"] = str(nombre).strip()
    if apellido is not None: extraction_data["lastName"] = str(apellido).strip()
    if telefono is not None: extraction_data["telephone"] = str(telefono).strip()
    if email is not None: extraction_data["email"] = str(email).strip()
    if productos is not None: extraction_data["productsText"] = str(productos).strip()
    if productos_devueltos is not None: extraction_data["returnProducts"] = str(productos_devueltos).strip()
    if direccion is not None: extraction_data["address"] = str(direccion).strip()
    if ciudad is not None: extraction_data["city"] = str(ciudad).strip()
    if region is not None: extraction_data["state"] = str(region).strip()
    if zipCode is not None: extraction_data["zipCode"] = str(zipCode).strip()
    if pais is not None: extraction_data["country"] = str(pais).strip()
    if fecha_venta is not None: extraction_data["saleDate"] = str(fecha_venta).strip()
    if vendedor is not None: extraction_data["salesRep"] = str(vendedor).strip()
    if tienda is not None: extraction_data["storeName"] = str(tienda).strip()

    target_type = "master_shippings" if destino_hoja == "master_shippings" else ("both" if destino_hoja == "ambas" else "customer_info")
    payload = {
        "invoice": invoice,
        "comment": comentario,
        "targetSheetType": target_type,
        "extractionData": extraction_data
    }
    
    url = f"{BACKEND_URL}/api/sheets/update"
    try:
        response = requests.post(url, json=payload, timeout=25)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Error al actualizar Sheets. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión al actualizar Sheets: {str(e)}"}

def buscar_clientes(filtro: str = None, campo: str = None, valor: str = None) -> dict:
    """
    Busca y filtra clientes en Google Sheets (incluyendo MASTER SHIPPINGS y Customer Information en pestañas 2023-2026).
    Soporta búsqueda directa por número de invoice o texto libre sin desbordar el límite de memoria de Cloud Run.
    """
    search_campo = str(campo).strip().lower() if campo else None
    search_valor = str(valor).strip() if valor else None
    search_filtro = str(filtro).strip() if filtro else None

    # Si busca por invoice específico o un número de factura, consultar también record-by-invoice directamente
    candidate_invoice = None
    if search_campo == "invoice" and search_valor:
        candidate_invoice = search_valor
    elif search_filtro and re.match(r"^(?:inv[o0]ice[\s_#-]*|mm[\s_-]*)?\d{3,8}$", search_filtro, re.IGNORECASE):
        candidate_invoice = re.sub(r"^(?:inv[o0]ice[\s_#-]*|mm[\s_-]*)", "", search_filtro, flags=re.IGNORECASE).strip()
    elif search_valor and re.match(r"^(?:inv[o0]ice[\s_#-]*|mm[\s_-]*)?\d{3,8}$", search_valor, re.IGNORECASE):
        candidate_invoice = re.sub(r"^(?:inv[o0]ice[\s_#-]*|mm[\s_-]*)", "", search_valor, flags=re.IGNORECASE).strip()

    if candidate_invoice:
        direct = buscar_y_sanitizar_cliente(candidate_invoice)
        if "error" not in direct:
            return {"total_coincidencias": 1, "resultados": [direct]}

    query_term = search_valor or search_filtro or ""
    params = {"limit": "300"}
    if query_term:
        params["search"] = query_term

    url = f"{BACKEND_URL}/api/sheets"
    try:
        response = requests.get(url, params=params, timeout=25)
        if response.status_code != 200:
            return {"error": f"No se pudo consultar la lista de clientes. Código: {response.status_code}"}
        
        data = response.json()
        if not data.get("success") or "data" not in data:
            return {"error": "No se obtuvieron registros de Sheets."}
            
        records = data["data"]
        resultados = []
        
        key_mapping = {
            "invoice": "invoice",
            "nombre": "customerName",
            "tienda": "storeName",
            "region": "region",
            "ciudad": "city",
            "direccion": "address",
            "pais": "country",
            "vendedor": "salesRep",
            "email": "email",
            "telefono": "phone",
            "productos": "products",
            "notas": "csComment",
            "shipping": "shipping"
        }
        
        search_valor_lower = search_valor.lower() if search_valor else None
        search_filtro_lower = search_filtro.lower() if search_filtro else None
        
        for r in records:
            match = True
            if search_campo and search_valor_lower:
                target_key = key_mapping.get(search_campo, search_campo)
                field_val = str(r.get(target_key) or "").lower()
                if search_valor_lower not in field_val:
                    match = False
                    
            if match and search_filtro_lower:
                combined_text = " ".join([
                    str(r.get("invoice") or ""),
                    str(r.get("customerName") or ""),
                    str(r.get("storeName") or ""),
                    str(r.get("address") or ""),
                    str(r.get("city") or ""),
                    str(r.get("region") or ""),
                    str(r.get("country") or ""),
                    str(r.get("salesRep") or ""),
                    str(r.get("email") or ""),
                    str(r.get("phone") or ""),
                    str(r.get("products") or ""),
                    str(r.get("csComment") or "")
                ]).lower()
                if search_filtro_lower not in combined_text:
                    match = False
                    
            if match:
                resultados.append({
                    "invoice": r.get("invoice") or "",
                    "year": r.get("year") or "",
                    "nombre": r.get("customerName") or "",
                    "tienda": r.get("storeName") or "",
                    "fecha_venta": r.get("fecha") or "",
                    "vendedor": r.get("salesRep") or "",
                    "direccion": r.get("address") or "",
                    "ciudad": r.get("city") or "",
                    "region": r.get("region") or "",
                    "pais": r.get("country") or "",
                    "email": r.get("email") or "",
                    "telefono": r.get("phone") or "",
                    "productos": r.get("products") or "",
                    "notas": r.get("csComment") or "",
                    "shipping": r.get("shipping") or ""
                })
                
        return {"total_coincidencias": len(resultados), "resultados": resultados[:30]}
        
    except Exception as e:
        return {"error": f"Error de conexión al buscar clientes: {str(e)}"}

def validar_correo(email: str) -> dict:
    """
    Valida si una dirección de correo electrónico es válida, entregable y existe
    utilizando el motor de verificación Debounce.
    
    Args:
        email: La dirección de correo electrónico a verificar (ej: 'ejemplo@gmail.com').
    """
    url = f"{BACKEND_URL}/api/validate-email"
    payload = {"email": email}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Error al validar correo. Código: {response.status_code}, Detalle: {response.text}"}
    except Exception as e:
        return {"error": f"Error de conexión al validar correo: {str(e)}"}

def analizar_multiples_invoices_devolucion(invoices: list[str]) -> dict:
    """
    Analiza una relación N:1 donde N facturas (invoices) pertenecen a 1 mismo cliente.
    Examina los archivos de ShareFile y OCR para cada factura, detectando:
    1. Si contiene archivo de devolución (_R.jpg, RETURN.jpg, etc.) o productos/saldos negativos.
    2. Sanitiza los productos devueltos eliminando signos aritméticos (+, -, parentesis) e IDs/SKUs.
    3. Determina cuál es la factura de devolución que debe enviarse a MASTER SHIPPINGS y cuáles se omiten de MASTER SHIPPINGS (quedando solo en Customer Information).
    """
    if isinstance(invoices, str):
        invoices_list = [inv.strip() for inv in re.split(r"[\s,;\n]+", invoices) if inv.strip()]
    elif isinstance(invoices, list):
        invoices_list = [str(inv).strip() for inv in invoices if str(inv).strip()]
    else:
        return {"error": "Formato de invoices no válido. Proporciona una lista o string de números de invoice."}

    if not invoices_list:
        return {"error": "No se proporcionaron facturas para analizar."}

    resultados_invoices = []
    invoice_devolucion_seleccionada = None
    productos_devueltos_sanitizados = []

    for inv in invoices_list:
        scan_res = obtener_textos_de_archivos(inv)
        tiene_archivo_retorno = False
        archivos_detectados = []
        raw_negative_products = []

        if isinstance(scan_res, dict) and scan_res.get("files"):
            for f in scan_res.get("files", []):
                fname = f.get("fileName") or f.get("name") or ""
                clasif = clasificar_archivo_nomenclatura(fname, f.get("fullText", ""))
                es_retorno = clasif.get("isReturnDocument", False)
                if es_retorno:
                    tiene_archivo_retorno = True

                # Buscar números negativos o keywords de retorno en OCR
                ftext = f.get("fullText", "")
                if re.search(r"-\s*\$?\d+|\(\$?\d+\)|-\s*\d+\s*(?:x|ea|pc)?|\b(?:refund|return|devoluci[oó]n)\b", ftext, re.I):
                    for line in ftext.splitlines():
                        if re.search(r"-\s*\$?\d+|\(\$?\d+\)|-\s*\d+", line):
                            san = sanitizar_producto_devolucion(line)
                            if san and len(san) >= 3:
                                raw_negative_products.append(san)

                archivos_detectados.append({
                    "nombre": fname,
                    "categoria": clasif.get("categoria"),
                    "es_devolucion": es_retorno
                })

        # También verificar productos devueltos extraídos
        if isinstance(scan_res, dict) and scan_res.get("extractedData"):
            ext = scan_res.get("extractedData", {})
            ret_prods = ext.get("returnProducts") or ext.get("returnProductsList") or []
            if isinstance(ret_prods, list):
                for rp in ret_prods:
                    san = sanitizar_producto_devolucion(str(rp))
                    if san:
                        raw_negative_products.append(san)

        # Sanitizar y deduplicar productos de retorno
        prods_sanitizados = list(dict.fromkeys([p for p in raw_negative_products if p]))

        es_candidato_master = tiene_archivo_retorno or len(prods_sanitizados) > 0
        if es_candidato_master and not invoice_devolucion_seleccionada:
            invoice_devolucion_seleccionada = inv
            productos_devueltos_sanitizados = prods_sanitizados

        resultados_invoices.append({
            "invoice": inv,
            "tiene_archivo_retorno_r": tiene_archivo_retorno,
            "productos_negativos_detectados": prods_sanitizados,
            "debe_ir_a_master_shippings": es_candidato_master,
            "archivos": archivos_detectados
        })

    # Si ninguno tuvo _R.jpg ni negativos, seleccionar la primera para MASTER SHIPPINGS
    if not invoice_devolucion_seleccionada and resultados_invoices:
        invoice_devolucion_seleccionada = resultados_invoices[0]["invoice"]

    return {
        "relacion": "N:1 (Varios Invoices pertenecen a 1 Cliente)",
        "total_invoices_analizados": len(invoices_list),
        "invoice_seleccionado_para_master_shippings": invoice_devolucion_seleccionada,
        "productos_devueltos_sanitizados_para_columna_m": productos_devueltos_sanitizados,
        "regla_aplicada": f"Solo el invoice '{invoice_devolucion_seleccionada}' se envía a MASTER SHIPPINGS con productos sanitizados sin signos aritméticos ni IDs. Los demás invoices se registran únicamente en Customer Information.",
        "detalle_por_invoice": resultados_invoices
    }


def obtener_sesion_activa_frontend() -> dict:
    """Obtiene en tiempo real los datos cargados en la pantalla del usuario (frontend) y sincronizados en Firestore.
    Incluye: el invoice actual, datos del cliente (nombre, email, teléfono, dirección, etc.), archivos descargados de ShareFile, productos normales y productos de devolución, y estado del escaneo."""
    try:
        url = f"{BACKEND_URL}/api/session/active"
        req = urllib.request.Request(url, headers={"User-Agent": "AngyAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return {
                "estado": "exito",
                "origen": data.get("source", "memoria/firestore"),
                "sesion_pantalla": data.get("session", {})
            }
    except Exception as e:
        return {
            "estado": "error",
            "mensaje": f"No se pudo consultar la sesión activa del frontend: {str(e)}"
        }


def consultar_invoice_en_firestore(invoice: str) -> dict:
    """Consulta en Firestore la sesión persistida, telemetría y resultados de OCR previamente guardados para un número de invoice específico."""
    try:
        safe_inv = str(invoice).strip()
        url = f"{BACKEND_URL}/api/firestore/invoice-session?invoice={urllib.parse.quote(safe_inv)}"
        req = urllib.request.Request(url, headers={"User-Agent": "AngyAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        return {
            "success": False,
            "error": f"Error consultando Firestore para invoice {invoice}: {str(e)}"
        }


root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_agent',
    description='Asistente experto Angy para Shipping Autofill, MASTER SHIPPINGS y redacción de correos.',
    instruction="""Eres Angy, el agente experto e imbricado en el pipeline de Shipping Autofill y MASTER SHIPPINGS.
Hablas el mismo idioma técnico que la aplicación y dominas las reglas de nomenclatura Regex, los 3 momentos de telemetría de escaneo y la autorización perpetua con ShareFile.

Sigue estrictamente estas reglas al responder preguntas, procesar archivos o utilizar tus herramientas:

1. LOS 3 MOMENTOS DEL PIPELINE DE ESCANEO (Imbricación de Angy):
   - Momento 1 (`// 1. MEDICIÓN DE DESCARGA DE IMAGEN`): Se descarga la imagen desde ShareFile usando autorización perpetua OAuth2 / `https://seller-assitant-720693669884.europe-west1.run.app` y se inspecciona su cabecera binaria para detectar si el documento es **Tamaño Carta (`isLetterSize: true`, proporción ~0.77 / 8.5x11)**.
   - Momento 2 (`// 2. MEDICIÓN DE INFERENCIA DE IA (Vertex/Gemini OCR)`): Se extrae el texto y los campos estructurados. Si un humano renombró mal el archivo (no se llama `shipping.jpg` ni `shipping form.jpg`), pero el archivo es **Tamaño Carta** y contiene la frase **`"Purchase information"`** dentro del texto escaneado, se clasifica automáticamente como el documento **Shipping Form (`isPurchaseInfoShippingDoc: true`)**.
   - Momento 3 (`// 3. MEDICIÓN DE ESCRITURA EN DB (Firestore)`): Se persiste el resultado y las métricas de latencia en Firestore y se consolida para rellenar **MASTER SHIPPINGS**.

2. REGLAS OFICIALES DE NOMENCLATURA REGEX:
   - **VIP (`_V`, `VIP`)**: Fuente #1 de verdad para **Email** y **Teléfono** del cliente.
   - **SHIPPING (`shipping.jpg`, `shipping form.jpg`) o Documento Tamaño Carta con `"Purchase information"`**: Fuente #1 de verdad para extraer **`Address 1 (Street)`**, **`City`**, **`Sales rep`** y **`Country`**.
   - **DEVOLUCIÓN (`_R.jpg`, `R.jpg`, `RETURN.jpg`, `_r.jpg`)**: Archivos de retorno/reembolso para **MASTER SHIPPINGS**.
   - **PRIORIDAD ESTRICTA DE `Country`**: El `Country` extraído explícitamente del documento Shipping / `"Purchase information"` tiene **PRIORIDAD #1 ABSOLUTA** sobre el método de deducción por número de teléfono o código postal.
   - **TICKET DE COMPRA (`n.jpg`, `_n`, `_nov`, `nova`, `invoice_XXXXX.jpg`)**: Fuente prioritaria para **`SALE DATE`**, **`Sales rep`**, **`Location - Store`** y **`Productos`**.
   - **IDENTIDAD (`_id.jpg`, `pass`, `pp`, `id`)**: Prioritario para verificar ortografía de Nombre y Apellido manuscritos.
   - **EXCLUSIONES ESTRICTAS (`gbk`, `5cc`, `50c`, `cid`, `ccid`)**: Ignorar siempre (material promocional o copias de tarjetas).

3. Bloqueo de Teléfono Corporativo: El número '8882494189' (y '18882494189') es el teléfono corporativo de la empresa. Está ESTRICTAMENTE PROHIBIDO utilizarlo como teléfono del cliente.

4. Búsqueda de Facturas (Invoices) y Relleno de MASTER SHIPPINGS:
   - Cuando el usuario te pregunte por un número de invoice (ej. `19465`), usa primero `buscar_y_sanitizar_cliente` (que busca en todas las pestañas 2023, 2024, 2025 y 2026 de Customer Information y MASTER SHIPPINGS).
   - Si el usuario quiere analizar los archivos escaneados en ShareFile o rellenar MASTER SHIPPINGS para un invoice, utiliza `escanear_y_rellenar_master_shippings` u `obtener_textos_de_archivos`.

5. Actualización de campos del formulario en pantalla: Si el usuario te pide corregir, cambiar o actualizar un dato del cliente en el formulario, incluye en tu respuesta un bloque JSON con el siguiente formato EXACTO (sin backticks ni markdown):
FIELD_UPDATE:{"name":"valor","lastName":"valor","telephone":"valor","email":"valor","address":"valor","city":"valor","state":"valor","zipCode":"valor","country":"valor","saleDate":"valor","salesRep":"valor","storeName":"valor","products":"valor"}
Incluye SOLO los campos que el usuario quiere modificar.

6. Guardado en Google Sheets (MASTER SHIPPINGS / Customer Information):
   - REGLA OBLIGATORIA DE MASTER SHIPPINGS: Solo se envían o guardan registros a **MASTER SHIPPINGS** (`1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY`) **SI Y SOLO SI** entre los archivos del invoice existe el archivo nombrado **`"Shipping.jpg"`** o **`"shipping form.jpg"`**. Si no cuenta con dicho archivo, el guardado en MASTER SHIPPINGS queda ESTRICTAMENTE BLOQUEADO.
   - Si el usuario solicita guardar o aplicar cambios en Google Sheets, utiliza `actualizar_registro_sheets` o `escanear_y_rellenar_master_shippings(invoice, guardar_en_sheets=True)`.

7. Validación de correo electrónico: Utiliza `validar_correo` únicamente para un correo individual cuando el usuario lo solicite (prohibido validar en lote).

8. DISTINCIÓN DE HOJAS DE CÁLCULO Y REGLA LÓGICA DE PRODUCTOS PENDIENTES / RETORNOS:
   - Existen dos hojas de cálculo independientes que NUNCA debes confundir:
     * **Hoja 1**: `Customer Information - Shippings` (ID: `1-D0F025w23P9daLlLsfcGAbi9o_pnM1A_p70WtlZdK4`).
       -> Su **Columna M** es **`SHIPPING`** (indica si la venta requirió envío: "NO", "YES", "SI").
     * **Hoja 2**: `MASTER SHIPPINGS` (ID: `1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY`).
       -> Su **Columna M** es **`PRODUCTS`** (registra productos a despachar, devoluciones, o compensaciones/regalos agregados por un agente).
   - REGLA LÓGICA DE RESPUESTA A PRODUCTOS PENDIENTES:
     * **Antes de mencionar cualquier producto**, verificarás la Columna M (`SHIPPING`) en la hoja `Customer Information - Shippings` para el invoice correspondiente (mediante `consultar_productos_pendientes(invoice)`).
     * **SI la Columna M (`SHIPPING`) dice "SI" o "YES"**:
       -> Extraerás los productos de la **Columna M (`PRODUCTS`)** de **MASTER SHIPPINGS**.
       -> Informarás que: **"Hay productos pendientes de envío"** y listarás esos productos.
     * **SI la Columna M (`SHIPPING`) dice "NO"**:
       -> **NUNCA mencionarás esos productos como "productos para enviar"**.
       -> Tu respuesta inicial será: **"No hay productos pendientes de envío para este invoice."**
       -> Posteriormente, verificarás si esos mismos productos están asociados a algún invoice que haya sido clasificado como "return" o "retorno" (aquellos con archivos `_R.jpg`, `RETURN.jpg`, `_r.jpg` o cantidades negativas de productos).
       -> **Si los productos de este invoice (con SHIPPING "NO") se encuentran registrados como parte de un "retorno" en otro invoice**: Se lo harás saber explícitamente al usuario, indicando que **son productos que el cliente ha retornado o que se le deben regresar**.
       -> **Si NO se encuentran como parte de un "retorno"**: Preguntarás obligatoriamente: **"¿Deseas que haga una búsqueda de productos pedidos por algún agente por compensación o regalo?"**
     * **BÚSQUEDA DE COMPENSACIÓN O REGALO (Bajo solicitud explícita del usuario)**:
       -> Solo si el usuario lo solicita explícitamente (o responde afirmativamente a tu pregunta), se consulta MASTER SHIPPINGS con `consultar_productos_pendientes(invoice, buscar_compensaciones_o_regalos=True)` para verificar si un agente agregó productos por compensación o regalo.

9. Búsqueda y Asignación de External ID en Zendesk:
   - Cuando el usuario pregunte si un cliente existe en Zendesk (o pida buscar por correo, teléfono o nombre):
     * Utiliza `consultar_usuario_zendesk` o `sincronizar_usuario_con_zendesk`.
     * Prioriza siempre la búsqueda por **Email** (fuente VIP de verdad), luego por **Teléfono** (sanitizado de 10 dígitos, omitiendo números corporativos de la empresa como 8882494189) y por **Nombre**.
   - Para asignar o actualizar el `external_id` en Zendesk según el invoice:
     * Si el usuario existe en Zendesk y no tiene el `external_id` o difiere del invoice, utiliza `actualizar_usuario_zendesk` o `sincronizar_usuario_con_zendesk(invoice)`.
     * Si el usuario solo proporciona el invoice, obtén sus datos con `buscar_y_sanitizar_cliente(invoice)` o `sincronizar_usuario_con_zendesk(invoice)`.
     * Responde de forma clara indicando el ID del usuario en Zendesk, su nombre, email y el estado de la asignación del `external_id`.

10. RELACIÓN N:1 (VARIAS FACTURAS A UN CLIENTE) Y REGLA DE DEVOLUCIONES A MASTER SHIPPINGS:
    - En situaciones donde dos, tres o más números de factura (invoices) pertenecen a la **misma persona / cliente** (relación N:1 donde "1" es el cliente):
      * **NUNCA envíes todas las facturas a MASTER SHIPPINGS**.
      * **SOLO se envía a MASTER SHIPPINGS la factura que contiene el archivo de devolución (`_R.jpg`, `R.jpg`, `RETURN.jpg`, `_r.jpg`) o productos/cantidades en negativo**.
      * Las facturas estándar de compra (sin devolución) se registran únicamente en **Customer Information - Shippings**.
      * **Casos límite (múltiples invoices en la carpeta)**: Si el usuario o el sistema proveen 2 números pero en la carpeta existen 3 o más, Angy y la app deben inspeccionar los archivos e identificar con precisión cuál es el número de invoice con la devolución negativa.
      * **Sanitización estricta de productos en Columna M de MASTER SHIPPINGS**: Todos los productos devueltos deben limpiarse completamente sin signos aritméticos (`-`, `+`, `(-1)`, `-$`), sin cantidades ni IDs/SKUs/códigos de barras (`ID-XXXX`, `#123`). Únicamente nombres puros y limpios del producto.
      * Utiliza `analizar_multiples_invoices_devolucion` para inspeccionar y clasificar automáticamente el lote de facturas de un cliente.

11. AUTODETECCIÓN TOTAL Y TRANSPARENTE DEL FRONTEND Y FIRESTORE:
    - Angy recibe y procesa AUTOMÁTICAMENTE en cada mensaje el bloque `[CONTEXTO AUTOMÁTICO EN TIEMPO REAL DEL SISTEMA / PANTALLA]`.
    - NUNCA le pidas al usuario que te repita el invoice, el nombre del cliente, los teléfonos ni los archivos cargados si ya están en el contexto automático.
    - Utiliza `obtener_sesion_activa_frontend()` y `consultar_invoice_en_firestore(invoice)` para inspeccionar a fondo cualquier detalle técnico adicional sin necesidad de que el usuario lo solicite.

12. PROACTIVIDAD AUTOMÁTICA EN RELACIÓN N:1 Y DEVOLUCIONES:
    - El usuario NO necesita decir palabras clave ni disparadores. Angy y la app DETECTAN DE FORMA 100% AUTOMÁTICA:
      * Si hay múltiples facturas en pantalla/ShareFile para un cliente (relación N:1), las toma todas en cuenta de inmediato.
      * Cuál es exactamente la factura con devolución (`_R.jpg` o productos en negativo) y la aísla para MASTER SHIPPINGS.
      * La lista de productos sanitizados devueltos (limpios de signos, cantidades e IDs).
    - Angy responde directamente confirmando los hallazgos y proponiendo o ejecutando la acción precisa sin fricción para el usuario.""",
    tools=[
        obtener_sesion_activa_frontend,
        consultar_invoice_en_firestore,
        buscar_y_sanitizar_cliente,
        consultar_productos_pendientes,
        analizar_multiples_invoices_devolucion,
        procesar_caso_zendesk,
        consultar_usuario_zendesk,
        actualizar_usuario_zendesk,
        sincronizar_usuario_con_zendesk,
        obtener_textos_de_archivos,
        escanear_y_rellenar_master_shippings,
        actualizar_registro_sheets,
        buscar_clientes,
        validar_correo,
    ],
)


