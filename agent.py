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

    if REGEX_EXCLUDED.search(fname):
        categoria = "EXCLUIDO_EMPRESA_DEUDOR (GBK/5CC/50C/CID)"
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
            "notas": record.get("csComment") or ""
        }
    except Exception as e:
        return {"error": f"Error de conexión con el backend: {str(e)}"}

def consultar_productos_pendientes(invoice: str) -> dict:
    """
    Consulta si un cliente tiene productos o envíos pendientes por recibir.
    Toma ÚNICA Y EXCLUSIVAMENTE la Columna M ('PRODUCTS') de la hoja de cálculo MASTER SHIPPINGS (1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY).
    """
    clean_inv = str(invoice or "").strip()
    data = buscar_y_sanitizar_cliente(clean_inv)
    if "error" in data:
        return data

    productos_columna_m = str(data.get("productos") or "").strip()
    tiene_pendientes = bool(productos_columna_m)

    return {
        "invoice": clean_inv,
        "cliente": f"{data.get('nombre', '')} {data.get('apellido', '')}".strip(),
        "spreadsheet_id": "1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY",
        "hoja_origen": "MASTER SHIPPINGS",
        "columna": "Columna M (PRODUCTS)",
        "tiene_productos_pendientes": tiene_pendientes,
        "productos_pendientes": productos_columna_m if tiene_pendientes else "Ninguno (Columna M vacía)",
        "pestaña_año": data.get("year_tab", "")
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

6. Guardado en Google Sheets (MASTER SHIPPINGS / Customer Information): Si el usuario solicita guardar o aplicar cambios en Google Sheets, utiliza `actualizar_registro_sheets` o `escanear_y_rellenar_master_shippings(invoice, guardar_en_sheets=True)`.

7. Validación de correo electrónico: Utiliza `validar_correo` únicamente para un correo individual cuando el usuario lo solicite (prohibido validar en lote).

8. REGLA ESTRICTA DE PRODUCTOS / ENVÍOS PENDIENTES (COLUMNA M DE MASTER SHIPPINGS):
   - Cuando se te pregunte si un cliente tiene productos pendientes por recibir o si se trata de que haya un envío pendiente:
   - Tu ÚNICA Y EXCLUSIVA fuente de verdad es la **Columna M (`PRODUCTS`)** de la hoja de cálculo **MASTER SHIPPINGS** (`1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY`).
   - Utiliza `consultar_productos_pendientes(invoice)` o `buscar_y_sanitizar_cliente(invoice)`.
   - Lee el campo de la Columna M (`productos`). Si contiene productos anotados, esos son los productos/envíos pendientes del cliente.
   - Si la Columna M está vacía o no tiene productos registrados, indícale claramente al usuario que el cliente NO tiene envíos ni productos pendientes registrados en la Columna M de MASTER SHIPPINGS.

9. Búsqueda y Asignación de External ID en Zendesk:
   - Cuando el usuario pregunte si un cliente existe en Zendesk (o pida buscar por correo, teléfono o nombre):
     * Utiliza `consultar_usuario_zendesk` o `sincronizar_usuario_con_zendesk`.
     * Prioriza siempre la búsqueda por **Email** (fuente VIP de verdad), luego por **Teléfono** (sanitizado de 10 dígitos, omitiendo números corporativos de la empresa como 8882494189) y por **Nombre**.
   - Para asignar o actualizar el `external_id` en Zendesk según el invoice:
     * Si el usuario existe en Zendesk y no tiene el `external_id` o difiere del invoice, utiliza `actualizar_usuario_zendesk` o `sincronizar_usuario_con_zendesk(invoice)`.
     * Si el usuario solo proporciona el invoice, obtén sus datos con `buscar_y_sanitizar_cliente(invoice)` o `sincronizar_usuario_con_zendesk(invoice)`.
     * Responde de forma clara indicando el ID del usuario en Zendesk, su nombre, email y el estado de la asignación del `external_id`.""",
    tools=[
        buscar_y_sanitizar_cliente,
        consultar_productos_pendientes,
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


