import os
import requests
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

import json
import urllib.request
import urllib.parse
from google.adk.agents.llm_agent import Agent

# URL del backend Node.js (auto-detecta si está en Cloud Run o local)
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None
DEFAULT_BACKEND = "https://customer-information-720693669884.us-central1.run.app" if IS_CLOUD_RUN else "http://localhost:3000"
BACKEND_URL = os.environ.get("BACKEND_URL", DEFAULT_BACKEND)
SELLER_ASSISTANT_URL = os.environ.get("SELLER_ASSISTANT_URL", "https://seller-assitant-720693669884.europe-west1.run.app")

# Lista negra de teléfonos de empresa corporativos
COMPANY_PHONES = {"8882494189", "18882494189"}

import re

REGEX_VIP = re.compile(r"(?:^|[\s_-])vip(?:[\s_.-]|$)", re.IGNORECASE)
REGEX_SHIPPING = re.compile(r"shippin(?:g)?(?:\s*form)?", re.IGNORECASE)
REGEX_PURCHASE_INFO = re.compile(r"purchase\s+inf[o0]rmati[o0]n", re.IGNORECASE)
REGEX_TICKET_NOVA = re.compile(r"(?:_(?:n|nov)\b|[\s_-]n\.jpe?g$|\bnova\b|\binvoice[\s_-]*\d{4,6}\.jpe?g$)", re.IGNORECASE)
REGEX_ID = re.compile(r"(?:^|[\s_-])(?:id|pass|passport|pp|dl|license)(?:[\s_.-]|$)", re.IGNORECASE)
REGEX_EXCLUDED = re.compile(r"(?:gbk|5cc|50c|ccid|(?<![a-z])cid(?![a-z])|e[\s_.-]?transfer|transfer|(?:^|[_\s.-])v(?:\s*\(\d+\)|\s*\d+)?\.(?:jpe?g|png|webp)$)", re.IGNORECASE)
REGEX_RETURN = re.compile(r"(?:^|[_\s.-])(?:r|ret|returns?|refunds?|devoluci[oó]n)(?:[_\s.-]\d*|\d+|$)", re.IGNORECASE)
REGEX_N_SHIPPING = re.compile(r"(?:^|[_\s.-])n[\s_-]*shipping", re.IGNORECASE)


REGEX_EXCLUDE_DISCARD_LINE = re.compile(
    r"(?:"
    r"all\s+sales\s+are\s+final|"
    r"by\s+signing\s+(?:the\s+above|below)|"
    r"acknowledge\s+that|"
    r"exchanges?\s+for\s+unopened|"
    r"reconozco\s+la\s+posibilidad|"
    r"diferencias?\s+en\s+el\s+monto|"
    r"ground\s+free|"
    r"business\s+days?\s+express\s+delivery|"
    r"ship\s*date|"
    r"standard\s+shipping|"
    r"exchange\s+rate|"
    r"transacci[oó]n\s+en\s+d[oó]lares|"
    r"afiliaci[oó]n|"
    r"terminal\s*:\s*.*|"
    r"please\s+sign\s*&?\s*confirm|"
    r"visit\s+us\s+at|"
    r"morena\s+mia\s+beauty\s+group|"
    r"empire\s+tech(?:\s+cosmetics)?|"
    r"empire\s+ssa4|"
    r"chief\s+administrator|"
    r"driver'?s?\s*license|"
    r"endorsements?|"
    r"restrictions?|"
    r"\b(?:bbva|bancomer)\b|"
    r"\bus\s+toll\s+free\b|"
    r"\b(?:mvc|njcn\w+|rnqj\w+)\b|"
    r"c[\s-]*o[\s-]*m[\s-]*e[\s-]*r[\s-]*c[\s-]*i[\s-]*o|"
    r"\b(?:dob|exp|gender|hgt|eyes|class\s*d)\s*:|"
    r"\b(?:phone|email|telephone|tel[eé]fono)\s*:|"
    r"\b(?:city|state|zip\s*code|country|address)\s*:|"
    r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b|"
    r"^\+?1?\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$"
    r")",
    re.IGNORECASE
)

PALABRAS_INVALIDAS_DEVOLUCION = {
    "return", "refund", "returns", "refunds", "devolucion", "devolución",
    "void", "cancel", "cambio", "exchange", "item", "items", "producto",
    "productos", "total", "subtotal", "tax", "date", "customer", "signature",
    "visa", "mastercard", "amex", "cash", "credit", "debit", "sale", "invoice",
    "phone", "email", "rev", "us toll free#", "commerce", "c-o-m-e-r-c-i-o", "ssa4",
    "bbva", "transaccion en dolares", "afiliacion", "fecha", "rnqj6azmem", "njcn6m01",
    "mvc", "chief administrator", "dl r6696", "class d", "endorsements", "restrictions",
    "visit us at", "empire ssa4", "terminal", "terminal: vs i@", "ground free",
    "business days express delivery", "ship date", "dob", "exp", "gender", "hgt", "eyes"
}


