from .pdf_storage import open_pdf, read_pdf, write_pdf
import re
import hashlib
import hmac as hmac_lib
import fitz
import base64
import os
import json
from io import BytesIO
from PIL import Image
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
import qrcode
# ─── File Hash (used during initial upload) ────────────────
def generate_file_hash(file):
    sha256 = hashlib.sha256()
    for chunk in file.chunks():
        sha256.update(chunk)
    return sha256.hexdigest()

# ─── Canonical Fingerprint ─────────────────────────────────
def generate_canonical_fingerprint(pdf_path, previous_cf=None):
    """
    Generates CF by hashing extracted text, embedded images, and metadata.

    Vector drawing commands are intentionally excluded: PyMuPDF's interpreted
    drawing output is not stable enough across PDF producers and library versions
    to serve as a long-lived canonical identifier.

    If previous_cf is provided, it is folded into the hash as well,
    chaining this version's fingerprint to the prior version's fingerprint
    (hash-chained version history — tampering with an earlier version
    breaks the chain for every version after it).
    """
    doc = open_pdf(pdf_path)
    combined_hash = hashlib.sha256()
    try:
        for page in doc:
            text = page.get_text().strip().lower()
            combined_hash.update(hashlib.sha256(text.encode()).hexdigest().encode())

            for img in page.get_images(full=True):
                base_image = doc.extract_image(img[0])
                combined_hash.update(hashlib.sha256(base_image["image"]).hexdigest().encode())
        combined_hash.update(hashlib.sha256(str(doc.metadata).encode()).hexdigest().encode())
    finally:
        doc.close()

    if previous_cf:
        combined_hash.update(previous_cf.encode())
    return combined_hash.hexdigest()