def sanitizar_producto_devolucion(nombre: str) -> str:
    """
    Sanitiza el nombre de un producto devuelto o negativo para MASTER SHIPPINGS (Columna M):
    1. Signos aritméticos y simbolismos monetarios (+, -, (, ), $, %, $XX.XX, -1, x2, 3pcs, 50% OFF, -$XX.XX)
    2. Identificadores y códigos (IDs de producto, SKUs, códigos de barras, ID-XXXX, #123, SKU-Y, UPC: ZZZ)
    3. Información de contacto y datos personales (PHONE:, 609-707-4463, EMAIL:, direcciones, DOB, EXP, GENDER, HGT, EYES, DL, CLASS D, MVC, Chief Administrator)
    4. Términos y condiciones, descargos y legalidades ('All sales are final', políticas de venta, garantías)
    5. Información de envío y logística (Ground Free, Business Days Express Delivery, SHIP DATE)
    6. Información de transacción y pagos (Exchange Rate, TRANSACCION EN DOLARES, AFILIACION, Terminal: VS I@, BBVA)
    7. Palabras genéricas o ruido de OCR (Commerce, C-O-M-E-R-C-I-O, SSA4, RNQJ6AZMEM, NJCN6M01, Visit us at, nombres corporativos)
    Criterios de INCLUSIÓN: Preserva nombres puros como 'HYDRA . SILK', 'ACTIVING ELIXIR', 'ATOMIC HEATING MASK', 'D.TOX Peeling', 'C.TOX Moisturizer', 'Do\'r24K Milk Cleanser', 'SOLOS', 'A.TOX Syringe'.
    Preserva la cantidad física en valor absoluto: '<CANTIDAD> <PRODUCTO>' (ej: '1 ATLAS', '2 HYDRA . SILK').
    """
    if not nombre:
        return ""
    s = str(nombre).strip()

    # 0. Descarte inmediato por criterios de exclusión (legalidades, envíos, terminales, bancos, contacto, licencias, ruido OCR)
    if REGEX_EXCLUDE_DISCARD_LINE.search(s):
        return ""

    # Pre-limpieza para detectar cantidades ignorando precios, descuentos, SKUs y números de más de 5 dígitos
    temp = re.sub(r"[\$\€\£]\s*-?\s*\d+(?:\.\d+)?|-?\s*[\$\€\£]\s*\d+(?:\.\d+)?", " ", s)
    temp = re.sub(r"\b\d+\s*%\s*(?:off|discount|descuento)?\b", " ", temp, flags=re.I)
    temp = re.sub(r"\b(?:ID|SKU|UPC|REF|ITEM|CODE|COD|ART)[-:\s#]*[A-Za-z0-9_-]*\b", " ", temp, flags=re.I)
    temp = re.sub(r"#\s*\d+\b", " ", temp)

    detected_qty = 1
    found_qty = False

    # Patrón A: Paréntesis con cantidad o número: ( -2 ), ( Qty -3 ), ( Cantidad -1 ), ( 2 pcs )
    m_paren = re.search(r"\(\s*(?:qty|cant|cantidad)?\s*[:x-]?\s*[-–—]?\s*(\d+)\s*(?:x|ea|pc|units?|unidades?|pcs)?\s*\)", temp, flags=re.I)
    if m_paren:
        val = int(m_paren.group(1))
        if val > 0:
            detected_qty = val
            found_qty = True

    # Patrón B: Número negativo explícito: -1, -2, -3, -5
    if not found_qty:
        m_neg = re.search(r"(?:^|[\s(])[-–—]\s*(\d+)\b", temp)
        if m_neg:
            val = int(m_neg.group(1))
            if val > 0:
                detected_qty = val
                found_qty = True

    # Patrón C: Sufijo o prefijo con palabra clave o unidad: Qty: 2, 3pcs, x2, 2x
    if not found_qty:
        m_suff = re.search(r"\b(?:qty|cant|cantidad)\s*[:x-]?\s*[-–—]?\s*(\d+)\b|\b(\d+)\s*(?:x|ea|pc|pcs|units?|unidades?)\b|\b(?:x)\s*(\d+)\b", temp, flags=re.I)
        if m_suff:
            num_str = m_suff.group(1) or m_suff.group(2) or m_suff.group(3)
            if num_str:
                val = int(num_str)
                if val > 0:
                    detected_qty = val
                    found_qty = True

    # Patrón D: Cantidad al inicio seguida de espacio y letra: '1 ATLAS', '2 HYDRA'
    if not found_qty:
        m_lead = re.match(r"^[+-]?\s*(\d+)\s+[A-Za-z]", temp)
        if m_lead:
            val = int(m_lead.group(1))
            if val > 0:
                detected_qty = val
                found_qty = True

    # Ahora limpiar el nombre del producto
    cleaned = s

    # 1. Eliminar prefijos de archivos comunes
    cleaned = re.sub(r"^[A-Za-z0-9_-]+\.(?:jpe?g|png|pdf|tiff?)\s*[-–—:]*\s*", "", cleaned, flags=re.I)

    # 2. Eliminar prefijos de cabeceras de devolución / retorno
    cleaned = re.sub(
        r"^(?:returned\s+items?|refunded\s+items?|return\s+items?|productos?\s+devueltos?|items?\s+retornados?|returned|refunded|return|refund|devoluci[oó]n|retorno|void|exchange|cambio)\s*[:–—-]*\s*",
        "",
        cleaned,
        flags=re.I
    )

    # 3. Eliminar paréntesis con signo negativo, precios o cantidades
    cleaned = re.sub(r"\(\s*-\s*\$?\s*\d+(?:\.\d+)?\s*\)", "", cleaned)
    cleaned = re.sub(r"\(\s*\$?\s*\d+(?:\.\d+)?\s*-\s*\)", "", cleaned)
    cleaned = re.sub(r"\(\s*-\s*\d+\s*(?:x|ea|pc|units?|unidades?|pcs)?\s*\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\(\s*(?:qty|cant|cantidad|retornar|retorno)\s*[:x-]?\s*-?\d+[^)]*\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\(\s*cantidad\s*-\s*\d+[^)]*\)", "", cleaned, flags=re.I)

    # 4. Eliminar importes y precios negativos o positivos y porcentajes de descuento
    cleaned = re.sub(r"-\s*\$\s*\d+(?:\.\d+)?", "", cleaned)
    cleaned = re.sub(r"\$\s*-\s*\d+(?:\.\d+)?", "", cleaned)
    cleaned = re.sub(r"\$\s*\d+(?:\.\d+)?", "", cleaned)
    cleaned = re.sub(r"\b\d+\s*%\s*(?:off|discount|descuento)?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+\.\d{2}\b", "", cleaned)

    # 5. Eliminar cantidades y unidades negativas o normales al inicio/final
    cleaned = re.sub(r"^[+-]?\s*\d*\s*(?:x|ea|pc|units?|unidades?|pcs)\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^[+-]\s*\d+\s*", "", cleaned)
    cleaned = re.sub(r"^[+-]?\s*\d+\s+(?=[A-Za-z])", "", cleaned)
    cleaned = re.sub(r"\b(?:qty|cant|cantidad|retornar|retorno)\s*[:x-]?\s*-?\d+[^)]*\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"[-–—]\s*\d+\s*(?:x|ea|pc|units?|unidades?|pcs)?\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b[xX]\s*\d+\s*$", "", cleaned)
    cleaned = re.sub(r"\b\d+\s*[xX]\s*$", "", cleaned)
    cleaned = re.sub(r"\b\d+\s*(?:pcs|pc|ea|units?|unidades?)\b", "", cleaned, flags=re.I)

    # 6. Eliminar totales, subtotales, impuestos y balance
    cleaned = re.sub(r"\b(?:price|precio|total|subtotal|sub-total|amount|tax|balance\s*due)\b[^\n]*", "", cleaned, flags=re.I)

    # 7. Eliminar IDs, SKUs, UPCs, códigos de barras y referencias numéricas
    cleaned = re.sub(r"\b(?:ID|SKU|UPC|REF|ITEM|CODE|COD|ART)[-:\s#]*[A-Za-z0-9_-]*\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"#\s*\d+\b", "", cleaned)
    cleaned = re.sub(r"\b\d{6,}\b", "", cleaned)

    # 8. Eliminar cualquier otro paréntesis residual vacío o con números
    cleaned = re.sub(r"\(\s*\)", "", cleaned)

    # 9. Limpiar símbolos y signos al inicio y al final (preservando puntos o símbolos internos como 'HYDRA . SILK')
    cleaned = re.sub(r"^[+-/*•±\\–—\s\"'`.:;|,]+", "", cleaned)
    cleaned = re.sub(r"[+-/*•±\\–—\s\"'`.:;|,]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 10. Descarte final si coincide con palabras inválidas, es demasiado corta o no tiene letras
    if len(cleaned) < 3 or cleaned.lower() in PALABRAS_INVALIDAS_DEVOLUCION or not re.search(r"[a-zA-Z]", cleaned):
        return ""

    # 11. Descarte si lo que queda activa el regex de exclusión
    if REGEX_EXCLUDE_DISCARD_LINE.search(cleaned):
        return ""

    return f"{detected_qty} {cleaned}"


def extraer_productos_negativos_o_devolucion_de_texto(texto: str, es_documento_retorno: bool = False) -> list[str]:
    """
    Extrae candidatos de productos devueltos a partir de texto OCR o campos de texto:
    1. Líneas con signo negativo explícito (- [Producto], -X [Producto], [Producto] -)
    2. Líneas con cantidad o importe negativo ((-1) [Producto], [Producto] (Cantidad -1), -1x [Producto])
    3. Líneas con palabras clave en contexto (RETURNED: [Producto], o bloques de 'Productos devueltos:')
    4. En documentos clasificados como DEVOLUCION_RETURN (_R.jpg), líneas de producto listadas con viñetas/guiones.
    """
    if not texto:
        return []

    lines = [line.strip() for line in str(texto).splitlines() if line.strip()]
    candidatos = []
    
    # Modo bloque: si detectamos cabecera de sección de retorno
    en_bloque_retorno = False
    
    # Regex para detectar líneas con signo negativo o cantidad negativa
    patron_negativo = re.compile(
        r"(?:^|[\s(])[-–—]\s*(?:\$?\d+|\d+\s*(?:x|ea|pc)?|\b[A-Za-z])|"  # - Producto, -1 Producto, -$50
        r"\(\s*-\s*\$?\d+[^)]*\)|"                                        # (-1), (-$50)
        r"\(\s*(?:qty|cant|cantidad|retornar)?\s*[-–—]\s*\d+[^)]*\)|"   # (Cantidad -1)
        r"\b(?:qty|cant|cantidad)\s*[:x-]?\s*-\s*\d+\b|"                 # Cantidad: -1
        r"[-–—]\s*$",                                                    # Producto -
        re.I
    )
    
    # Regex para prefijos de retorno en la línea
    patron_prefijo_retorno = re.compile(
        r"^\s*(?:returned\s+items?|refunded\s+items?|return\s+items?|productos?\s+devueltos?|items?\s+retornados?|returned|refunded|return|refund|devoluci[oó]n|retorno)\s*[:–—-]\s*(.+)",
        re.I
    )
    
    # Regex para cabeceras de bloque de devolución
    patron_cabecera_bloque = re.compile(
        r"^(?:returned\s+items?|refunded\s+items?|return\s+items?|productos?\s+devueltos?|items?\s+retornados?|returns?|refunds?|devoluci[oó]n|retorno)\s*:\s*$",
        re.I
    )
    
    # Regex para fin de bloque de items (totales, metadata)
    patron_fin_bloque = re.compile(
        r"^(?:total|subtotal|sub-total|tax|balance\s*due|amount|payment|visa|mastercard|signature|date|thank\s*you)\b",
        re.I
    )

    for line in lines:
        # 1. Verificar si entramos en una cabecera de bloque (ej. "PRODUCTOS DEVUELTOS:", "RETURNED ITEMS:")
        if patron_cabecera_bloque.match(line):
            en_bloque_retorno = True
            continue
            
        if en_bloque_retorno:
            if patron_fin_bloque.match(line):
                en_bloque_retorno = False
            else:
                san = sanitizar_producto_devolucion(line)
                if san:
                    candidatos.append(san)
                continue

        # 2. Prefijo directo de retorno en la línea (ej. RETURNED: ATOMIC HEATING MASK)
        match_prefijo = patron_prefijo_retorno.match(line)
        if match_prefijo:
            prod_part = match_prefijo.group(1)
            san = sanitizar_producto_devolucion(prod_part)
            if san:
                candidatos.append(san)
            continue

        # 3. Signo negativo explícito o cantidad/importe negativo (ej. - HYDRA . SILK, (-1) ACTIVING ELIXIR, etc.)
        if patron_negativo.search(line):
            san = sanitizar_producto_devolucion(line)
            if san:
                candidatos.append(san)
            continue

        # 4. En documentos clasificados como DEVOLUCION_RETURN (_R.jpg / RETURN.jpg)
        # Si la línea tiene un bullet/viñeta o guión explícito
        if es_documento_retorno and re.match(r"^[\s*•\-–—]\s*[A-Za-z]", line):
            san = sanitizar_producto_devolucion(line)
            if san:
                candidatos.append(san)

    # Deduplicar preservando orden
    return list(dict.fromkeys(candidatos))


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
    is_return_doc = bool(REGEX_RETURN.search(fname)) or fname.lower().endswith("_r.jpg") or fname.lower().endswith("-r.jpg") or bool(REGEX_N_SHIPPING.search(fname))

    if REGEX_EXCLUDED.search(fname):
        categoria = "EXCLUIDO_EMPRESA_DEUDOR (GBK/5CC/50C/CID/V/E.TRANSFER)"
    elif is_return_doc:
        categoria = "DEVOLUCION_RETURN (_R / RETURN)"
    elif REGEX_VIP.search(fname) or "vip" in fname.lower():
        categoria = "VIP_FORM (VIP)"
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


def formatear_sharefile_folder_url(folder_id_or_url: str) -> str:
    """
    Formatea cualquier ID o URL de carpeta de ShareFile al enlace web legacy funcional:
    https://morenamiabeautygroup.sharefile.com/#/folders/fo<folder_id>
    """
    if not folder_id_or_url:
        return ""
    raw = str(folder_id_or_url).strip()
    match = re.search(r"sharefile\.com/(?:fo/|#/folders/|home/shared/)(?:fo)?([a-zA-Z0-9\-]+)", raw, re.IGNORECASE)
    if match:
        clean_id = match.group(1)
    else:
        clean_id = raw

    # Asegurar el prefijo 'fo'
    if not clean_id.lower().startswith("fo"):
        clean_id = f"fo{clean_id}"

    return f"https://morenamiabeautygroup.sharefile.com/#/folders/{clean_id}"



# ==============================================================================
# MOTOR UNIVERSAL DIRECTO DE GOOGLE SHEETS Y GOOGLE DRIVE (A:AZ + MULTI-HOJA)
# ==============================================================================
import time
import base64

CUSTOMER_INFO_SPREADSHEET_ID = os.environ.get(
    "GOOGLE_SPREADSHEET_ID", "1-D0F025w23P9daLlLsfcGAbi9o_pnM1A_p70WtlZdK4"
)
MASTER_SHIPPINGS_SPREADSHEET_ID = os.environ.get(
    "MASTER_SHIPPINGS_SPREADSHEET_ID", "1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY"
)
UP_SALES_SPREADSHEET_ID = os.environ.get(
    "UP_SALES_SPREADSHEET_ID", "1HgzpKN0pwQqexkUqqjWhPsCaBC9VfvX-Dj8PqhOhA2s"
)
GCP_PROJECT_NUMBER = os.environ.get("GCP_PROJECT_NUMBER", "720693669884")

_DIRECT_SA_CREDS = None
_DIRECT_META_CACHE: dict[str, tuple[float, dict]] = {}
_DIRECT_VALUES_CACHE: dict[str, tuple[float, list]] = {}
_DIRECT_HEADER_CACHE: dict[str, list[str]] = {}


def _index_to_col_letter(idx: int) -> str:
    """Convierte índice 0-based (0 -> A, 25 -> Z, 26 -> AA, 39 -> AN) a letra de columna de Excel/Sheets."""
    letter = ""
    temp = idx
    while temp >= 0:
        letter = chr((temp % 26) + 65) + letter
        temp = (temp // 26) - 1
    return letter


def _col_letter_to_index(col_str: str) -> int:
    """Convierte letra de columna (A -> 0, Z -> 25, AA -> 26, AN -> 39) a índice 0-based."""
    s = str(col_str or "").strip().upper()
    result = 0
    for ch in s:
        if "A" <= ch <= "Z":
            result = result * 26 + (ord(ch) - 64)
    return result - 1


def _get_direct_google_token() -> str | None:
    """
    Obtiene un token OAuth2 para Google Sheets y Google Drive usando:
    1. Variable de entorno GOOGLE_SERVICE_ACCOUNT_JSON
    2. Archivo local service-account.json (../service-account.json o ./service-account.json)
    3. GCP Secret Manager (projects/720693669884/secrets/GOOGLE_SERVICE_ACCOUNT_JSON/versions/latest:access)
    4. Credenciales por defecto de Cloud Run (ADC) si tienen scopes suficientes
    """
    global _DIRECT_SA_CREDS
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    try:
        from google.oauth2 import service_account
        import google.auth
        import google.auth.transport.requests

        if _DIRECT_SA_CREDS is not None:
            if not _DIRECT_SA_CREDS.valid or _DIRECT_SA_CREDS.expired:
                _DIRECT_SA_CREDS.refresh(google.auth.transport.requests.Request())
            return _DIRECT_SA_CREDS.token

        sa_info = None
        env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if env_json:
            try:
                sa_info = json.loads(env_json)
            except Exception:
                pass

        if not sa_info:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            candidate_paths = [
                os.path.join(base_dir, "service-account.json"),
                os.path.join(os.path.dirname(base_dir), "service-account.json"),
                os.path.abspath("service-account.json"),
            ]
            for p in candidate_paths:
                if os.path.isfile(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            sa_info = json.load(f)
                        break
                    except Exception:
                        pass

        if not sa_info:
            # Intentar leer GOOGLE_SERVICE_ACCOUNT_JSON desde GCP Secret Manager usando ADC de Cloud Run
            try:
                adc_creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                adc_creds.refresh(google.auth.transport.requests.Request())
                sm_url = f"https://secretmanager.googleapis.com/v1/projects/{GCP_PROJECT_NUMBER}/secrets/GOOGLE_SERVICE_ACCOUNT_JSON/versions/latest:access"
                sm_resp = requests.get(
                    sm_url,
                    headers={"Authorization": f"Bearer {adc_creds.token}"},
                    timeout=10,
                )
                if sm_resp.status_code == 200:
                    b64_payload = sm_resp.json().get("payload", {}).get("data", "")
                    if b64_payload:
                        raw_secret = base64.b64decode(b64_payload).decode("utf-8")
                        sa_info = json.loads(raw_secret)
            except Exception:
                pass

        if sa_info:
            _DIRECT_SA_CREDS = service_account.Credentials.from_service_account_info(
                sa_info, scopes=scopes
            )
            _DIRECT_SA_CREDS.refresh(google.auth.transport.requests.Request())
            return _DIRECT_SA_CREDS.token

        # Fallback a ADC directo
        adc_creds, _ = google.auth.default(scopes=scopes)
        adc_creds.refresh(google.auth.transport.requests.Request())
        _DIRECT_SA_CREDS = adc_creds
        return _DIRECT_SA_CREDS.token
    except Exception:
        return None


def _parse_spreadsheet_input(
    raw_input: str = "", explicit_gid: str = ""
) -> dict:
    """
    Extrae spreadsheetId, gid y alias desde una URL completa de Google Sheets, un ID o un nombre corto.
    Ejemplo: https://docs.google.com/spreadsheets/d/1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY/edit?gid=1576484704#gid=1576484704
    """
    s = str(raw_input or "").strip()
    gid = str(explicit_gid or "").strip() or None
    if not s:
        return {"spreadsheetId": None, "gid": gid, "alias": "ambas"}

    m_url = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    m_gid = re.search(r"[?#&]gid=(\d+)", s)
    if m_gid and not gid:
        gid = m_gid.group(1)

    if m_url:
        sp_id = m_url.group(1)
        alias = "custom"
        if sp_id == MASTER_SHIPPINGS_SPREADSHEET_ID:
            alias = "master_shippings"
        elif sp_id == CUSTOMER_INFO_SPREADSHEET_ID:
            alias = "customer_information"
        elif sp_id == UP_SALES_SPREADSHEET_ID:
            alias = "up_sales"
        return {"spreadsheetId": sp_id, "gid": gid, "alias": alias}

    low = s.lower()
    if low in ("master", "master_shippings", "master shippings"):
        return {
            "spreadsheetId": MASTER_SHIPPINGS_SPREADSHEET_ID,
            "gid": gid,
            "alias": "master_shippings",
        }
    if low in (
        "customer",
        "customer_information",
        "customer_info",
        "customer information",
        "customer information - shippings",
    ):
        return {
            "spreadsheetId": CUSTOMER_INFO_SPREADSHEET_ID,
            "gid": gid,
            "alias": "customer_information",
        }
    if low in ("upsales", "up_sales", "up sales", "up sales report", "daily report"):
        return {
            "spreadsheetId": UP_SALES_SPREADSHEET_ID,
            "gid": gid,
            "alias": "up_sales",
        }
    if low in ("ambas", "both", "todas", "all"):
        return {"spreadsheetId": None, "gid": gid, "alias": low}

    if re.match(r"^[a-zA-Z0-9-_]{25,60}$", s):
        alias = "custom"
        if s == MASTER_SHIPPINGS_SPREADSHEET_ID:
            alias = "master_shippings"
        elif s == CUSTOMER_INFO_SPREADSHEET_ID:
            alias = "customer_information"
        elif s == UP_SALES_SPREADSHEET_ID:
            alias = "up_sales"
        return {"spreadsheetId": s, "gid": gid, "alias": alias}

    return {"spreadsheetId": None, "gid": gid, "alias": low}


def _direct_get_spreadsheet_meta(spreadsheet_id: str) -> dict | None:
    """Obtiene el título y las pestañas (title, sheetId/gid, rowCount, columnCount) de un Spreadsheet."""
    now = time.time()
    cached = _DIRECT_META_CACHE.get(spreadsheet_id)
    if cached and (now - cached[0]) < 300:
        return cached[1]

    token = _get_direct_google_token()
    if not token:
        return None
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?fields=properties.title,sheets.properties"
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        meta = {
            "spreadsheetId": spreadsheet_id,
            "title": data.get("properties", {}).get("title", spreadsheet_id),
            "sheets": [
                {
                    "title": sh.get("properties", {}).get("title", ""),
                    "gid": sh.get("properties", {}).get("sheetId", 0),
                    "rowCount": sh.get("properties", {})
                    .get("gridProperties", {})
                    .get("rowCount", 0),
                    "columnCount": sh.get("properties", {})
                    .get("gridProperties", {})
                    .get("columnCount", 0),
                }
                for sh in data.get("sheets", [])
            ],
        }
        _DIRECT_META_CACHE[spreadsheet_id] = (now, meta)
        return meta
    except Exception:
        return None


def _direct_get_sheet_values(
    spreadsheet_id: str, range_str: str, ttl_seconds: int = 45
) -> list[list[str]] | None:
    """Lee un rango de valores (ej. '2026!A:AZ') desde Google Sheets API v4 con caché ligero."""
    cache_key = f"{spreadsheet_id}:{range_str}"
    now = time.time()
    cached = _DIRECT_VALUES_CACHE.get(cache_key)
    if cached and (now - cached[0]) < ttl_seconds:
        return cached[1]

    token = _get_direct_google_token()
    if not token:
        return None
    encoded_range = urllib.parse.quote(range_str)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}"
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=20
        )
        if resp.status_code != 200:
            return None
        rows = resp.json().get("values", [])
        _DIRECT_VALUES_CACHE[cache_key] = (now, rows)
        return rows
    except Exception:
        return None


def _direct_get_sheet_headers(spreadsheet_id: str, sheet_name: str) -> list[str]:
    """Obtiene los encabezados reales de una pestaña (detectando automáticamente si la Fila 1 es un banner como en UP SALES REPORT y los encabezados están en la Fila 2)."""
    cache_key = f"{spreadsheet_id}:{sheet_name}"
    if cache_key in _DIRECT_HEADER_CACHE:
        return _DIRECT_HEADER_CACHE[cache_key]

    rows = _direct_get_sheet_values(spreadsheet_id, f"'{sheet_name}'!A1:AZ2", ttl_seconds=300)
    if not rows:
        return []
    row1 = [str(c or "").strip() for c in (rows[0] if len(rows) > 0 else [])]
    row2 = [str(c or "").strip() for c in (rows[1] if len(rows) > 1 else [])]
    non_empty_1 = len([c for c in row1 if c])
    non_empty_2 = len([c for c in row2 if c])
    chosen = row2 if (non_empty_1 <= 2 and non_empty_2 >= 3) else row1
    _DIRECT_HEADER_CACHE[cache_key] = chosen
    return chosen


def _direct_build_row_cell_breakdown(
    sheet_type: str,
    sheet_tab: str,
    row_number: int,
    row_values: list,
    raw_headers: list[str] | None = None,
    requested_col_or_cell: str = "",
) -> dict:
    """
    Construye el desglose celda por celda desde la Columna A hasta AZ (incluyendo los 4 bloques de proveedores
    de Tracking en MASTER SHIPPINGS: ZERO GRAVITY, Empire Tech, STEMTOX / INTERNAL y SPACETOUCH).
    """
    master_fallback_headers = [
        "SALE DATE", "Email", "Customer Full Name", "Phone #", "NOVA INVOICE",
        "Address 1 (Street)", "City", "State", "Zip Code", "Country",
        "Location - Store", "Sales rep", "Products", "Notes", "TRACKING Received/Email sent",
        "Handled (ZERO GRAVITY)", "Shipped (ZERO GRAVITY)", "Tracking # (ZERO GRAVITY)", "Arrived (ZERO GRAVITY)", "Invoice (ZERO GRAVITY)", "ZERO GRAVITY (Productos)",
        "Handled (Empire Tech)", "Shipped (Empire Tech)", "Tracking # (Empire Tech)", "Arrived (Empire Tech)", "Invoice (Empire Tech)", "Empire Tech (Productos)",
        "Handled (STEMTOX / INTERNAL)", "Shipped (STEMTOX / INTERNAL)", "Tracking # (STEMTOX / INTERNAL)", "Arrived (STEMTOX / INTERNAL)", "Invoice (STEMTOX / INTERNAL)", "STEMTOX / INTERNAL (Productos)",
        "Handled (SPACETOUCH)", "Shipped (SPACETOUCH)", "Tracking # (SPACETOUCH)", "Arrived (SPACETOUCH)", "Invoice (SPACETOUCH)", "SPACETOUCH (Productos)",
        "ESTADO",
    ]
    customer_fallback_headers = [
        "DATE", "INVOICE", "TOTAL", "STORE NAME", "NAME", "LAST NAME",
        "TELEPHONE", "EMAIL", "STATE", "ZIP CODE", "PRODUCTS", "INSTRUCTIONS",
        "SHIPPING", "SHIPPING DATE", "NOTES", "CS CONTACT",
    ]
    upsales_fallback_headers = [
        "INVOICE", "DATE", "TOTAL", "CUSTOMER NAME", "LOCATION", "CITY",
        "SHAREFILE LINK", " NEW VALID EMAIL", "SENT TO VIP", "WRONG EMAIL OR SENT FAILED",
        "EMAIL SENT BY:", " DATE SENT", "OPENED EMAIL", "STATUS", "Signature",
        "Notes", "Sales Reps", "EMAIL",
    ]

    if sheet_type == "master_shippings":
        fallback_headers = master_fallback_headers
        spreadsheet_title = "MASTER SHIPPINGS (1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY)"
    elif sheet_type == "customer_information":
        fallback_headers = customer_fallback_headers
        spreadsheet_title = "Customer Information - Shippings (1-D0F025w23P9daLlLsfcGAbi9o_pnM1A_p70WtlZdK4)"
    elif sheet_type == "up_sales":
        fallback_headers = upsales_fallback_headers
        spreadsheet_title = "UP SALES REPORT (1HgzpKN0pwQqexkUqqjWhPsCaBC9VfvX-Dj8PqhOhA2s)"
    else:
        fallback_headers = raw_headers or []
        spreadsheet_title = sheet_type

    max_len = max(len(row_values or []), len(raw_headers or []), len(fallback_headers))
    celdas_por_columna = {}
    todas_las_celdas = []
    datos_contacto = {}
    direccion_cliente = {}

    for idx in range(max_len):
        col_letter = _index_to_col_letter(idx)
        coord = f"{col_letter}{row_number}"
        raw_h = (
            str(raw_headers[idx]).strip()
            if (raw_headers and idx < len(raw_headers) and str(raw_headers[idx]).strip())
            else ""
        )
        header_name = raw_h or (fallback_headers[idx] if idx < len(fallback_headers) else f"Columna {col_letter}")

        if sheet_type == "master_shippings":
            if 15 <= idx <= 19:
                header_name = f"{header_name} [Bloque 1: ZERO GRAVITY]"
            elif idx == 20:
                header_name = "ZERO GRAVITY (Productos Bloque 1)"
            elif 21 <= idx <= 25:
                header_name = f"{header_name} [Bloque 2: Empire Tech]"
            elif idx == 26:
                header_name = "Empire Tech (Productos Bloque 2)"
            elif 27 <= idx <= 31:
                header_name = f"{header_name} [Bloque 3: STEMTOX / INTERNAL]"
            elif idx == 32:
                header_name = "STEMTOX / INTERNAL (Productos Bloque 3)"
            elif 33 <= idx <= 37:
                header_name = f"{header_name} [Bloque 4: SPACETOUCH]"
            elif idx == 38:
                header_name = "SPACETOUCH (Productos Bloque 4)"

        val = str(row_values[idx]).strip() if (row_values and idx < len(row_values) and row_values[idx] is not None) else ""
        categoria_semantica = "OTROS_DATOS_FILA"
        subcampo = header_name

        if sheet_type == "customer_information":
            if col_letter in ("E", "F", "G", "H"):
                categoria_semantica = "DATOS_DE_CONTACTO_DEL_CLIENTE"
                if col_letter == "E":
                    subcampo = "Nombre del cliente (NAME)"
                    datos_contacto["nombre"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "F":
                    subcampo = "Apellido del cliente (LAST NAME)"
                    datos_contacto["apellido"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "G":
                    subcampo = "Teléfono del cliente (TELEPHONE)"
                    datos_contacto["telefono"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "H":
                    subcampo = "Correo electrónico del cliente (EMAIL)"
                    datos_contacto["email"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif col_letter in ("I", "J"):
                categoria_semantica = "DIRECCION_DEL_CLIENTE"
                if col_letter == "I":
                    subcampo = "Estado / Región (STATE)"
                    direccion_cliente["estado_state"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "J":
                    subcampo = "Código Postal (ZIP CODE)"
                    direccion_cliente["codigo_postal_zip"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif col_letter in ("A", "B", "C", "D"):
                categoria_semantica = "DATOS_DE_LA_VENTA_E_INVOICE"
            elif col_letter in ("K", "L", "M", "N", "O", "P"):
                categoria_semantica = "PRODUCTOS_Y_ENVIO"
        elif sheet_type == "master_shippings":
            if col_letter in ("B", "C", "D"):
                categoria_semantica = "DATOS_DE_CONTACTO_DEL_CLIENTE"
                if col_letter == "B":
                    subcampo = "Correo electrónico del cliente (Email)"
                    datos_contacto["email"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "C":
                    subcampo = "Nombre completo del cliente (Customer Full Name)"
                    datos_contacto["nombre_completo"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "D":
                    subcampo = "Teléfono del cliente (Phone #)"
                    datos_contacto["telefono"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif col_letter in ("F", "G", "H", "I", "J"):
                categoria_semantica = "DIRECCION_DEL_CLIENTE"
                if col_letter == "F":
                    subcampo = "Calle y número (Address 1 / Street)"
                    direccion_cliente["calle_address_1"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "G":
                    subcampo = "Ciudad (City)"
                    direccion_cliente["ciudad_city"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "H":
                    subcampo = "Estado / Provincia (State)"
                    direccion_cliente["estado_state"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "I":
                    subcampo = "Código Postal (Zip Code)"
                    direccion_cliente["codigo_postal_zip"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif col_letter == "J":
                    subcampo = "País (Country)"
                    direccion_cliente["pais_country"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif col_letter in ("A", "E", "K", "L"):
                categoria_semantica = "DATOS_DE_LA_VENTA_E_INVOICE"
            elif col_letter in ("M", "N"):
                categoria_semantica = "PRODUCTOS_Y_MOTIVO_ENVIO"
            elif idx >= 14:
                categoria_semantica = "SEGUIMIENTO_Y_LOGISTICA_TRACKING"
        else:
            h_low = header_name.lower()
            if any(w in h_low for w in ("email", "mail", "phone", "tel", "customer name", "full name", "client", "nombre")):
                categoria_semantica = "DATOS_DE_CONTACTO_DEL_CLIENTE"
                if "email" in h_low or "mail" in h_low:
                    datos_contacto[f"email_{col_letter}"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                elif "phone" in h_low or "tel" in h_low:
                    datos_contacto[f"telefono_{col_letter}"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
                else:
                    datos_contacto[f"nombre_{col_letter}"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif any(w in h_low for w in ("address", "city", "state", "zip", "country", "calle", "ciudad")):
                categoria_semantica = "DIRECCION_DEL_CLIENTE"
                direccion_cliente[f"direccion_{col_letter}"] = {"celda": coord, "columna": col_letter, "encabezado": header_name, "valor": val}
            elif any(w in h_low for w in ("invoice", "date", "total", "store", "location", "sales rep")):
                categoria_semantica = "DATOS_DE_LA_VENTA_E_INVOICE"
            elif any(w in h_low for w in ("tracking", "shipped", "arrived", "handled", "status", "estado")):
                categoria_semantica = "SEGUIMIENTO_Y_LOGISTICA_TRACKING"

        cell_info = {
            "celda": coord,
            "columna": col_letter,
            "fila": row_number,
            "encabezado": header_name,
            "valor": val,
            "categoriaSemantica": categoria_semantica,
            "Significado": subcampo,
        }
        celdas_por_columna[col_letter] = cell_info
        todas_las_celdas.append(cell_info)

    seguimiento_logistica_tracking = None
    if sheet_type == "master_shippings":
        def _gv(i: int) -> str:
            return str(row_values[i]).strip() if (row_values and i < len(row_values) and row_values[i] is not None) else ""

        vendor_blocks = [
            {
                "proveedor": "ZERO GRAVITY",
                "rangoColumnas": f"P{row_number}:U{row_number}",
                "handled": {"celda": f"P{row_number}", "valor": _gv(15)},
                "shipped": {"celda": f"Q{row_number}", "valor": _gv(16)},
                "trackingNumber": {"celda": f"R{row_number}", "valor": _gv(17)},
                "arrived": {"celda": f"S{row_number}", "valor": _gv(18)},
                "vendorInvoice": {"celda": f"T{row_number}", "valor": _gv(19)},
                "productos": {"celda": f"U{row_number}", "valor": _gv(20)},
            },
            {
                "proveedor": "Empire Tech",
                "rangoColumnas": f"V{row_number}:AA{row_number}",
                "handled": {"celda": f"V{row_number}", "valor": _gv(21)},
                "shipped": {"celda": f"W{row_number}", "valor": _gv(22)},
                "trackingNumber": {"celda": f"X{row_number}", "valor": _gv(23)},
                "arrived": {"celda": f"Y{row_number}", "valor": _gv(24)},
                "vendorInvoice": {"celda": f"Z{row_number}", "valor": _gv(25)},
                "productos": {"celda": f"AA{row_number}", "valor": _gv(26)},
            },
            {
                "proveedor": "STEMTOX / INTERNAL",
                "rangoColumnas": f"AB{row_number}:AG{row_number}",
                "handled": {"celda": f"AB{row_number}", "valor": _gv(27)},
                "shipped": {"celda": f"AC{row_number}", "valor": _gv(28)},
                "trackingNumber": {"celda": f"AD{row_number}", "valor": _gv(29)},
                "arrived": {"celda": f"AE{row_number}", "valor": _gv(30)},
                "vendorInvoice": {"celda": f"AF{row_number}", "valor": _gv(31)},
                "productos": {"celda": f"AG{row_number}", "valor": _gv(32)},
            },
            {
                "proveedor": "SPACETOUCH",
                "rangoColumnas": f"AH{row_number}:AM{row_number}",
                "handled": {"celda": f"AH{row_number}", "valor": _gv(33)},
                "shipped": {"celda": f"AI{row_number}", "valor": _gv(34)},
                "trackingNumber": {"celda": f"AJ{row_number}", "valor": _gv(35)},
                "arrived": {"celda": f"AK{row_number}", "valor": _gv(36)},
                "vendorInvoice": {"celda": f"AL{row_number}", "valor": _gv(37)},
                "productos": {"celda": f"AM{row_number}", "valor": _gv(38)},
            },
        ]

        trackings_activos = []
        for vb in vendor_blocks:
            if (
                vb["trackingNumber"]["valor"]
                or vb["shipped"]["valor"]
                or vb["arrived"]["valor"]
                or vb["productos"]["valor"]
            ):
                trackings_activos.append(
                    {
                        "bloqueProveedor": vb["proveedor"],
                        "rango": vb["rangoColumnas"],
                        "celdaTracking": vb["trackingNumber"]["celda"],
                        "trackingNumber": vb["trackingNumber"]["valor"] or "(Pendiente de guía / sin tracking)",
                        "handled": vb["handled"]["valor"],
                        "shipped": f"{vb['shipped']['valor']} ({vb['shipped']['celda']})" if vb["shipped"]["valor"] else "",
                        "arrived": f"{vb['arrived']['valor']} ({vb['arrived']['celda']})" if vb["arrived"]["valor"] else "",
                        "invoiceProveedor": f"{vb['vendorInvoice']['valor']} ({vb['vendorInvoice']['celda']})" if vb["vendorInvoice"]["valor"] else "",
                        "productosProveedor": f"{vb['productos']['valor']} ({vb['productos']['celda']})" if vb["productos"]["valor"] else "",
                    }
                )

        seguimiento_logistica_tracking = {
            "trackingReceivedOrEmailSent": {"celda": f"O{row_number}", "valor": _gv(14)},
            "estadoGeneral": {"celda": f"AN{row_number}", "valor": _gv(39)},
            "tieneTrackingActivo": any(bool(vb["trackingNumber"]["valor"]) for vb in vendor_blocks),
            "trackingsActivos": trackings_activos,
            "bloquesProveedores": vendor_blocks,
        }

    celda_consultada = None
    if requested_col_or_cell:
        clean_req = str(requested_col_or_cell).strip().upper()
        m_col = re.match(r"^([A-Z]{1,2})(\d+)?$", clean_req)
        if m_col:
            col_l = m_col.group(1)
            if col_l in celdas_por_columna:
                celda_consultada = celdas_por_columna[col_l]

    if sheet_type == "customer_information":
        nota_dir = "En Customer Information - Shippings solo se registran Estado (Columna I) y Código Postal (Columna J). La dirección postal completa está en MASTER SHIPPINGS (Columnas F a J)."
    elif sheet_type == "master_shippings":
        nota_dir = "En MASTER SHIPPINGS la dirección postal completa está en las columnas F (Address 1 / Calle), G (City), H (State), I (Zip Code) y J (Country)."
    else:
        nota_dir = "Columnas de ubicación detectadas automáticamente por encabezado."

    return {
        "hoja": spreadsheet_title,
        "tipoHoja": sheet_type,
        "pestaña": sheet_tab,
        "fila": row_number,
        "celdaConsultada": celda_consultada,
        "datosContactoCliente": {
            "descripcion": "Datos de contacto e identidad directa del cliente (Nombre, Apellido, Teléfono y Correo Electrónico)",
            "campos": datos_contacto,
        },
        "direccionCliente": {
            "descripcion": "Dirección física / postal de envío del cliente (Calle/Street, Ciudad, Estado, Código Postal, País)",
            "nota": nota_dir,
            "campos": direccion_cliente,
        },
        "seguimientoLogisticaTracking": seguimiento_logistica_tracking,
        "celdasPorColumna": celdas_por_columna,
        "todasLasCeldasDeLaFila": todas_las_celdas,
    }


def _direct_find_all_invoice_rows(
    spreadsheet_id: str,
    sheet_name: str,
    clean_inv: str,
    preferred_col_idx: int | None = None,
) -> list[dict]:
    """
    Busca TODAS las filas en una pestaña donde aparezca el invoice (exacto o en celda multilínea como '224366\\n\\n224377').
    """
    rows = _direct_get_sheet_values(spreadsheet_id, f"'{sheet_name}'!A:AZ")
    if not rows:
        return []
    matches = []
    target_lower = clean_inv.strip().lower()
    inv_regex = re.compile(
        r"(?:^|[\s,;:/\n\r#-])" + re.escape(target_lower) + r"(?:$|[\s,;:/\n\r#-])",
        re.I,
    )

    for i, row in enumerate(rows):
        matched = False
        if preferred_col_idx is not None and preferred_col_idx < len(row):
            cell_val = str(row[preferred_col_idx] or "").strip().lower()
            if cell_val == target_lower or inv_regex.search(cell_val):
                matched = True
        if not matched:
            for c_idx in range(min(len(row), 12)):
                cell_val = str(row[c_idx] or "").strip().lower()
                if cell_val == target_lower or inv_regex.search(cell_val):
                    matched = True
                    break
        if matched:
            matches.append({"rowIndex": i + 1, "rowValues": row})
    return matches


def _direct_read_row_cells(
    invoice: str = "",
    fila: int = 0,
    columna_o_celda: str = "",
    hoja: str = "ambas",
    anio: str = "2026",
    spreadsheet_url_o_id: str = "",
    gid: str = "",
) -> dict:
    """
    Motor directo en Python para leer cualquier fila y sus celdas (A..AZ) por invoice, número de fila,
    coordenada de celda o URL/ID de Google Sheets + gid.
    """
    clean_inv = str(invoice or "").strip()
    req_row = int(fila) if (fila and int(fila) > 0) else 0
    cell_param = str(columna_o_celda or "").strip().upper()
    year_param = str(anio or "2026").strip()
    raw_sheet_input = str(spreadsheet_url_o_id or hoja or "ambas").strip()
    parsed = _parse_spreadsheet_input(raw_sheet_input, explicit_gid=gid)

    if not req_row and cell_param:
        m_cell = re.match(r"^([A-Z]{1,2})(\d+)$", cell_param)
        if m_cell:
            req_row = int(m_cell.group(2))

    if not clean_inv and req_row <= 0:
        return {
            "success": False,
            "error": "Debes proporcionar un número de 'invoice', un número de 'fila' o una coordenada de 'celda' (ej. AD3975).",
        }

    resultados = {}

    # Caso 1: Spreadsheet específico por URL o ID o alias único (ej. cuando el usuario pasa un link con #gid=...)
    if parsed["spreadsheetId"]:
        sp_id = parsed["spreadsheetId"]
        sheet_type = parsed["alias"] if parsed["alias"] != "custom" else sp_id
        meta = _direct_get_spreadsheet_meta(sp_id)
        all_tabs = [s["title"] for s in (meta.get("sheets", []) if meta else [])]
        if not all_tabs:
            all_tabs = ["2026", "2025", "2024", "2023"]

        gid_resolved_tab = None
        if parsed["gid"] and meta:
            for sh in meta.get("sheets", []):
                if str(sh.get("gid")) == str(parsed["gid"]):
                    gid_resolved_tab = sh.get("title")
                    break

        preferred_col = (
            4 if sp_id == MASTER_SHIPPINGS_SPREADSHEET_ID else (1 if sp_id == CUSTOMER_INFO_SPREADSHEET_ID else (0 if sp_id == UP_SALES_SPREADSHEET_ID else None))
        )

        if clean_inv:
            # Si pidió un gid específico, buscar primero ahí; si no está ahí, buscar en TODAS las pestañas del documento
            tabs_order = []
            if gid_resolved_tab:
                tabs_order.append(gid_resolved_tab)
            for t in all_tabs:
                if t not in tabs_order:
                    tabs_order.append(t)

            all_found_breakdowns = []
            for tab in tabs_order:
                matches = _direct_find_all_invoice_rows(sp_id, tab, clean_inv, preferred_col)
                if matches:
                    headers = _direct_get_sheet_headers(sp_id, tab)
                    for m in matches:
                        bd = _direct_build_row_cell_breakdown(
                            sheet_type, tab, m["rowIndex"], m["rowValues"], headers, cell_param
                        )
                        if gid_resolved_tab and gid_resolved_tab != tab:
                            bd["notaGid"] = (
                                f"El enlace proporcionado apuntaba al gid={parsed['gid']} (pestaña '{gid_resolved_tab}'), "
                                f"pero el invoice {clean_inv} fue localizado en la pestaña '{tab}' (fila {m['rowIndex']}) de este mismo documento."
                            )
                        all_found_breakdowns.append(bd)

            if all_found_breakdowns:
                key_name = (
                    "master_shippings"
                    if sp_id == MASTER_SHIPPINGS_SPREADSHEET_ID
                    else ("customer_information" if sp_id == CUSTOMER_INFO_SPREADSHEET_ID else ("up_sales" if sp_id == UP_SALES_SPREADSHEET_ID else "custom_sheet"))
                )
                resultados[key_name] = all_found_breakdowns[0]
                if len(all_found_breakdowns) > 1:
                    resultados[f"{key_name}_todas_las_filas"] = all_found_breakdowns
            else:
                resultados[" Busqueda"] = {
                    "encontrado": False,
                    "spreadsheetId": sp_id,
                    "titulo": meta.get("title") if meta else sp_id,
                    "pestañasRevisadas": tabs_order,
                    "gidSolicitado": parsed["gid"],
                    "pestañaGidSolicitado": gid_resolved_tab,
                    "mensaje": f"No se encontró el invoice {clean_inv} en ninguna pestaña ({', '.join(tabs_order)}) del documento.",
                }
        else:
            target_tab = gid_resolved_tab or (year_param if year_param in all_tabs else all_tabs[0])
            headers = _direct_get_sheet_headers(sp_id, target_tab)
            rows = _direct_get_sheet_values(sp_id, f"'{target_tab}'!A{req_row}:AZ{req_row}")
            row_vals = rows[0] if rows else []
            resultados["fila_leida"] = _direct_build_row_cell_breakdown(
                sheet_type, target_tab, req_row, row_vals, headers, cell_param
            )

        return {
            "success": True,
            "consulta": {
                "invoice": clean_inv or None,
                "filaSolicitada": req_row or None,
                "celdaOColumnaSolicitada": cell_param or None,
                "spreadsheetId": sp_id,
                "gid": parsed["gid"],
                "pestañaGid": gid_resolved_tab,
            },
            "resultados": resultados,
        }

    # Caso 2: Búsqueda multi-hoja ("ambas" o "todas")
    include_cust = parsed["alias"] in ("ambas", "both", "todas", "all", "customer_information")
    include_master = parsed["alias"] in ("ambas", "both", "todas", "all", "master_shippings")
    include_upsales = parsed["alias"] in ("ambas", "both", "todas", "all", "up_sales")

    if include_cust:
        if clean_inv:
            found_c = []
            for tab in ["2026", "2025", "2024", "2023"]:
                matches = _direct_find_all_invoice_rows(CUSTOMER_INFO_SPREADSHEET_ID, tab, clean_inv, 1)
                if matches:
                    headers = _direct_get_sheet_headers(CUSTOMER_INFO_SPREADSHEET_ID, tab)
                    for m in matches:
                        found_c.append(
                            _direct_build_row_cell_breakdown(
                                "customer_information", tab, m["rowIndex"], m["rowValues"], headers, cell_param
                            )
                        )
                    break
            if found_c:
                resultados["customer_information"] = found_c[0]
                if len(found_c) > 1:
                    resultados["customer_information_todas_las_filas"] = found_c
            else:
                resultados["customer_information"] = {
                    "encontrado": False,
                    "mensaje": f"No se encontró el invoice {clean_inv} en Customer Information - Shippings.",
                }
        else:
            headers = _direct_get_sheet_headers(CUSTOMER_INFO_SPREADSHEET_ID, year_param)
            rows = _direct_get_sheet_values(
                CUSTOMER_INFO_SPREADSHEET_ID, f"'{year_param}'!A{req_row}:AZ{req_row}"
            )
            row_vals = rows[0] if rows else []
            resultados["customer_information"] = _direct_build_row_cell_breakdown(
                "customer_information", year_param, req_row, row_vals, headers, cell_param
            )

    if include_master:
        if clean_inv:
            found_m = []
            for tab in ["2026", "2025", "2024", "2023"]:
                matches = _direct_find_all_invoice_rows(MASTER_SHIPPINGS_SPREADSHEET_ID, tab, clean_inv, 4)
                if matches:
                    headers = _direct_get_sheet_headers(MASTER_SHIPPINGS_SPREADSHEET_ID, tab)
                    for m in matches:
                        found_m.append(
                            _direct_build_row_cell_breakdown(
                                "master_shippings", tab, m["rowIndex"], m["rowValues"], headers, cell_param
                            )
                        )
            if found_m:
                resultados["master_shippings"] = found_m[0]
                resultados["master_shippings_todas_las_filas"] = found_m
            else:
                resultados["master_shippings"] = {
                    "encontrado": False,
                    "mensaje": f"No se encontró el invoice {clean_inv} en MASTER SHIPPINGS.",
                }
        else:
            headers = _direct_get_sheet_headers(MASTER_SHIPPINGS_SPREADSHEET_ID, year_param)
            rows = _direct_get_sheet_values(
                MASTER_SHIPPINGS_SPREADSHEET_ID, f"'{year_param}'!A{req_row}:AZ{req_row}"
            )
            row_vals = rows[0] if rows else []
            resultados["master_shippings"] = _direct_build_row_cell_breakdown(
                "master_shippings", year_param, req_row, row_vals, headers, cell_param
            )

    if include_upsales and clean_inv:
        found_u = []
        for tab in ["DAILY REPORT", "Animations"]:
            matches = _direct_find_all_invoice_rows(UP_SALES_SPREADSHEET_ID, tab, clean_inv, 0)
            if matches:
                headers = _direct_get_sheet_headers(UP_SALES_SPREADSHEET_ID, tab)
                for m in matches:
                    found_u.append(
                        _direct_build_row_cell_breakdown(
                            "up_sales", tab, m["rowIndex"], m["rowValues"], headers, cell_param
                        )
                    )
        if found_u:
            resultados["up_sales_report"] = found_u[0]
            if len(found_u) > 1:
                resultados["up_sales_report_todas_las_filas"] = found_u

    return {
        "success": True,
        "consulta": {
            "invoice": clean_inv or None,
            "filaSolicitada": req_row or None,
            "celdaOColumnaSolicitada": cell_param or None,
            "hojaSolicitada": raw_sheet_input,
            "anioPestaña": year_param,
        },
        "resultados": resultados,
    }


def explorar_catalogo_hojas_drive(spreadsheet_url_o_id: str = "") -> dict:
    """
    Descubre y lista TODAS las hojas de cálculo (Google Sheets) a las que Angy y la aplicación tienen acceso
    en Google Drive, o inspecciona a fondo las pestañas (nombres, GIDs, número de filas/columnas y encabezados A..AZ)
    de cualquier documento específico (pasando su link de Google Sheets o su Spreadsheet ID).
    Úsala cuando necesites saber qué hojas existen más allá de MASTER SHIPPINGS y Customer Information (ej. UP SALES REPORT),
    o cuando el usuario te comparta un enlace de Google Sheets con `#gid=...` para saber a qué pestaña corresponde.
    """
    raw_target = str(spreadsheet_url_o_id or "").strip()
    if raw_target:
        parsed = _parse_spreadsheet_input(raw_target)
        sp_id = parsed.get("spreadsheetId") or raw_target
        meta = _direct_get_spreadsheet_meta(sp_id)
        if not meta:
            return {"error": f"No se pudo inspeccionar el documento {sp_id}. Verifica que esté compartido con la cuenta de servicio."}
        for sh in meta.get("sheets", []):
            sh["encabezados"] = _direct_get_sheet_headers(sp_id, sh["title"])
        return {
            "success": True,
            "spreadsheet": meta,
            "gidDetectadoEnLink": parsed.get("gid"),
        }

    # Listar todos los archivos Google Sheets en Drive
    token = _get_direct_google_token()
    if not token:
        return {"error": "No se pudo obtener token OAuth2 para consultar Google Drive."}

    try:
        q = urllib.parse.quote("mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
        drive_url = f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name,webViewLink,modifiedTime)&pageSize=50"
        resp = requests.get(drive_url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        files = resp.json().get("files", []) if resp.status_code == 200 else []
        catalogo = []
        for f in files:
            meta = _direct_get_spreadsheet_meta(f["id"])
            catalogo.append(
                {
                    "spreadsheetId": f["id"],
                    "nombre": f.get("name"),
                    "url": f.get("webViewLink"),
                    "ultimaModificacion": f.get("modifiedTime"),
                    "pestañas": meta.get("sheets", []) if meta else [],
                }
            )
        return {
            "success": True,
            "totalHojasAccesibles": len(catalogo),
            "hojas": catalogo,
        }
    except Exception as e:
        return {"error": f"Error explorando catálogo de hojas en Google Drive: {str(e)}"}


def buscar_y_sanitizar_cliente(invoice: str) -> dict:
    """
    Busca los datos de un cliente en Google Sheets (en Customer Information - Shippings, MASTER SHIPPINGS
    y UP SALES REPORT en todas las pestañas 2023, 2024, 2025, 2026 y DAILY REPORT) por su número de invoice,
    lee TODAS las filas donde aparezca con sus coordenadas de celda (Columnas A..AZ + Número de fila),
    extrae TODOS los números de tracking de los 4 bloques de proveedores en MASTER SHIPPINGS:
      - Bloque 1 (P..U): ZERO GRAVITY -> Tracking # en Columna R
      - Bloque 2 (V..AA): Empire Tech -> Tracking # en Columna X
      - Bloque 3 (AB..AG): STEMTOX / INTERNAL -> Tracking # en Columna AD
      - Bloque 4 (AH..AM): SPACETOUCH -> Tracking # en Columna AJ
      - Columna AN: ESTADO
    Clasifica semánticamente 'Datos de contacto del cliente' vs 'Dirección del cliente' vs 'Seguimiento y Logística (Tracking)',
    y bloquea estrictamente los teléfonos y correos corporativos (ej. 8882494189).
    """
    clean_inv = str(invoice or "").strip()
    if not clean_inv:
        return {"error": "Debes proporcionar un número de invoice válido."}

    # Usamos el motor directo universal (A:AZ + multi-fila + multi-hoja) para garantizar 0 fallos de conexión y lectura completa A..AN
    direct_data = _direct_read_row_cells(invoice=clean_inv, hoja="todas")
    res_map = direct_data.get("resultados", {}) if isinstance(direct_data, dict) else {}

    cust_bd = res_map.get("customer_information")
    has_cust = isinstance(cust_bd, dict) and cust_bd.get("encontrado") is not False and bool(cust_bd.get("fila"))

    master_bds = res_map.get("master_shippings_todas_las_filas") or (
        [res_map["master_shippings"]]
        if (isinstance(res_map.get("master_shippings"), dict) and res_map["master_shippings"].get("encontrado") is not False and res_map["master_shippings"].get("fila"))
        else []
    )
    has_master = len(master_bds) > 0

    upsales_bd = res_map.get("up_sales_report")
    has_upsales = isinstance(upsales_bd, dict) and bool(upsales_bd.get("fila"))

    if not has_cust and not has_master and not has_upsales:
        return {
            "error": f"No se encontró registro en Google Sheets (Customer Information, MASTER SHIPPINGS ni UP SALES REPORT) para la factura {clean_inv}. Puedes usar 'escanear_y_rellenar_master_shippings' o 'obtener_textos_de_archivos' para buscarla directamente en ShareFile."
        }

    def _cell_val(bd: dict | None, col_letter: str) -> str:
        if not bd or not isinstance(bd, dict):
            return ""
        return str(bd.get("celdasPorColumna", {}).get(col_letter, {}).get("valor") or "").strip()

    first_master = master_bds[0] if has_master else None

    # Extraer campos combinando Customer Info, MASTER SHIPPINGS y UP SALES REPORT
    name_cust = _cell_val(cust_bd, "E")
    last_cust = _cell_val(cust_bd, "F")
    full_name_master = _cell_val(first_master, "C")
    full_name_upsales = _cell_val(upsales_bd, "D")
    full_name = (
        f"{name_cust} {last_cust}".strip()
        or full_name_master
        or full_name_upsales
    )
    if not name_cust and full_name:
        parts = full_name.split()
        name_cust = parts[0] if parts else ""
        last_cust = " ".join(parts[1:]) if len(parts) > 1 else ""

    raw_phone = _cell_val(cust_bd, "G") or _cell_val(first_master, "D")
    clean_phone = "".join(filter(str.isdigit, raw_phone))
    if len(clean_phone) > 10:
        clean_phone = clean_phone[-10:]

    if clean_phone in COMPANY_PHONES:
        phone_status = "BLOQUEADO (Es el número corporativo de la empresa)"
        display_phone = ""
    elif len(clean_phone) < 10:
        phone_status = "Inválido (menos de 10 dígitos)"
        display_phone = ""
    else:
        phone_status = "Válido"
        display_phone = clean_phone

    raw_email = (
        _cell_val(cust_bd, "H")
        or _cell_val(first_master, "B")
        or _cell_val(upsales_bd, "R")
        or _cell_val(upsales_bd, "H")
    ).strip().lower()
    is_corporate_email = any(
        word in raw_email
        for word in [
            "customerservice", "noreply", "info@", "support@", "hello@", "contact@", "admin@", "sales@", "cs"
        ]
    )
    if is_corporate_email:
        email_status = "BLOQUEADO (Correo corporativo de soporte)"
        display_email = ""
    else:
        email_status = "Válido" if raw_email else "Vacío"
        display_email = raw_email

    address_street = _cell_val(first_master, "F")
    city_val = _cell_val(first_master, "G") or _cell_val(upsales_bd, "F")
    state_val = _cell_val(first_master, "H") or _cell_val(cust_bd, "I")
    zip_val = _cell_val(first_master, "I") or _cell_val(cust_bd, "J")
    country_val = _cell_val(first_master, "J")
    full_address = ", ".join([p for p in [address_street, city_val, state_val, zip_val, country_val] if p])

    # Consolidar productos, notas y TODOS los números de tracking de TODAS las filas de MASTER SHIPPINGS
    master_rows_summary = []
    all_active_trackings = []
    master_products_list = []
    master_notes_list = []

    for mbd in master_bds:
        r_num = mbd.get("fila")
        tab_m = mbd.get("pestaña")
        prod_m = _cell_val(mbd, "M")
        note_m = _cell_val(mbd, "N")
        if prod_m:
            master_products_list.append(f"[Fila {r_num}] {prod_m}" if len(master_bds) > 1 else prod_m)
        if note_m:
            master_notes_list.append(f"[Fila {r_num}] {note_m}" if len(master_bds) > 1 else note_m)

        seg = mbd.get("seguimientoLogisticaTracking") or {}
        row_trackings = seg.get("trackingsActivos") or []
        for rt in row_trackings:
            entry = dict(rt)
            entry["fila"] = r_num
            entry["pestaña"] = tab_m
            all_active_trackings.append(entry)

        master_rows_summary.append(
            {
                "pestaña": tab_m,
                "fila": r_num,
                "productos_columna_M": {"celda": f"M{r_num}", "valor": prod_m},
                "notas_columna_N": {"celda": f"N{r_num}", "valor": note_m},
                "tracking_received_columna_O": {"celda": f"O{r_num}", "valor": _cell_val(mbd, "O")},
                "estado_columna_AN": {"celda": f"AN{r_num}", "valor": _cell_val(mbd, "AN")},
                "trackings_activos_en_fila": row_trackings,
            }
        )

    primary_tracking = ""
    primary_shipped = ""
    primary_arrived = ""
    for t_item in all_active_trackings:
        t_val = str(t_item.get("trackingNumber") or "")
        if t_val and not t_val.startswith("(Pendiente"):
            primary_tracking = f"{t_val} ({t_item.get('celdaTracking')} - {t_item.get('bloqueProveedor')})"
            primary_shipped = t_item.get("shipped") or ""
            primary_arrived = t_item.get("arrived") or ""
            break

    shipping_col_m_cust = _cell_val(cust_bd, "M")
    is_shipping_no = shipping_col_m_cust.upper() in ("NO", "N")

    return {
        "invoice": clean_inv,
        "year_tab": (cust_bd.get("pestaña") if has_cust else (first_master.get("pestaña") if has_master else "2026")),
        "fila_customer_information": cust_bd.get("fila") if has_cust else None,
        "fila_master_shippings": first_master.get("fila") if has_master else None,
        "filas_master_shippings": [m.get("fila") for m in master_bds],
        "pestaña_master_shippings": first_master.get("pestaña") if has_master else None,
        "fila_up_sales_report": upsales_bd.get("fila") if has_upsales else None,
        "sharefile_link_up_sales": formatear_sharefile_folder_url(_cell_val(upsales_bd, "G")) if (has_upsales and _cell_val(upsales_bd, "G")) else "",
        "nombre_completo": full_name,
        "nombre": name_cust,
        "apellido": last_cust,
        "tienda": _cell_val(cust_bd, "D") or _cell_val(first_master, "K") or _cell_val(upsales_bd, "E"),
        "fecha_venta": _cell_val(cust_bd, "A") or _cell_val(first_master, "A") or _cell_val(upsales_bd, "B"),
        "total_venta": _cell_val(cust_bd, "C") or _cell_val(upsales_bd, "C"),
        "vendedor_sales_rep": _cell_val(first_master, "L") or _cell_val(upsales_bd, "Q"),
        "direccion_address_1": address_street,
        "ciudad_city": city_val,
        "region_state": state_val,
        "codigo_postal_zip": zip_val,
        "pais_country": country_val,
        "direccion_completa": full_address,
        "productos": _cell_val(cust_bd, "K") or " | ".join(master_products_list),
        "telefono_original": raw_phone,
        "telefono_sanitizado": display_phone,
        "telefono_estado": phone_status,
        "email_original": raw_email,
        "email_sanitizado": display_email,
        "email_estado": email_status,
        "shipping": shipping_col_m_cust or ("YES" if has_master else ""),
        "notas": _cell_val(cust_bd, "O") or " | ".join(master_notes_list),
        "datos_de_contacto_del_cliente": {
            "descripcion": "Datos de contacto e identidad directa del cliente (Nombre, Apellido, Teléfono, Email)",
            "nombre_completo": full_name,
            "nombre": name_cust,
            "apellido": last_cust,
            "telefono_sanitizado": display_phone,
            "telefono_original": raw_phone,
            "telefono_estado": phone_status,
            "email_sanitizado": display_email,
            "email_original": raw_email,
            "email_estado": email_status,
            "ubicacion_celdas": {
                "customer_information": cust_bd.get("datosContactoCliente", {}).get("campos", {}) if has_cust else None,
                "master_shippings": first_master.get("datosContactoCliente", {}).get("campos", {}) if has_master else None,
            },
        },
        "direccion_del_cliente": {
            "descripcion": "Ubicación física / dirección postal de envío del cliente (Calle/Street, Ciudad, Estado, Código Postal, País)",
            "calle_address_1": address_street,
            "ciudad_city": city_val,
            "estado_state": state_val,
            "codigo_postal_zip": zip_val,
            "pais_country": country_val,
            "direccion_completa_formateada": full_address,
            "ubicacion_celdas": {
                "customer_information": cust_bd.get("direccionCliente", {}).get("campos", {}) if has_cust else None,
                "master_shippings": first_master.get("direccionCliente", {}).get("campos", {}) if has_master else None,
            },
        },
        "seguimiento_y_tracking_master_shippings": {
            "total_filas_en_master_shippings": len(master_bds),
            "tracking_principal_detectado": primary_tracking,
            "todos_los_trackings_y_bloques_activos": all_active_trackings,
            "resumen_por_fila_master": master_rows_summary,
        },
        "lectura_filas_y_celdas": {
            "customer_information": cust_bd if has_cust else None,
            "master_shippings": first_master if has_master else None,
            "master_shippings_todas_las_filas": master_bds,
            "up_sales_report": upsales_bd if has_upsales else None,
        },
        "found_in_master": has_master,
        "master_shippings_products": " | ".join(master_products_list),
        "master_shippings_notes": " | ".join(master_notes_list),
        "master_tracking": primary_tracking,
        "master_all_trackings": all_active_trackings,
        "master_shipped": primary_shipped,
        "master_arrived": primary_arrived,
        "is_shipping_no": is_shipping_no,
        "has_pending_products": bool(master_products_list),
        "pending_reason": "Productos registrados en Columna M (PRODUCTS) de MASTER SHIPPINGS" if master_products_list else "",
    }


def leer_fila_y_celdas_sheets(
    invoice: str = "",
    fila: int = 0,
    columna_o_celda: str = "",
    hoja: str = "todas",
    anio: str = "2026",
    spreadsheet_url_o_id: str = "",
    gid: str = "",
) -> dict:
    """
    Lee toda una fila completa (o todas las filas asociadas a un invoice) en Google Sheets desde la Columna A hasta AZ (40+ columnas),
    soportando tanto las hojas principales ('Customer Information - Shippings', 'MASTER SHIPPINGS', 'UP SALES REPORT')
    como CUALQUIER enlace/URL de Google Sheets o Spreadsheet ID + GID que comparta el usuario.

    Devuelve el desglose exacto celda por celda (Columna A..AN/AZ), indicando:
    1. A qué columna (Letra A..AZ y nombre del encabezado) y coordenada (ej. AD3975, F3975) corresponde cada valor.
    2. Qué celdas corresponden a 'DATOS DE CONTACTO DEL CLIENTE' (Nombre, Apellido, Teléfono, Email).
    3. Qué celdas corresponden a 'DIRECCIÓN DEL CLIENTE' (Address 1 / Calle, City, State, Zip Code, Country).
    4. En MASTER SHIPPINGS, desglosa explícitamente los 4 bloques de proveedores de Tracking:
       - Bloque 1 (P..U): ZERO GRAVITY -> Tracking # en Columna R
       - Bloque 2 (V..AA): Empire Tech -> Tracking # en Columna X
       - Bloque 3 (AB..AG): STEMTOX / INTERNAL -> Tracking # en Columna AD
       - Bloque 4 (AH..AM): SPACETOUCH -> Tracking # en Columna AJ
       - Columna AN: ESTADO

    Parámetros:
    - invoice: (Opcional) Número de factura (ej. '224377') para localizar todas sus filas y leer todas sus celdas A..AZ.
    - fila: (Opcional) Número de fila exacta en Google Sheets (ej. 3975) si el usuario pregunta por una fila específica.
    - columna_o_celda: (Opcional) Letra de columna (ej. 'AD', 'R', 'F') o coordenada de celda (ej. 'AD3975') para enfocar una celda específica.
    - hoja: 'todas' (por defecto), 'ambas', 'master_shippings', 'customer_information', 'up_sales', o una URL/ID de Google Sheets.
    - anio: Pestaña del año ('2026' por defecto, '2025', '2024', '2023', 'DAILY REPORT').
    - spreadsheet_url_o_id: (Opcional) URL completa de Google Sheets (ej. 'https://docs.google.com/spreadsheets/d/.../edit?gid=1576484704') o ID.
    - gid: (Opcional) GID numérico de la pestaña si el usuario lo especificó.
    """
    return _direct_read_row_cells(
        invoice=invoice,
        fila=fila,
        columna_o_celda=columna_o_celda,
        hoja=hoja,
        anio=anio,
        spreadsheet_url_o_id=spreadsheet_url_o_id,
        gid=gid,
    )


def consultar_envios_y_motivos(invoice: str) -> dict:
    """
    Consulta todas las filas ("Envíos") asociadas a un mismo número de invoice en Google Sheets
    (en Customer Information - Shippings, MASTER SHIPPINGS y UP SALES REPORT en todas las pestañas).
    Incluye todos los números de tracking de los 4 bloques de proveedores (Columnas R, X, AD, AJ) y el motivo de cada envío.
    """
    clean_inv = str(invoice or "").strip()
    if not clean_inv:
        return {"error": "Debe proporcionar un número de invoice válido."}

    direct_res = _direct_read_row_cells(invoice=clean_inv, hoja="todas")
    res_map = direct_res.get("resultados", {}) if isinstance(direct_res, dict) else {}
    envios = []

    cust_bds = res_map.get("customer_information_todas_las_filas") or (
        [res_map["customer_information"]]
        if (isinstance(res_map.get("customer_information"), dict) and res_map["customer_information"].get("fila"))
        else []
    )
    for cbd in cust_bds:
        c_map = cbd.get("celdasPorColumna", {})
        shipping_val = c_map.get("M", {}).get("valor", "")
        notes_val = c_map.get("O", {}).get("valor", "")
        prods_val = c_map.get("K", {}).get("valor", "")
        motivo = notes_val or (
            f"Venta registrada con requerimiento de envío (SHIPPING: {shipping_val})"
            if shipping_val.upper() in ("YES", "SI")
            else f"Registro de venta en tienda (SHIPPING: {shipping_val or 'NO'})"
        )
        envios.append(
            {
                "numero_envio": len(envios) + 1,
                "hoja": "Customer Information - Shippings",
                "pestaña": cbd.get("pestaña"),
                "fila": cbd.get("fila"),
                "invoice": c_map.get("B", {}).get("valor", clean_inv),
                "fecha": c_map.get("A", {}).get("valor", ""),
                "cliente": f"{c_map.get('E', {}).get('valor', '')} {c_map.get('F', {}).get('valor', '')}".strip(),
                "tienda": c_map.get("D", {}).get("valor", ""),
                "productos": prods_val,
                "shipping_columna_M": shipping_val,
                "notas": notes_val,
                "motivo": motivo,
            }
        )

    master_bds = res_map.get("master_shippings_todas_las_filas") or (
        [res_map["master_shippings"]]
        if (isinstance(res_map.get("master_shippings"), dict) and res_map["master_shippings"].get("fila"))
        else []
    )
    for mbd in master_bds:
        c_map = mbd.get("celdasPorColumna", {})
        prods_val = c_map.get("M", {}).get("valor", "")
        notes_val = c_map.get("N", {}).get("valor", "")
        seg = mbd.get("seguimientoLogisticaTracking") or {}
        trackings_activos = seg.get("trackingsActivos") or []
        motivo = notes_val or (
            f"Despacho de productos en MASTER SHIPPINGS ({prods_val})" if prods_val else "Registro en MASTER SHIPPINGS"
        )
        envios.append(
            {
                "numero_envio": len(envios) + 1,
                "hoja": "MASTER SHIPPINGS",
                "pestaña": mbd.get("pestaña"),
                "fila": mbd.get("fila"),
                "invoice": c_map.get("E", {}).get("valor", clean_inv),
                "fecha": c_map.get("A", {}).get("valor", ""),
                "cliente": c_map.get("C", {}).get("valor", ""),
                "tienda": c_map.get("K", {}).get("valor", ""),
                "vendedor": c_map.get("L", {}).get("valor", ""),
                "productos_columna_M": prods_val,
                "notas_columna_N": notes_val,
                "motivo": motivo,
                "estado_tracking_columna_O": c_map.get("O", {}).get("valor", ""),
                "estado_general_columna_AN": c_map.get("AN", {}).get("valor", ""),
                "trackings_por_proveedor": trackings_activos,
            }
        )

    return {
        "success": True,
        "invoice": clean_inv,
        "total_envios": len(envios),
        "envios": envios,
    }


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
        
        # 1. Si ya existen productos en la Columna M de MASTER SHIPPINGS, SOLO mencionar esos
        if master_prod:
            return {
                "invoice": clean_inv,
                "cliente": cliente_nombre,
                "columna_m_shipping": "SI",
                "tiene_productos_pendientes": True,
                "estado": "PENDIENTES_DE_ENVIO",
                "respuesta": f"Hay productos pendientes de envío registrados en MASTER SHIPPINGS: {master_prod}",
                "productos_pendientes": master_prod,
                "columna_m_master_shippings": master_prod,
                "nota": "Se toman exclusiva y directamente de la Columna M de MASTER SHIPPINGS (prohibido mencionar los de Customer Information)."
            }

        # 2. Si no están aún en MASTER SHIPPINGS, buscar si el invoice (o invoices del cliente) contiene archivo de devolución / negativo (_R.jpg / '-')
        invoices_a_revisar = [clean_inv]
        try:
            sesion = obtener_sesion_activa_frontend()
            if sesion.get("hay_sesion_activa"):
                for inv in (sesion.get("invoices_detectados") or []):
                    if str(inv).strip() and str(inv).strip() not in invoices_a_revisar:
                        invoices_a_revisar.append(str(inv).strip())
        except Exception:
            pass

        analisis_dev = analizar_multiples_invoices_devolucion(invoices_a_revisar)
        if analisis_dev.get("hay_devolucion") and analisis_dev.get("hay_productos_sanitizables"):
            prods_dev = analisis_dev.get("productos_devueltos_sanitizados") or []
            inv_dev = analisis_dev.get("invoice_con_devolucion") or clean_inv
            prods_dev_str = ", ".join(prods_dev)
            return {
                "invoice": inv_dev,
                "cliente": cliente_nombre,
                "columna_m_shipping": "SI",
                "tiene_productos_pendientes": True,
                "estado": "PRODUCTOS_DEVOLUCION_PENDIENTES",
                "respuesta": f"Hay productos de devolución/retorno con saldo negativo para enviar a MASTER SHIPPINGS en el invoice {inv_dev}: {prods_dev_str}",
                "productos_pendientes": prods_dev_str,
                "invoice_devolucion": inv_dev,
                "columna_m_master_shippings": "Pendiente de registro",
                "nota": "Se toman exclusivamente los productos en negativo/retorno del invoice de devolución (no los productos de Customer Information)."
            }

        # 3. Si no hay productos en MASTER SHIPPINGS ni archivo de retorno/negativo
        return {
            "invoice": clean_inv,
            "cliente": cliente_nombre,
            "columna_m_shipping": "SI",
            "tiene_productos_pendientes": False,
            "estado": "NO_HAY_PRODUCTOS_PENDIENTES",
            "respuesta": "No hay productos pendientes de envío para este invoice.",
            "productos_pendientes": "",
            "columna_m_master_shippings": "Vacía",
            "nota": "Para un invoice sin productos en negativo y con SHIPPING: SI, NO se considera pendiente de envío ni se mencionan productos de Customer Information."
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
        folder_id = data.get("folderParentId") or data.get("folderId")
        if folder_id:
            data["sharefile_folder_url"] = formatear_sharefile_folder_url(folder_id)
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

        folder_id = scan_data.get("folderParentId") or scan_data.get("folderId")
        folder_url = scan_data.get("sharefileFolderUrl") or (formatear_sharefile_folder_url(folder_id) if folder_id else "")

        return {
            "success": True,
            "invoice": invoice,
            "folder_parent_id": folder_id,
            "sharefile_folder_url": folder_url,
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
    - Si el invoice ya existe, se mantiene en su fila y simplemente se actualizan sus datos.
    - Si el invoice no existe en MASTER SHIPPINGS, se crea automáticamente una nueva fila antes de la celda de la Columna A con 'DEFECTIVE DEVICES TO ARRIVE SAME DAY, IN LINE TO BE PROCESSED AT ARRIVAL'.
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
    1. Si contiene archivo de devolución (_R.jpg, RETURN.jpg, etc.) o productos/saldos negativos en crudo (Raw OCR).
    2. Autodetecta facturas adicionales presentes en la misma carpeta ganadora (ej. si se dan 2 facturas pero la carpeta contiene 3+).
    3. Sanitiza los productos devueltos eliminando signos aritméticos (+, -, parentesis), precios e IDs/SKUs.
    4. Determina cuál es la factura de devolución que debe enviarse a MASTER SHIPPINGS y cuáles se omiten de MASTER SHIPPINGS (quedando solo en Customer Information).
    """
    if isinstance(invoices, str):
        invoices_list = [inv.strip() for inv in re.split(r"[\s,;\n]+", invoices) if inv.strip()]
    elif isinstance(invoices, list):
        invoices_list = [str(inv).strip() for inv in invoices if str(inv).strip()]
    else:
        return {"error": "Formato de invoices no válido. Proporciona una lista o string de números de invoice."}

    if not invoices_list:
        return {"error": "No se proporcionaron facturas para analizar."}

    # Revisar si en la sesión activa de frontend hay invoices detectados adicionales
    invoices_iniciales = list(dict.fromkeys(invoices_list))
    invoices_a_procesar = list(invoices_iniciales)
    try:
        sesion = obtener_sesion_activa_frontend()
        if sesion.get("hay_sesion_activa") or sesion.get("estado") == "exito":
            sp = sesion.get("sesion_pantalla", {})
            for inv_s in (sp.get("invoices_detectados") or []):
                inv_str = str(inv_s).strip()
                if inv_str and inv_str not in invoices_a_procesar:
                    invoices_a_procesar.append(inv_str)
    except Exception:
        pass

    resultados_invoices = []
    invoice_devolucion_seleccionada = None
    productos_devueltos_sanitizados = []
    invoices_descubiertos_en_carpeta = []
    archivos_carpeta_ganadora = []
    todos_los_archivos_carpeta = []
    carpeta_ganadora_folder_url = ""

    procesados = set()
    idx = 0
    while idx < len(invoices_a_procesar):
        inv = invoices_a_procesar[idx]
        idx += 1
        if inv in procesados:
            continue
        procesados.add(inv)

        scan_res = obtener_textos_de_archivos(inv)
        tiene_archivo_retorno = False
        archivos_detectados = []
        raw_negative_products = []

        if isinstance(scan_res, dict):
            if not carpeta_ganadora_folder_url:
                carpeta_ganadora_folder_url = scan_res.get("sharefile_folder_url") or formatear_sharefile_folder_url(scan_res.get("folderParentId"))
            # Autodescubrimiento de otros invoices en la misma carpeta desde respuesta de la API
            folder_invs = scan_res.get("allInvoicesInFolder") or []
            for f_inv in folder_invs:
                f_inv_str = str(f_inv).strip()
                if f_inv_str and f_inv_str not in invoices_a_procesar:
                    invoices_a_procesar.append(f_inv_str)
                    if f_inv_str not in invoices_descubiertos_en_carpeta and f_inv_str not in invoices_iniciales:
                        invoices_descubiertos_en_carpeta.append(f_inv_str)

            files = scan_res.get("files") or []
            for f in files:
                fname = f.get("fileName") or f.get("name") or ""
                ftext = f.get("text") or f.get("fullText") or ""
                
                # Autodescubrimiento de invoices en el nombre del archivo
                fname_inv_matches = re.findall(r"(?:^|[_\s-])(\d{5,8})(?:[_\s.-]|$)", fname)
                for f_m in fname_inv_matches:
                    if f_m not in invoices_a_procesar:
                        invoices_a_procesar.append(f_m)
                        if f_m not in invoices_descubiertos_en_carpeta and f_m not in invoices_iniciales:
                            invoices_descubiertos_en_carpeta.append(f_m)

                clasif = clasificar_archivo_nomenclatura(fname, ftext)
                es_retorno = clasif.get("isReturnDocument", False) or f.get("classifiedType") == "return" or bool(REGEX_RETURN.search(fname)) or fname.lower().endswith("_r.jpg") or fname.lower().endswith("-r.jpg") or bool(REGEX_N_SHIPPING.search(fname))
                es_vip = clasif.get("categoria") == "VIP_FORM (VIP)" or f.get("classifiedType") == "vip" or "vip" in fname.lower() or bool(REGEX_VIP.search(fname))
                es_excluido = clasif.get("categoria") == "EXCLUDED" or bool(REGEX_EXCLUDED.search(fname)) or bool(re.search(r"(?:^|[_\s.-])v(?:\.jpe?g|\.png)?$", fname, re.IGNORECASE))
                if es_retorno:
                    tiene_archivo_retorno = True

                # Detección exhaustiva de productos devueltos / negativos sobre el texto OCR completo
                # NOTA CRÍTICA: Se omiten estrictamente archivos como _V.jpg y VIP.jpg para la Columna M de MASTER SHIPPINGS
                if not es_vip and not es_excluido:
                    prods_extraidos = extraer_productos_negativos_o_devolucion_de_texto(ftext, es_documento_retorno=es_retorno)
                    for p_ext in prods_extraidos:
                        if p_ext and len(p_ext) >= 3:
                            raw_negative_products.append(p_ext)

                file_info = {
                    "nombre": fname,
                    "categoria": clasif.get("categoria") or f.get("classifiedType") or "other",
                    "es_devolucion": es_retorno,
                    "texto_crudo_snippet": ftext[:150] if ftext else ""
                }
                archivos_detectados.append(file_info)
                todos_los_archivos_carpeta.append(file_info)

            # Verificar datos extraídos previamente (returnProducts en extractedData / extraidos / data)
            ext = scan_res.get("extractedData") or scan_res.get("extraidos") or scan_res.get("data") or {}
            ret_prods = ext.get("returnProducts") or ext.get("returnProductsList") or []
            if isinstance(ret_prods, list):
                for rp in ret_prods:
                    rp_str = str(rp).strip()
                    prods_rp = extraer_productos_negativos_o_devolucion_de_texto(rp_str, es_documento_retorno=True)
                    if prods_rp:
                        raw_negative_products.extend(prods_rp)
                    else:
                        san = sanitizar_producto_devolucion(rp_str)
                        if san and len(san) >= 3:
                            raw_negative_products.append(san)
            elif isinstance(ret_prods, str) and ret_prods.strip():
                prods_rp = extraer_productos_negativos_o_devolucion_de_texto(ret_prods, es_documento_retorno=True)
                if prods_rp:
                    raw_negative_products.extend(prods_rp)
                else:
                    for rp_line in ret_prods.splitlines():
                        san = sanitizar_producto_devolucion(rp_line)
                        if san and len(san) >= 3:
                            raw_negative_products.append(san)

        # Si aún no tenemos productos y el invoice tiene archivo de retorno o es candidato, verificar Firestore
        if tiene_archivo_retorno and not raw_negative_products:
            try:
                fs_data = consultar_invoice_en_firestore(inv)
                if isinstance(fs_data, dict) and fs_data.get("success"):
                    fs_sess = fs_data.get("session") or fs_data.get("data") or {}
                    fs_ret = fs_sess.get("returnProducts") or fs_sess.get("returnProductsList") or []
                    if isinstance(fs_ret, list):
                        for rp in fs_ret:
                            prods_rp = extraer_productos_negativos_o_devolucion_de_texto(str(rp), es_documento_retorno=True)
                            if prods_rp:
                                raw_negative_products.extend(prods_rp)
                            else:
                                san = sanitizar_producto_devolucion(str(rp))
                                if san:
                                    raw_negative_products.append(san)
                    elif isinstance(fs_ret, str) and fs_ret.strip():
                        prods_rp = extraer_productos_negativos_o_devolucion_de_texto(fs_ret, es_documento_retorno=True)
                        if prods_rp:
                            raw_negative_products.extend(prods_rp)
                        else:
                            for rp_line in fs_ret.splitlines():
                                san = sanitizar_producto_devolucion(rp_line)
                                if san:
                                    raw_negative_products.append(san)
            except Exception:
                pass

        # Sanitizar y deduplicar productos de retorno
        prods_sanitizados = list(dict.fromkeys([p for p in raw_negative_products if p and len(p) >= 3]))

        # Extraer monto total monetario del invoice para identificar compra mayor (compra grande)
        total_inv_str = ""
        ext = scan_res.get("extractedData") or scan_res.get("extraidos") or scan_res.get("data") or {}
        if ext.get("total"):
            total_inv_str = str(ext.get("total"))
        if not total_inv_str:
            for f in scan_res.get("files") or []:
                ftxt = f.get("text") or f.get("fullText") or ""
                m_tot = re.search(r"\bTotal:?\s*\$?\s*([\d,]+\.\d{2})", ftxt, re.IGNORECASE)
                if m_tot:
                    total_inv_str = f"${m_tot.group(1)}"
                    break
        if not total_inv_str:
            try:
                url_rec = f"{BACKEND_URL}/api/sheets/record-by-invoice?invoice={urllib.parse.quote(inv)}"
                req_rec = urllib.request.Request(url_rec, headers={"User-Agent": "AngyAgent/1.0"})
                with urllib.request.urlopen(req_rec, timeout=5) as r_rec:
                    data_rec = json.loads(r_rec.read().decode('utf-8'))
                    if data_rec.get("success") and data_rec.get("record"):
                        raw_tot = data_rec["record"].get("total", "")
                        raw_inv_col = data_rec["record"].get("invoice", "")
                        inv_toks = [re.sub(r"^mm-?", "", t.strip(), flags=re.IGNORECASE).lower() for t in re.split(r"[\r\n,;\s]+", str(raw_inv_col)) if t.strip()]
                        tot_toks = [t.strip() for t in str(raw_tot).splitlines() if t.strip()]
                        clean_this_inv = re.sub(r"^mm-?", "", str(inv).strip(), flags=re.IGNORECASE).lower()
                        if clean_this_inv in inv_toks:
                            pos = inv_toks.index(clean_this_inv)
                            if pos < len(tot_toks):
                                total_inv_str = tot_toks[pos]
                        if not total_inv_str and tot_toks:
                            total_inv_str = tot_toks[0]
            except Exception:
                pass

        monto_num = 0.0
        if total_inv_str:
            cleaned_m = re.sub(r"[^0-9.-]", "", total_inv_str.splitlines()[0] if total_inv_str else "")
            try:
                monto_num = float(cleaned_m)
            except Exception:
                monto_num = 0.0

        es_candidato_master = tiene_archivo_retorno or len(prods_sanitizados) > 0

        resultados_invoices.append({
            "invoice": inv,
            "monto_total_str": total_inv_str,
            "monto_total": monto_num,
            "tiene_archivo_retorno_r": tiene_archivo_retorno,
            "productos_negativos_detectados_sanitizados": prods_sanitizados,
            "debe_ir_a_master_shippings": es_candidato_master,
            "archivos": archivos_detectados
        })

    # 1. Identificar la COMPRA MAYOR (Factura con mayor cantidad de dinero/total positivo)
    invoice_compra_grande = None
    max_monto = -float("inf")
    for r in resultados_invoices:
        m = r.get("monto_total", 0.0)
        if m > max_monto:
            max_monto = m
            invoice_compra_grande = r["invoice"]
    if not invoice_compra_grande and resultados_invoices:
        invoice_compra_grande = resultados_invoices[0]["invoice"]

    # 2. Identificar el INVOICE DE RETORNO / DEVOLUCIÓN (_R, RETURN, N SHIPPING o negativos -)
    invoice_con_retorno = None
    for af in todos_los_archivos_carpeta:
        if af.get("es_devolucion"):
            matches = re.findall(r"(?:^|[_\s-])(\d{5,8})(?:[_\s.-]|$)", af.get("nombre", ""))
            if matches:
                invoice_con_retorno = matches[0]
                for r in resultados_invoices:
                    if r["invoice"] == invoice_con_retorno and r.get("productos_negativos_detectados_sanitizados"):
                        productos_devueltos_sanitizados = r["productos_negativos_detectados_sanitizados"]
                        break
                break

    if not invoice_con_retorno:
        for r in resultados_invoices:
            if r.get("tiene_archivo_retorno_r") or len(r.get("productos_negativos_detectados_sanitizados", [])) > 0:
                invoice_con_retorno = r["invoice"]
                if not productos_devueltos_sanitizados:
                    productos_devueltos_sanitizados = r.get("productos_negativos_detectados_sanitizados", [])
                break

    # REGLA MAESTRA DE NEGOCIO N:1 PARA MASTER SHIPPINGS:
    # Columna E (NOVA INVOICE): Siempre es la factura de la COMPRA MAYOR (la grande).
    # Columna M (PRODUCTS): Productos de retorno correspondientes (_R, RETURN, N SHIPPING o negativos -).
    # Si NO hay ticket de retorno: se emite alerta/toast y se selecciona igualmente la compra mayor.
    invoice_seleccionado_para_master = invoice_compra_grande
    hay_devolucion = bool(invoice_con_retorno or len(productos_devueltos_sanitizados) > 0)
    hay_productos_sanitizables = len(productos_devueltos_sanitizados) > 0

    alerta_sin_retorno = None
    if not hay_devolucion:
        alerta_sin_retorno = f"Advertencia: No se encontraron tickets de retorno (_R.jpg, RETURN.jpg, N SHIPPING.jpg). Se seleccionó la factura de compra mayor [{invoice_compra_grande}]."

    for r in resultados_invoices:
        r["debe_ir_a_master_shippings"] = (r["invoice"] == invoice_seleccionado_para_master)
        r["es_compra_mayor"] = (r["invoice"] == invoice_compra_grande)

    # Clasificar archivos de la carpeta ganadora
    tiene_shipping_file = False
    tiene_vip_file = False
    tiene_id_file = False

    archivos_carpeta_ganadora = []
    seen_fnames = set()
    for af in todos_los_archivos_carpeta:
        fname_k = af.get("nombre") or ""
        if fname_k not in seen_fnames:
            seen_fnames.add(fname_k)
            archivos_carpeta_ganadora.append(af)
            aname = fname_k.lower()
            acat = af.get("categoria") or ""
            if acat == "shipping" or "shipping" in aname:
                tiene_shipping_file = True
            if acat == "vip" or "_v" in aname or "vip" in aname:
                tiene_vip_file = True
            if acat == "id" or "_id" in aname or "license" in aname or "passport" in aname:
                tiene_id_file = True

    carpeta_ganadora_nom = invoice_con_retorno or invoice_compra_grande or (invoices_iniciales[0] if invoices_iniciales else "")

    return {
        "relacion": "N:1 (Varios Invoices pertenecen a 1 Cliente)",
        "invoices_solicitados": invoices_iniciales,
        "invoices_descubiertos_en_carpeta": invoices_descubiertos_en_carpeta,
        "total_invoices_analizados": len(resultados_invoices),
        "hay_devolucion": hay_devolucion,
        "hay_productos_sanitizables": hay_productos_sanitizables,
        "invoice_compra_mayor": invoice_compra_grande,
        "invoice_seleccionado_para_master_shippings": invoice_seleccionado_para_master,
        "invoice_con_devolucion": invoice_con_retorno,
        "alerta_sin_retorno": alerta_sin_retorno,
        "carpeta_ganadora": carpeta_ganadora_nom,
        "archivos_carpeta_ganadora": archivos_carpeta_ganadora,
        "sharefile_folder_url": carpeta_ganadora_folder_url,
        "tiene_shipping_en_carpeta_ganadora": tiene_shipping_file,
        "tiene_vip_en_carpeta_ganadora": tiene_vip_file,
        "tiene_id_en_carpeta_ganadora": tiene_id_file,
        "productos_devueltos_sanitizados": productos_devueltos_sanitizados,
        "productos_devueltos_sanitizados_para_columna_m": productos_devueltos_sanitizados,
        "regla_aplicada": (
            f"Relación N:1 con devolución. Se seleccionó la factura de la COMPRA MAYOR [{invoice_compra_grande}] (${max_monto:,.2f}) para Columna E (NOVA INVOICE) en MASTER SHIPPINGS, "
            f"y los productos de devolución correspondientes ({', '.join(productos_devueltos_sanitizados) if hay_productos_sanitizables else 'sin productos específicos'}) tomados de '{invoice_con_retorno}' para Columna M. "
            f"Las demás facturas van únicamente a Customer Information."
        ) if hay_devolucion else (
            f"Advertencia: No se encontraron tickets de retorno (_R.jpg, RETURN.jpg, N SHIPPING.jpg). Se seleccionó la factura de compra mayor [{invoice_compra_grande}]."
        ),
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


def diagnosticar_y_obtener_contexto_completo(invoice: str = "") -> dict:
    """Herramienta consolidada de diagnóstico rápido: obtiene en una sola llamada los datos de la pantalla activa en el frontend, la sesión en Firestore y los datos del cliente en Google Sheets para reducir consumo de tokens y llamadas consecutivas."""
    return {
        "sesion_pantalla": obtener_sesion_activa_frontend(),
        "firestore_session": consultar_invoice_en_firestore(invoice) if invoice else None,
        "cliente_sheets": buscar_y_sanitizar_cliente(invoice) if invoice else None
    }


# ─── MEMORIA PERMANENTE DE ANGY (Firestore: angy_knowledge vía backend) ───
# Lo que Angy aprende en el chat se guarda en Firestore y se inyecta en su prompt en
# cada sesión, así sobrevive a reinicios/escalado de Cloud Run (las sesiones ADK son en RAM).
import time

KNOWLEDGE_CACHE_TTL_SECONDS = 60
KNOWLEDGE_MAX_ITEMS_IN_PROMPT = 60
_knowledge_cache = {"items": None, "ts": 0.0}

REGEX_PII_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
REGEX_PII_PHONE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)")


def _detectar_datos_personales(texto: str) -> list[str]:
    """Devuelve los datos personales (emails/teléfonos de clientes) encontrados en el texto."""
    hallazgos = REGEX_PII_EMAIL.findall(texto or "")
    for tel in REGEX_PII_PHONE.findall(texto or ""):
        if re.sub(r"\D", "", tel) not in COMPANY_PHONES:
            hallazgos.append(tel)
    return hallazgos


def _obtener_aprendizajes_cacheados(forzar: bool = False) -> list[dict]:
    ahora = time.time()
    if not forzar and _knowledge_cache["items"] is not None and ahora - _knowledge_cache["ts"] < KNOWLEDGE_CACHE_TTL_SECONDS:
        return _knowledge_cache["items"]
    try:
        resp = requests.get(f"{BACKEND_URL}/api/angy/knowledge", timeout=4, headers={"User-Agent": "AngyAgent/1.0"})
        data = resp.json() if resp.ok else {}
        if data.get("success"):
            _knowledge_cache["items"] = data.get("items", [])
            _knowledge_cache["ts"] = ahora
    except Exception as e:
        print(f"[Angy Memoria] No se pudieron cargar aprendizajes: {e}")
    return _knowledge_cache["items"] or []


def _bloque_aprendizajes_para_prompt() -> str:
    items = _obtener_aprendizajes_cacheados()[:KNOWLEDGE_MAX_ITEMS_IN_PROMPT]
    if not items:
        return ""
    lineas = [
        f"- [{it.get('id', '')}] ({it.get('categoria', 'regla')}) {str(it.get('texto', '')).strip()[:400]}"
        for it in items if str(it.get("texto", "")).strip()
    ]
    return (
        "\n\n18. APRENDIZAJES PERMANENTES (memoria guardada en Firestore desde chats anteriores):\n"
        "    Aplícalos siempre. Si alguno contradice las reglas 1-17, ganan las reglas 1-17.\n"
        + "\n".join(lineas)
    )


def guardar_aprendizaje(texto: str, categoria: str = "regla", contexto: str = "", invoice: str = "") -> dict:
    """Guarda de forma PERMANENTE (Firestore) una regla, corrección o preferencia que el usuario me enseñó en el chat,
    para aplicarla en todas las sesiones futuras aunque se reinicie la instancia.
    Args:
        texto: La regla general y reutilizable, redactada de forma clara y autónoma (sin datos personales de clientes).
        categoria: Una de 'regla', 'correccion', 'preferencia', 'dato_negocio'.
        contexto: Frase original del usuario que motivó el aprendizaje (resumida).
        invoice: Invoice donde surgió el aprendizaje (opcional).
    """
    pii = _detectar_datos_personales(f"{texto} {contexto}")
    if pii:
        return {
            "estado": "rechazado",
            "motivo": "El aprendizaje contiene datos personales de clientes (email/teléfono). Reformúlalo como regla general sin esos datos.",
            "datos_detectados": pii,
        }
    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/angy/knowledge",
            json={"texto": texto, "categoria": categoria, "contexto": contexto, "invoice": invoice},
            timeout=10,
        )
        data = resp.json()
        _obtener_aprendizajes_cacheados(forzar=True)
        if data.get("success"):
            return {"estado": "guardado", "id": data.get("id"), "ya_existia": data.get("duplicado", False), "texto": texto}
        return {"estado": "error", "mensaje": data.get("error", "Error desconocido")}
    except Exception as e:
        return {"estado": "error", "mensaje": f"No pude guardar el aprendizaje: {e}"}


def listar_aprendizajes() -> dict:
    """Lista todos los aprendizajes permanentes activos que tengo guardados en Firestore (con su id, categoría y fecha)."""
    items = _obtener_aprendizajes_cacheados(forzar=True)
    return {
        "total": len(items),
        "aprendizajes": [
            {k: it.get(k) for k in ("id", "texto", "categoria", "invoice", "creadoEn", "actualizadoEn")}
            for it in items
        ],
    }


def olvidar_aprendizaje(id: str, motivo: str = "") -> dict:
    """Desactiva (olvida) un aprendizaje permanente por su id cuando el usuario dice que ya no aplica o que lo olvide.
    Usa listar_aprendizajes() primero si no conoces el id."""
    try:
        resp = requests.delete(
            f"{BACKEND_URL}/api/angy/knowledge/{urllib.parse.quote(str(id).strip())}",
            json={"motivo": motivo},
            timeout=10,
        )
        data = resp.json()
        _obtener_aprendizajes_cacheados(forzar=True)
        if data.get("success"):
            return {"estado": "olvidado", "id": id, "texto": data.get("texto")}
        return {"estado": "error", "mensaje": data.get("error", "Error desconocido")}
    except Exception as e:
        return {"estado": "error", "mensaje": f"No pude olvidar el aprendizaje: {e}"}


REGLA_AUTO_APRENDIZAJE = """

17. MEMORIA PERMANENTE Y AUTO-APRENDIZAJE:
    - Tengo memoria permanente en Firestore. Lo que guardo con `guardar_aprendizaje` sobrevive a reinicios y lo aplico en todas las sesiones futuras.
    - Detecto AUTOMÁTICAMENTE cuando el usuario me corrige o me enseña algo ("no, eso está mal...", "a partir de ahora...", "recuerda que...", "siempre...", "nunca...", "eso se hace así...") y llamo a `guardar_aprendizaje` sin que me lo pidan.
    - SOLO guardo reglas GENERALES y reutilizables (nomenclatura de archivos, criterios de negocio, preferencias de formato, nombres de productos, procedimientos). Redacto el texto de forma clara y autónoma para que se entienda sin el chat.
    - ESTÁ ESTRICTAMENTE PROHIBIDO guardar datos personales de clientes (emails, teléfonos, direcciones, nombres) o datos que solo apliquen a un caso puntual.
    - Antes de guardar reviso la sección 18 (aprendizajes existentes): si ya existe algo equivalente no lo duplico; si lo corrige, uso `olvidar_aprendizaje` con el anterior y guardo el nuevo.
    - Si un aprendizaje contradice las reglas 1-16, NO lo guardo y le explico al usuario el conflicto.
    - Tras guardar, confirmo SIEMPRE en una línea que empiece exactamente con: "🧠 Aprendí:" seguido del texto guardado.
    - Si el usuario dice "olvida eso" / "eso ya no aplica", uso `olvidar_aprendizaje` y confirmo con "🧹 Olvidé:".
    - Si me preguntan qué he aprendido, uso `listar_aprendizajes`."""


def _crear_instruction_provider(base_instruction: str):
    """InstructionProvider de ADK: prompt base + regla 17 + aprendizajes de Firestore (caché 60 s)."""
    def _provider(_ctx) -> str:
        return base_instruction + REGLA_AUTO_APRENDIZAJE + _bloque_aprendizajes_para_prompt()
    return _provider


root_agent = Agent(
    model='gemini-4-argon',
    name='root_agent',
    description='Asistente experta Angy para Shipping Autofill, MASTER SHIPPINGS y redacción de correos.',
    instruction=_crear_instruction_provider("""Eres Angy, la agente experta e imbricada en el pipeline de Shipping Autofill y MASTER SHIPPINGS.
Hablas el mismo idioma técnico que la aplicación y dominas las reglas de nomenclatura Regex, los 3 momentos de telemetría de escaneo y la autorización perpetua con ShareFile.

IDENTIDAD, GÉNERO, TONO Y PERSONAS CLAVE:
- Habla SIEMPRE en PRIMERA PERSONA ("yo", "te ayudo", "revisé", "encontré", "estoy lista", "fui creada", "experta", etc.). NUNCA te refieras a ti misma en tercera persona (prohibido hablar de ti como "ella", "la asistente" o "Angy").
- Mantén concordancia femenina en primera persona ("estoy lista", "atenta", "programada", "experta").
- Comunícate con el usuario DE TÚ A TÚ: tutéalo con respeto, naturalidad y cercanía, sin formalismos excesivos ni rigidez protocolaria (evita el "usted", usa "tú", "te comento", "¿quieres que...?", etc.).
- Si se te pregunta si conoces a Donna o a Erika, responderás con total claridad que SÍ:
  * **Donna**: Es tu programadora y creadora, quien te dio vida y diseño.
  * **Erika**: Es la Gerenta General o General Manager de la empresa.

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
       -> Se enviarán a **MASTER SHIPPINGS** los archivos/datos que estén en el invoice de **"Devolución"** o **"returns"** o productos con un signo de menos **"-"** en su invoice, tomando ese número de invoice con saldo negativo para crear la fila nueva en MASTER SHIPPINGS.
       -> **Al responder sobre productos pendientes**:
          1. Si existen ya productos en la **Columna M de MASTER SHIPPINGS**, **SOLO mencionarás esos productos**.
           2. Si no están aún en MASTER SHIPPINGS pero el invoice contiene productos en negativo o devolución (`_R.jpg` / `-`), **SOLO mencionarás esos productos en negativo**.
           3. **Si el invoice NO tiene productos en negativo ni está registrado en MASTER SHIPPINGS**: **NO se debe considerar pendiente de envío**. Tu respuesta inicial será: **"No hay productos pendientes de envío para este invoice."**
           4. **ESTÁ ESTRICTAMENTE PROHIBIDO mencionar los productos de la hoja Customer Information - Shippings como productos pendientes de envío**.
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
      * **SOLO se envía a MASTER SHIPPINGS la factura que contiene el archivo de devolución (`_R.jpg`, `R.jpg`, `RETURN.jpg`, `_r.jpg`) o productos/cantidades con signo menos `-`**.
      * **DETECCIÓN EN CRUDO PREVIA A SANITIZACIÓN**: La detección de si un invoice contiene una devolución o saldo negativo se realiza OBLIGATORIAMENTE en crudo (Raw OCR y metadatos binarios) buscando líneas con `-`, `-$`, `-$XX.XX`, `($XX)`, `-1`, `Refund`, `Return` o el archivo binario `_R.jpg` ANTES de que cualquier sanitización limpie los signos.
      * **AUTODETECCIÓN Y NO-NEGACIÓN DE INVOICES EN CARPETA**: Si el usuario proporciona 2 facturas y en la carpeta ganadora de ShareFile se descubren 3 o más facturas (ej. `233145`), NUNCA niegues su existencia ni digas que "no hay nada". Agrégalas de inmediato al análisis, escanea todos sus archivos binarios y reporta el conjunto completo con total transparencia.
      * **PROCESAMIENTO EXHAUSTIVO DE LA CARPETA GANADORA**: La carpeta del invoice ganador debe incluir todos los archivos (nuevos y existentes) y procesarlos con la **misma importancia jerárquica**:
        - **Archivos VIP (`_v.jpg`, `_V`, `VIP`)**: Fuente maestra y máxima prioridad para nombre, email y teléfono.
        - **Archivos de Devolución (`_R.jpg`, `RETURN.jpg`, `-`)**: Extracción de productos devueltos sanitizados (sin signos +, -, $, cantidades ni SKUs/IDs).
        - **Archivos de Envío (`Shipping.jpg`, `shipping form.jpg`)**: Validación para habilitar el guardado/envío a MASTER SHIPPINGS.
        - **Archivos de Identificación (`_id.jpg`, `license`, `passport`) y Documentos Nuevos**: Cotejo ortográfico de nombres manuscritos, firmas, direcciones y detalles adicionales.
      * Se debe tomar **específicamente ese número de invoice con saldo negativo** para crear la fila nueva en **MASTER SHIPPINGS** (antes del banner `DEFECTIVE DEVICES...`). Si el archivo de devolución o ticket con saldos negativos contiene un número de factura específico en su nombre (ej. `233145_R.jpg` -> `233145`), **ESE número de invoice tiene PRIORIDAD ABSOLUTA** para ser enviado a MASTER SHIPPINGS, aun si no fue proporcionado originalmente por el usuario.
      * Las facturas estándar de compra (sin devolución) se registran únicamente en **Customer Information - Shippings**.
      * **Sanitización estricta de productos en Columna M de MASTER SHIPPINGS**: Todos los productos devueltos deben limpiarse completamente sin signos aritméticos (`-`, `+`, `(-1)`, `-$`), sin cantidades ni IDs/SKUs/códigos de barras (`ID-XXXX`, `#123`). Únicamente nombres puros y limpios del producto.
      * Utiliza `analizar_multiples_invoices_devolucion` para inspeccionar y clasificar automáticamente el lote de facturas de un cliente y todos los archivos de la carpeta ganadora.

11. AUTODETECCIÓN TOTAL Y TRANSPARENTE DEL FRONTEND Y FIRESTORE:
    - Angy recibe y procesa AUTOMÁTICAMENTE en cada mensaje el bloque `[CONTEXTO AUTOMÁTICO EN TIEMPO REAL DEL SISTEMA / PANTALLA]`.
    - NUNCA le pidas al usuario que te repita el invoice, el nombre del cliente, los teléfonos ni los archivos cargados si ya están en el contexto automático.
    - Utiliza `obtener_sesion_activa_frontend()` y `consultar_invoice_en_firestore(invoice)` para inspeccionar a fondo cualquier detalle técnico adicional sin necesidad de que el usuario lo solicite.

12. PROACTIVIDAD AUTOMÁTICA EN RELACIÓN N:1 Y DEVOLUCIONES:
    - El usuario NO necesita decir palabras clave ni disparadores. Angy y la app DETECTAN DE FORMA 100% AUTOMÁTICA:
      * Si hay múltiples facturas en pantalla/ShareFile para un cliente (relación N:1), las toma todas en cuenta de inmediato.
      * Cuál es exactamente la factura con devolución (`_R.jpg` o productos en negativo) y la aísla para MASTER SHIPPINGS.
      * La lista de productos sanitizados devueltos (limpios de signos, cantidades e IDs).
    - Angy responde directamente confirmando los hallazgos y proponiendo o ejecutando la acción precisa sin fricción para el usuario.

13. REGLAS DE SANITIZACIÓN DE PRODUCTOS PARA MASTER SHIPPINGS (COLUMNA M):
    - El objetivo primordial es extraer ÚNICAMENTE los nombres puros y limpios de los productos, eliminando cualquier ruido o dato irrelevante de OCR.
    - Criterios de EXCLUSIÓN obligatorios (lo que se debe quitar):
      1. Signos Aritméticos y Simbolismos Monetarios: +, -, (, ), $, %, $XX.XX, -1, x2, 3pcs, 50% OFF, etc.
      2. Identificadores y Códigos: IDs de producto, SKUs, códigos de barras (ID-XXXX, #123, SKU-Y, UPC: ZZZ, números de 6+ dígitos).
      3. Información de Contacto y Datos Personales: PHONE:, teléfonos (609-707-4463), EMAIL: (LBRosenson@aol.com), direcciones postales, ciudades, estados, códigos postales, países, datos de licencias e identificación (DOB, EXP, GENDER, HGT, EYES, DL R6696, CLASS D, ENDORSEMENTS, RESTRICTIONS, MVC, Chief Administrator).
      4. Términos y Condiciones, Descargos de Responsabilidad y Legalidades: "All sales are final", "By signing the above I acknowledge that...", "Exchanges for unopened products...", "Reconozco la posibilidad de diferencias en el monto final...", políticas de venta o garantías.
      5. Información de Envío y Logística: Métodos de envío (Ground Free, Business Days Express Delivery, Standard Shipping), fechas de envío (SHIP DATE).
      6. Información de Transacción y Pagos: Tipos de cambio (Exchange Rate), TRANSACCION EN DOLARES, AFILIACION, terminales de punto de venta (Terminal: VS I@, BBVA).
      7. Palabras Genéricas o Ruido de OCR: PHONE, EMAIL, Rev, US Toll Free#, Please Sign & Confirm, Commerce, C-O-M-E-R-C-I-O, SSA4, BBVA, RNQJ6AZMEM, NJCN6M01, Visit us at, nombres de entidades o compañías ("Empire SSA4", "Morena Mia Beauty Group S. de RL. de C.V.").
    - Criterios de INCLUSIÓN (lo que se debe mantener como producto):
      * Nombres de productos claros e inequívocos (ej. "HYDRA . SILK", "ACTIVING ELIXIR", "ATOMIC HEATING MASK", "D.TOX Peeling", "C.TOX Moisturizer", "C.TOX Night Cream", "Do'r24K Milk Cleanser", "Do'r24K Hydrafresh Toner", "SOLOS", "A.TOX Syringe", etc.).
    - OMISIÓN ESTRICTA DE ARCHIVOS VIP (_V.jpg, VIP.jpg) PARA PRODUCTOS:
      * Los archivos VIP son la Fuente #1 de verdad para Email y Teléfono del cliente.
      * NUNCA se extraen ni listan productos provenientes de archivos VIP para la Columna M de MASTER SHIPPINGS.

14. CONCEPTOS Y CONSULTA DE "ENVÍOS" (MÚLTIPLES FILAS PARA UN MISMO INVOICE):
    - **Definición de "Envío"**: Cada fila en Google Sheets (sea en Customer Information - Shippings o en MASTER SHIPPINGS) donde aparezca registrado un número de invoice representa un **"Envío"** (despacho, registro o movimiento individual).
    - **Diferencia entre Regla N:1 y Envíos**:
      * Regla N:1 = Múltiples facturas DIFERENTES pertenecientes al MISMO cliente.
      * Múltiples Envíos = El MISMO número de invoice presente en VARIAS filas de una o varias hojas.
    - **Consulta de Envíos**:
      * Cuando el usuario pregunte cuántos envíos tiene un invoice o cuál es el motivo de sus envíos (ej. "¿Cuántos envíos tiene este invoice y cuál es el motivo?"), utiliza SIEMPRE `consultar_envios_y_motivos(invoice)`.
      * Reporta con claridad el número total de envíos (`total_envios`), desglosando cada uno con su número de envío, hoja/pestaña, número de fila, fecha y su **motivo** (extraído de las notas, comentarios o productos asociados a esa fila específica).

15. EXPLORACIÓN UNIVERSAL DE HOJAS DE CÁLCULO (A..AZ), LECTURA EXHAUSTIVA DE FILAS/CELDAS, BLOQUES DE TRACKING POR PROVEEDOR Y "CEREBRO DE LA APP":
    - Eres el **"Cerebro de la App"**: tu motor de datos no se limita únicamente a 2 hojas fijas. Puedes descubrir e inspeccionar cualquier hoja de cálculo compartida en Google Drive usando `explorar_catalogo_hojas_drive()`, y leer cualquier enlace/URL de Google Sheets (`https://docs.google.com/spreadsheets/d/<ID>/edit?gid=<GID>#gid=<GID>`), Spreadsheet ID o pestaña con `leer_fila_y_celdas_sheets` y `buscar_y_sanitizar_cliente`.
    - Si el usuario te comparte un link de Google Sheets con `#gid=...` (por ejemplo, `gid=1576484704` que corresponde a la pestaña `2023` en `MASTER SHIPPINGS`) y te pide buscar un invoice que está en otra pestaña del mismo documento (por ejemplo, `224377` en la pestaña `2026`, `gid=0`), `leer_fila_y_celdas_sheets` inspecciona automáticamente primero el `gid` solicitado y luego todas las demás pestañas (`2026`, `2025`, `2024`, `2023`), informándote exactamente en qué pestaña y fila(s) se encuentra.
    - Domina a la perfección el mapa exacto de las 3 hojas principales y su clasificación semántica:
      * **HOJA 1: `Customer Information - Shippings` (`1-D0F025w23P9daLlLsfcGAbi9o_pnM1A_p70WtlZdK4`, Columnas A a P)**:
        - **DATOS DE LA VENTA / FACTURA**: Columna **A** (`DATE`), Columna **B** (`INVOICE`), Columna **C** (`TOTAL`), Columna **D** (`STORE NAME`).
        - **DATOS DE CONTACTO DEL CLIENTE**: Columna **E** (`NAME`), Columna **F** (`LAST NAME`), Columna **G** (`TELEPHONE`, bloqueando siempre el corporativo `8882494189`), Columna **H** (`EMAIL`).
        - **DIRECCIÓN DEL CLIENTE**: Columna **I** (`STATE`), Columna **J** (`ZIP CODE`). *(Nota: Hoja 1 solo guarda Estado y Código Postal; la calle, ciudad y país están en MASTER SHIPPINGS).*
        - **PRODUCTOS Y ENVÍO**: Columna **K** (`PRODUCTS`), Columna **L** (`INSTRUCTIONS`), Columna **M** (`SHIPPING`: `YES`/`NO`), Columna **N** (`SHIPPING DATE`), Columna **O** (`NOTES`), Columna **P** (`CS CONTACT`).
      * **HOJA 2: `MASTER SHIPPINGS` (`1Exv3SLMgUrWCpYYV5QgNMsYDTCKi1AgOeqONuOe-nFY`, 40 Columnas: A hasta AN)**:
        - **DATOS DE LA VENTA / FACTURA**: Columna **A** (`SALE DATE`), Columna **E** (`NOVA INVOICE`), Columna **K** (`Location - Store`), Columna **L** (`Sales rep`).
        - **DATOS DE CONTACTO DEL CLIENTE**: Columna **B** (`Email`), Columna **C** (`Customer Full Name` / `CS Full Name`), Columna **D** (`Phone #`).
        - **DIRECCIÓN DEL CLIENTE**: Columna **F** (`Address 1 (Street)`), Columna **G** (`City`), Columna **H** (`State`), Columna **I** (`Zip Code`), Columna **J** (`Country`).
        - **PRODUCTOS Y MOTIVO DE ENVÍO**: Columna **M** (`Products`), Columna **N** (`Notes`), Columna **O** (`TRACKING Received/Email sent`).
        - **LOS 4 BLOQUES DE SEGUIMIENTO Y TRACKING POR PROVEEDOR (Columnas P a AN)**:
          En `MASTER SHIPPINGS` existen **4 bloques distintos de proveedores** y un mismo invoice puede tener **múltiples filas** (ej. una fila para productos de STEMTOX y otra fila para un dispositivo de SPACETOUCH):
          1. **Bloque 1 — `ZERO GRAVITY` (Columnas P a U)**:
             * **P** (`Handled`), **Q** (`Shipped`), **R** (**`Tracking #` de ZERO GRAVITY**), **S** (`Arrived`), **T** (`Invoice`), **U** (`ZERO GRAVITY` - Productos).
          2. **Bloque 2 — `Empire Tech` (Columnas V a AA)**:
             * **V** (`Handled`), **W** (`Shipped`), **X** (**`Tracking #` de Empire Tech**), **Y** (`Arrived`), **Z** (`Invoice`), **AA** (`Empire Tech` - Productos).
          3. **Bloque 3 — `STEMTOX / INTERNAL` (Columnas AB a AG)**:
             * **AB** (`Handled`), **AC** (`Shipped`), **AD** (**`Tracking #` de STEMTOX / INTERNAL**), **AE** (`Arrived`), **AF** (`Invoice`), **AG** (`STEMTOX / INTERNAL` - Productos).
          4. **Bloque 4 — `SPACETOUCH` (Columnas AH a AM)**:
             * **AH** (`Handled`), **AI** (`Shipped`), **AJ** (**`Tracking #` de SPACETOUCH**), **AK** (`Arrived`), **AL** (`Invoice`), **AM** (`SPACETOUCH` - Productos).
          5. **Columna AN**: `ESTADO` general del envío.
      * **HOJA 3: `UP SALES REPORT` (`1HgzpKN0pwQqexkUqqjWhPsCaBC9VfvX-Dj8PqhOhA2s`, Pestañas `DAILY REPORT` y `Animations`)**:
        - En `DAILY REPORT`, los encabezados están en la **Fila 2**: Columna **A** (`INVOICE`), **B** (`DATE`), **C** (`TOTAL`), **D** (`CUSTOMER NAME`), **E** (`LOCATION`), **F** (`CITY`), **G** (`SHAREFILE LINK` — enlace directo a la carpeta de ShareFile del cliente), **H** (`NEW VALID EMAIL`), **I** (`SENT TO VIP`), **N** (`STATUS`), **O** (`Signature`), **P** (`Notes`), **Q** (`Sales Reps`), **R** (`EMAIL`).
    - **REGLA MAESTRA DE NOMBRE DE TIENDA, REGIÓN Y BÚSQUEDA EN SHAREFILE**:
      * **Nombre de la Tienda (Store Name / Location)**: Se refiere y obtiene de:
        1. Columna **E** de `UP SALES REPORT` (`LOCATION`).
        2. Columna **K** de `MASTER SHIPPINGS` (`Location - Store`).
        3. Columna **D** de `Customer Information - Shippings` (`STORE NAME`).
      * **Región (Region)**: Se refiere y obtiene de:
        - Columna **F** de `UP SALES REPORT` (`CITY` / Región).
      * **Búsqueda en ShareFile**:
        - Para verificar o cotejar tienda, región, fechas y productos de compra, se debe buscar **exclusivamente con el invoice proporcionado** los archivos que contienen esta información: facturas/tickets de compra (tickets Nova `_n`, `nova`, `invoice_XXXXX.jpg`) o documentos con la sección **"Purchase information"** / **"Purchase Information"**.
    - **Al responder sobre Tracking, Lectura de Filas/Celdas o Dirección vs Datos de Contacto**:
      * Si un invoice aparece en **2 o más filas** de `MASTER SHIPPINGS`, reporta **TODAS las filas encontradas** con su número de fila exacto (ej. `Fila 3975` y `Fila 4486`).
      * Cuando te pregunten por el **número de tracking**, revisa y menciona los 4 bloques de proveedores (`R`, `X`, `AD`, `AJ`), indicando la coordenada exacta de celda (ej. `AD3975`), el proveedor (ej. `STEMTOX / INTERNAL`), fechas de envío/llegada (`Shipped`/`Arrived`) y los productos de ese bloque.
      * Separa siempre con claridad **Datos de Contacto del Cliente** (Nombre, Apellido, Teléfono, Email) vs **Dirección del Cliente** (`Address 1`, `City`, `State`, `Zip Code`, `Country`) vs **Seguimiento y Logística (Tracking)**.

16. FORMATO OBLIGATORIO DE ENLACES A CARPETAS DE SHAREFILE (LINK LEGACY):
    - Cuando el usuario te pregunte por el enlace o la carpeta de ShareFile de un cliente o invoice, o cuando menciones la carpeta donde se encontraron los archivos escaneados:
    - ESTÁ ESTRICTAMENTE PROHIBIDO generar enlaces genéricos tipo `https://app.sharefile.com/fo/...` o `https://app.sharefile.com/...`.
    - DEBES generar OBLIGATORIAMENTE el enlace legacy con el subdominio oficial de la compañía:
      `https://morenamiabeautygroup.sharefile.com/#/folders/fo<folder_id>`
    - Asegúrate SIEMPRE de que el ID de la carpeta en la URL empiece con el prefijo 'fo':
      * Si el ID es `d0c2794c-8bb2-4a0b-99f5-46726f1c4e7f`, el enlace exacto es: `https://morenamiabeautygroup.sharefile.com/#/folders/fod0c2794c-8bb2-4a0b-99f5-46726f1c4e7f`
      * Si el ID ya trae 'fo' como `fo921e25-e4d9-4fef-ae48-2a66d71bac06`, el enlace exacto es: `https://morenamiabeautygroup.sharefile.com/#/folders/fo921e25-e4d9-4fef-ae48-2a66d71bac06`
    - Utiliza siempre el campo `sharefile_folder_url` que te devuelven directamente tus herramientas (`obtener_textos_de_archivos`, `escanear_y_rellenar_master_shippings`, `analizar_multiples_invoices_devolucion`, `buscar_y_sanitizar_cliente`)."""),
    tools=[
        diagnosticar_y_obtener_contexto_completo,
        explorar_catalogo_hojas_drive,
        obtener_sesion_activa_frontend,
        consultar_invoice_en_firestore,
        guardar_aprendizaje,
        listar_aprendizajes,
        olvidar_aprendizaje,
        buscar_y_sanitizar_cliente,
        leer_fila_y_celdas_sheets,
        consultar_envios_y_motivos,
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