def _normalize_vector_value(value):
    """Convert PyMuPDF drawing values into deterministic JSON-safe data."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    if isinstance(value, fitz.Point):
        return [round(value.x, 4), round(value.y, 4)]
    if isinstance(value, fitz.Rect):
        return [round(value.x0, 4), round(value.y0, 4), round(value.x1, 4), round(value.y1, 4)]
    if isinstance(value, fitz.Quad):
        return [_normalize_vector_value(point) for point in value]
    if isinstance(value, dict):
        return {
            str(key): _normalize_vector_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_vector_value(item) for item in value]
    return str(value)


def generate_vector_fingerprint(pdf_path):
    """Hash normalized vector drawing commands for strict editor/tamper detection."""
    document = open_pdf(pdf_path)
    try:
        digest = hashlib.sha256()
        digest.update(b'[')
        for page_number, page in enumerate(document):
            drawings = page.get_drawings()
            normalized = []
            for drawing in drawings:
                normalized.append(_normalize_vector_value(drawing))
            if page_number:
                digest.update(b',')
            serialized_page = json.dumps(
                {'page': page_number, 'drawings': normalized},
                sort_keys=True, separators=(',', ':'), ensure_ascii=False,
            )
            digest.update(serialized_page.encode('utf-8'))
            del drawings, normalized, serialized_page
        digest.update(b']')
        return digest.hexdigest()
    finally:
        document.close()

# ─── HMAC ──────────────────────────────────────────────────
def generate_hmac(cf: str, key: bytes):
    """
    Generates HMAC-SHA256 of the canonical fingerprint.
    Provides integrity authentication of the CF before encryption.
    """
    return hmac_lib.new(key, cf.encode(), hashlib.sha256).hexdigest()

def verify_hmac(cf: str, key: bytes, expected_hmac: str):
    """
    Verifies the HMAC of the canonical fingerprint.
    Returns True if valid, False if tampered.
    """
    computed = hmac_lib.new(key, cf.encode(), hashlib.sha256).hexdigest()
    return hmac_lib.compare_digest(computed, expected_hmac)

# ─── AES-256-CBC Encryption ────────────────────────────────
def encrypt_cf(cf: str, public_key_path: str):
    """
    1. Generates HMAC of CF
    2. Encrypts CF with AES-256-CBC
    3. Wraps AES key with RSA public key
    Returns: (encrypted_b64, hmac_value, wrapped_key_b64, iv_b64)
    """
    # Generate AES key and IV
    aes_key = get_random_bytes(32)
    iv = get_random_bytes(16)

    # Step 1: Generate HMAC using AES key as the HMAC key
    hmac_value = generate_hmac(cf, aes_key)

    # Step 2: Encrypt CF with AES-256-CBC
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(cf.encode(), AES.block_size))
    encrypted_b64 = base64.b64encode(encrypted).decode()

    # Step 3: Wrap AES key with RSA public key
    with open(public_key_path, 'rb') as f:
        public_key = RSA.import_key(f.read())

    rsa_cipher = PKCS1_OAEP.new(public_key)
    wrapped_key = rsa_cipher.encrypt(aes_key)
    wrapped_key_b64 = base64.b64encode(wrapped_key).decode()

    return (
        encrypted_b64,
        hmac_value,
        wrapped_key_b64,
        base64.b64encode(iv).decode()
    )

def decrypt_cf(encrypted_b64: str, wrapped_key_b64: str, iv_b64: str,
               hmac_value: str, private_key_path: str):
    """
    1. Unwraps AES key with RSA private key
    2. Decrypts CF with AES-256-CBC
    3. Verifies HMAC
    Returns decrypted CF if valid, raises exception if HMAC fails.
    """
    # Step 1: Unwrap AES key using RSA private key
    with open(private_key_path, 'rb') as f:
        private_key = RSA.import_key(f.read())

    rsa_cipher = PKCS1_OAEP.new(private_key)
    aes_key = rsa_cipher.decrypt(base64.b64decode(wrapped_key_b64))

    # Step 2: Decrypt CF
    iv = base64.b64decode(iv_b64)
    encrypted = base64.b64decode(encrypted_b64)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted_cf = unpad(cipher.decrypt(encrypted), AES.block_size).decode()

    # Step 3: Verify HMAC
    if not verify_hmac(decrypted_cf, aes_key, hmac_value):
        raise ValueError("HMAC verification failed — fingerprint integrity compromised.")

    return decrypted_cf

# ─── LSB Steganography ─────────────────────────────────────
def embed_data_in_image(input_image_path, output_image_path, data: str):
    """
    Embeds encrypted CF string into seal image via LSB.
    Preserves alpha (transparency) and writes only to fully opaque pixels.

    PyMuPDF stores a transparent PNG's RGB data separately from its alpha mask
    and may premultiply the RGB values of translucent pixels.  Restricting the
    payload to opaque pixels keeps the LSB values stable after PDF embedding.
    """
    img = Image.open(input_image_path).convert('RGBA')
    pixels = list(img.getdata())

    data_with_delim = data + "||END||"
    binary_data = ''.join(format(ord(c), '08b') for c in data_with_delim)
    data_len = len(binary_data)

    writable_pixels = sum(1 for _r, _g, _b, alpha in pixels if alpha == 255)
    if data_len > writable_pixels * 3:
        raise ValueError("Data too large to embed in this image.")

    new_pixels = []
    data_index = 0

    for pixel in pixels:
        r, g, b, a = pixel

        if a == 255 and data_index < data_len:
            r = (r & ~1) | int(binary_data[data_index])
            data_index += 1
        if a == 255 and data_index < data_len:
            g = (g & ~1) | int(binary_data[data_index])
            data_index += 1
        if a == 255 and data_index < data_len:
            b = (b & ~1) | int(binary_data[data_index])
            data_index += 1

        new_pixels.append((r, g, b, a))

    img.putdata(new_pixels)
    img.save(output_image_path, "PNG")
    return output_image_path


def _extract_lsb_data(pixels, *, opaque_only=False):
    """Extract one delimited LSB payload from an RGBA pixel sequence."""
    bits = []
    chars = []
    delimiter = "||END||"

    for red, green, blue, alpha in pixels:
        if opaque_only and alpha != 255:
            continue
        bits.extend((str(red & 1), str(green & 1), str(blue & 1)))
        while len(bits) >= 8:
            chars.append(chr(int(''.join(bits[:8]), 2)))
            del bits[:8]
            if ''.join(chars[-len(delimiter):]) == delimiter:
                return ''.join(chars[:-len(delimiter)])
    return None


def extract_data_from_image(image_path: str):
    """
    Extracts the hidden encrypted CF from the seal image.
    """
    img = Image.open(image_path).convert('RGBA')
    pixels = list(img.getdata())
    # New transparent seals use opaque pixels so the payload survives
    # PyMuPDF's alpha handling. Fall back to the legacy all-pixel layout.
    return _extract_lsb_data(pixels, opaque_only=True) or _extract_lsb_data(pixels) or ''


def extract_lsb_marker_from_pdf(pdf_path: str, expected_marker: str):
    """Return the expected LSB marker if it survives as an embedded PDF image."""
    try:
        with open_pdf(pdf_path) as document:
            seen = set()
            for page in document:
                for image_info in page.get_images(full=True):
                    xref = image_info[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    image_bytes = document.extract_image(xref)['image']
                    image = Image.open(BytesIO(image_bytes)).convert('RGB')
                    smask_xref = image_info[1]
                    if smask_xref:
                        mask_bytes = document.extract_image(smask_xref)['image']
                        alpha = Image.open(BytesIO(mask_bytes)).convert('L')
                        if alpha.size == image.size:
                            image.putalpha(alpha)
                        else:
                            image = image.convert('RGBA')
                    else:
                        image = image.convert('RGBA')

                    pixels = list(image.getdata())
                    for opaque_only in (False, True):
                        value = _extract_lsb_data(pixels, opaque_only=opaque_only)
                        if value and hmac_lib.compare_digest(value, expected_marker):
                            return value
    except Exception:
        return None
    return None

def stamp_seal_on_pdf(input_pdf, output_pdf, seal_path, qr_path=None, encrypted_cf=None,
                      qr_paths=None, encrypt_output=False, verify_url=None):
    """
    Creates a new PDF with extended page size to fit an authentication
    strip below content. Stores encrypted CF in PDF metadata for
    reliable verification.
    """
    src = open_pdf(input_pdf)
    dst = fitz.open()

    strip_height = 150
    seal_box_size = 100
    qr_box_size = 90
    padding = 18
    accent_color = (0.15, 0.35, 0.75)      # barangay blue accent bar
    border_color = (0.75, 0.78, 0.83)      # box borders
    text_dark = (0.15, 0.18, 0.22)
    text_muted = (0.45, 0.5, 0.56)
    badge_color = (0.16, 0.6, 0.4)         # verified checkmark green

    for src_page in src:
        src_rect = src_page.rect
        page_width = src_rect.width
        page_height = src_rect.height
        new_height = page_height + strip_height

        new_page = dst.new_page(width=page_width, height=new_height)

        new_page.show_pdf_page(
            fitz.Rect(0, 0, page_width, page_height),
            src,
            src_page.number
        )

        strip_y = page_height

        # ── Strip background ──
        new_page.draw_rect(
            fitz.Rect(0, strip_y, page_width, new_height),
            color=None,
            fill=(0.98, 0.98, 0.99),
        )

        # ── Accent bar along the top edge of the strip ──
        new_page.draw_rect(
            fitz.Rect(0, strip_y, page_width, strip_y + 3),
            color=None,
            fill=accent_color,
        )

        # ── Seal box (left) ──
        seal_box_x = padding
        seal_box_y = strip_y + (strip_height - seal_box_size) / 2 - 6
        seal_box_rect = fitz.Rect(seal_box_x, seal_box_y, seal_box_x + seal_box_size, seal_box_y + seal_box_size)

        new_page.draw_rect(seal_box_rect, color=border_color, fill=(1, 1, 1), width=1)
        new_page.insert_image(
            fitz.Rect(seal_box_x + 6, seal_box_y + 6, seal_box_x + seal_box_size - 6, seal_box_y + seal_box_size - 6),
            filename=seal_path,
            overlay=True,
        )

        new_page.insert_textbox(
            fitz.Rect(seal_box_x - 10, seal_box_y + seal_box_size + 6, seal_box_x + seal_box_size + 10, seal_box_y + seal_box_size + 20),
            "Official Seal", fontsize=7, color=text_muted, align=1,
        )

        # ── Vertical divider between seal and center text ──
        divider1_x = seal_box_x + seal_box_size + padding
        new_page.draw_line(
            fitz.Point(divider1_x, strip_y + 16),
            fitz.Point(divider1_x, new_height - 16),
            color=border_color, width=1,
        )

        # ── QR box (right), only if provided ──
        page_qr_path = qr_paths[src_page.number] if qr_paths else qr_path
        if page_qr_path:
            qr_box_x = page_width - qr_box_size - padding
            qr_box_y = strip_y + (strip_height - qr_box_size) / 2 - 6
            qr_box_rect = fitz.Rect(qr_box_x, qr_box_y, qr_box_x + qr_box_size, qr_box_y + qr_box_size)

            new_page.draw_rect(qr_box_rect, color=border_color, fill=(1, 1, 1), width=1)
            new_page.insert_image(
                fitz.Rect(qr_box_x + 6, qr_box_y + 6, qr_box_x + qr_box_size - 6, qr_box_y + qr_box_size - 6),
                filename=page_qr_path,
                overlay=True,
            )
            new_page.insert_textbox(
                fitz.Rect(qr_box_x - 10, qr_box_y + qr_box_size + 6, qr_box_x + qr_box_size + 10, qr_box_y + qr_box_size + 20),
                "Scan to Verify", fontsize=7, color=text_muted, align=1,
            )

            # ── Vertical divider between center text and QR ──
            divider2_x = qr_box_x - padding
            new_page.draw_line(
                fitz.Point(divider2_x, strip_y + 16),
                fitz.Point(divider2_x, new_height - 16),
                color=border_color, width=1,
            )

            center_left = divider1_x + 14
            center_right = divider2_x - 14
        else:
            center_left = divider1_x + 14
            center_right = page_width - padding

        # ── Center text block ──
        new_page.insert_text(
            fitz.Point(center_left, strip_y + 31), "BARANGAY STO. NIÑO, BIÑAN CITY",
            fontsize=11, color=text_dark, fontname="hebo",
        )

        # Vector check badge (the built-in fonts have no check glyph).
        icon_cx, icon_cy, icon_r = center_left + 5.5, strip_y + 49, 5.5
        new_page.draw_circle(fitz.Point(icon_cx, icon_cy), icon_r, color=None, fill=badge_color)
        check = new_page.new_shape()
        check.draw_polyline([
            fitz.Point(icon_cx - 2.6, icon_cy + 0.2),
            fitz.Point(icon_cx - 0.7, icon_cy + 2.2),
            fitz.Point(icon_cx + 2.7, icon_cy - 2.0),
        ])
        check.finish(color=(1, 1, 1), fill=None, width=1.3, closePath=False)
        check.commit()
        new_page.insert_textbox(
            fitz.Rect(center_left + 16, strip_y + 43, center_right, strip_y + 57),
            "Digitally Authenticated Document",
            fontsize=8.5, color=accent_color, fontname="helv", align=0,
        )

        new_page.draw_line(
            fitz.Point(center_left, strip_y + 66), fitz.Point(center_right, strip_y + 66),
            color=(0.88, 0.9, 0.93), width=0.6,
        )
        new_page.insert_textbox(
            fitz.Rect(center_left, strip_y + 71, center_right, strip_y + 84),
            f"Page {src_page.number + 1} of {src.page_count}",
            fontsize=8, color=text_muted, fontname="helv", align=0,
        )

        # ── Verification link ──
        new_page.insert_text(
            fitz.Point(center_left, new_height - 38), "VERIFY THIS DOCUMENT ONLINE",
            fontsize=6.5, color=text_muted, fontname="hebo",
        )
        link_size = 9
        if verify_url:
            display = re.sub(r'^https?://', '', verify_url).rstrip('/')
            text_width = fitz.get_text_length(display, fontname="helv", fontsize=link_size)
            while text_width > center_right - center_left and link_size > 6:
                link_size -= 0.5
                text_width = fitz.get_text_length(display, fontname="helv", fontsize=link_size)
        else:
            display = "the SealGuard website"
            text_width = fitz.get_text_length(display, fontname="helv", fontsize=link_size)
        baseline = new_height - 22
        new_page.insert_text(
            fitz.Point(center_left, baseline), display,
            fontsize=link_size, color=accent_color, fontname="helv",
        )
        if verify_url:
            new_page.draw_line(
                fitz.Point(center_left, baseline + 1.6),
                fitz.Point(center_left + text_width, baseline + 1.6),
                color=accent_color, width=0.6,
            )
            new_page.insert_link({
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(center_left, baseline - link_size, center_left + text_width, baseline + 3),
                "uri": verify_url,
            })

    # ── Store encrypted CF in PDF metadata ──
    if encrypted_cf:
        metadata = dst.metadata
        metadata['keywords'] = f'SEALGUARD:{encrypted_cf}'
        dst.set_metadata(metadata)

    if encrypt_output:
        write_pdf(output_pdf, dst.tobytes())
    else:
        dst.save(output_pdf)
    dst.close()
    src.close()

def generate_qr_code(data: str, output_path: str):
    """
    Generates a QR code containing the encrypted CF.
    This is embedded alongside the seal for physical copy scanning.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(output_path)
    return output_path

def extract_cf_from_metadata(pdf_path: str):
    """Extracts the SEALGUARD value from the PDF metadata, if present."""
    try:
        with open_pdf(pdf_path) as doc:
            keywords = doc.metadata.get('keywords', '')
        if keywords.startswith('SEALGUARD:'):
            return keywords[len('SEALGUARD:'):]
    except Exception:
        pass
    return None

from .models import AuditLog

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_activity(
    request,
    action,
    contract=None,
    note='',
    *,
    document_title='',
    verification_source='',
    verification_result='',
    integrity_check='',
    document_size=None,
    version_number=None,
    verification_debug_log='',
):
    if document_size is None and contract and contract.file:
        try:
            document_size = contract.file.size
        except (OSError, ValueError):
            # A deleted or unavailable file should not prevent the audit
            # event from being recorded.
            document_size = None
    return AuditLog.objects.create(
        contract=contract,
        document_title=contract.title if contract else document_title,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        ip_address=get_client_ip(request),
        version_number=version_number,
        note=note,
        verification_source=verification_source,
        verification_result=verification_result,
        integrity_check=integrity_check,
        document_size=document_size,
        verification_debug_log=verification_debug_log,
    )
