import hashlib
import hmac as hmac_lib
import fitz
import base64
import os
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
    doc = fitz.open(pdf_path)
    combined = ""

    for page in doc:
        # h_text
        text = page.get_text().strip().lower()
        combined += hashlib.sha256(text.encode()).hexdigest()

        # h_images
        for img in page.get_images(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            combined += hashlib.sha256(base_image["image"]).hexdigest()

    # h_metadata
    combined += hashlib.sha256(str(doc.metadata).encode()).hexdigest()
    doc.close()

    if previous_cf:
        combined += previous_cf

    cf = hashlib.sha256(combined.encode()).hexdigest()
    return cf

def verify_version_chain(contract):
    """
    Walks a contract's full version history and recomputes each version's
    fingerprint using its own file content plus the recorded previous
    fingerprint, confirming the entire chain is internally consistent.

    Returns a list of dicts, one per version, each noting whether that
    link in the chain checked out.
    """
    versions = contract.versions.order_by('version_number')
    results = []
    running_prev = ''

    for version in versions:
        expected_previous = running_prev
        link_valid = version.previous_fingerprint == expected_previous
        try:
            recomputed = generate_canonical_fingerprint(
                version.file.path,
                previous_cf=expected_previous or None,
            )
            valid = link_valid and recomputed == version.fingerprint
        except Exception:
            valid = False

        results.append({
            'version_number': version.version_number,
            'source': version.get_source_display(),
            'valid': valid,
            'created_at': version.created_at,
        })

        running_prev = version.fingerprint

    return results

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
    Preserves alpha (transparency) channel.
    """
    img = Image.open(input_image_path).convert('RGBA')
    pixels = list(img.getdata())

    data_with_delim = data + "||END||"
    binary_data = ''.join(format(ord(c), '08b') for c in data_with_delim)
    data_len = len(binary_data)

    if data_len > len(pixels) * 3:
        raise ValueError("Data too large to embed in this image.")

    new_pixels = []
    data_index = 0

    for pixel in pixels:
        r, g, b, a = pixel

        if data_index < data_len:
            r = (r & ~1) | int(binary_data[data_index])
            data_index += 1
        if data_index < data_len:
            g = (g & ~1) | int(binary_data[data_index])
            data_index += 1
        if data_index < data_len:
            b = (b & ~1) | int(binary_data[data_index])
            data_index += 1

        new_pixels.append((r, g, b, a))

    img.putdata(new_pixels)
    img.save(output_image_path, "PNG")
    return output_image_path

def extract_data_from_image(image_path: str):
    """
    Extracts the hidden encrypted CF from the seal image.
    """
    img = Image.open(image_path).convert('RGBA')
    pixels = list(img.getdata())

    binary_data = ""
    for pixel in pixels:
        r, g, b, a = pixel
        binary_data += str(r & 1)
        binary_data += str(g & 1)
        binary_data += str(b & 1)

    chars = []
    for i in range(0, len(binary_data), 8):
        byte = binary_data[i:i+8]
        if len(byte) < 8:
            break
        chars.append(chr(int(byte, 2)))
        if ''.join(chars[-7:]) == "||END||":
            break

    result = ''.join(chars)
    return result.replace("||END||", "")


def extract_lsb_marker_from_pdf(pdf_path: str, expected_marker: str):
    """Return the expected LSB marker if it survives as an embedded PDF image."""
    try:
        with fitz.open(pdf_path) as document:
            seen = set()
            for page in document:
                for image_info in page.get_images(full=True):
                    xref = image_info[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    image_bytes = document.extract_image(xref)['image']
                    image = Image.open(BytesIO(image_bytes)).convert('RGBA')
                    bits = []
                    chars = []
                    for red, green, blue, _alpha in image.getdata():
                        bits.extend((str(red & 1), str(green & 1), str(blue & 1)))
                        while len(bits) >= 8:
                            chars.append(chr(int(''.join(bits[:8]), 2)))
                            del bits[:8]
                            if ''.join(chars[-7:]) == '||END||':
                                value = ''.join(chars[:-7])
                                if hmac_lib.compare_digest(value, expected_marker):
                                    return value
                                break
    except Exception:
        return None
    return None

def stamp_seal_on_pdf(input_pdf, output_pdf, seal_path, qr_path=None, encrypted_cf=None,
                      qr_paths=None):
    """
    Creates a new PDF with extended page size to fit an authentication
    strip below content. Stores encrypted CF in PDF metadata for
    reliable verification.
    """
    src = fitz.open(input_pdf)
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

        # ── Small verified badge, overlapping the seal box's bottom-right corner ──
        badge_r = 11
        badge_cx = seal_box_x + seal_box_size - 2
        badge_cy = seal_box_y + seal_box_size - 2
        new_page.draw_circle(
            fitz.Point(badge_cx, badge_cy), badge_r,
            color=(1, 1, 1), fill=badge_color, width=1.5,
        )
        new_page.insert_text(
            fitz.Point(badge_cx - 4, badge_cy + 4),
            "✓", fontsize=13, color=(1, 1, 1), fontname="helv",
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
        center_rect = fitz.Rect(center_left, strip_y + 18, center_right, new_height - 14)

        new_page.insert_textbox(
            fitz.Rect(center_left, strip_y + 18, center_right, strip_y + 34),
            "BARANGAY STO. NIÑO, BIÑAN CITY",
            fontsize=9.5, color=text_dark, fontname="helv", align=0,
        )
        new_page.insert_textbox(
            fitz.Rect(center_left, strip_y + 34, center_right, strip_y + 48),
            "Digitally Authenticated Document",
            fontsize=8, color=accent_color, fontname="helv", align=0,
        )

        page_num_text = f"Page {src_page.number + 1} of {src.page_count}"
        new_page.insert_textbox(
            fitz.Rect(center_left, strip_y + 56, center_right, strip_y + 68),
            page_num_text,
            fontsize=7.5, color=text_muted, align=0,
        )
        new_page.insert_textbox(
            fitz.Rect(center_left, new_height - 30, center_right, new_height - 16),
            "Verify at: [your website URL]",
            fontsize=7, color=text_muted, align=0,
        )

    # ── Store encrypted CF in PDF metadata ──
    if encrypted_cf:
        metadata = dst.metadata
        metadata['keywords'] = f'SEALGUARD:{encrypted_cf}'
        dst.set_metadata(metadata)

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
        with fitz.open(pdf_path) as doc:
            keywords = doc.metadata.get('keywords', '')
        if keywords.startswith('SEALGUARD:'):
            return keywords[len('SEALGUARD:'):]
    except Exception:
        pass
    return None

    """
    Creates a new PDF with extended page size to fit the signature strip below content.
    Stores encrypted CF in PDF metadata for reliable verification.
    """
    src = fitz.open(input_pdf)
    dst = fitz.open()

    strip_height = 130
    seal_size = 110
    qr_size = 100
    padding = 15

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

        new_page.draw_rect(
            fitz.Rect(0, strip_y, page_width, new_height),
            color=(0.95, 0.95, 0.95),
            fill=(0.95, 0.95, 0.95)
        )

        new_page.draw_line(
            fitz.Point(0, strip_y),
            fitz.Point(page_width, strip_y),
            color=(0.5, 0.5, 0.5),
            width=1
        )

        seal_x = padding
        seal_y = strip_y + (strip_height - seal_size) // 2
        new_page.insert_image(
            fitz.Rect(seal_x, seal_y, seal_x + seal_size, seal_y + seal_size),
            filename=seal_path,
            overlay=True,
        )

        new_page.insert_text(
            fitz.Point(seal_x + (seal_size // 2) - 20, seal_y + seal_size + 10),
            "Official Seal",
            fontsize=6,
            color=(0.4, 0.4, 0.4)
        )

        if qr_path:
            qr_x = page_width - qr_size - padding
            qr_y = strip_y + (strip_height - qr_size) // 2
            new_page.insert_image(
                fitz.Rect(qr_x, qr_y, qr_x + qr_size, qr_y + qr_size),
                filename=qr_path,
                overlay=True,
            )

            new_page.insert_text(
                fitz.Point(qr_x + (qr_size // 2) - 25, qr_y + qr_size + 10),
                "Scan to Verify",
                fontsize=6,
                color=(0.4, 0.4, 0.4)
            )

            center_x = (seal_x + seal_size + qr_x) / 2

            page_num_text = f"Page {src_page.number + 1} of {src.page_count}"
            new_page.insert_text(
                fitz.Point(center_x - 30, strip_y + 20),
                page_num_text,
                fontsize=6,
                color=(0.5, 0.5, 0.5)
            )
            new_page.insert_text(
                fitz.Point(center_x - 60, strip_y + 38),
                "Barangay Sto. Nino, Binan City",
                fontsize=7,
                color=(0.3, 0.3, 0.3)
            )
            new_page.insert_text(
                fitz.Point(center_x - 55, strip_y + 52),
                "Digitally Authenticated Document",
                fontsize=7,
                color=(0.3, 0.3, 0.3)
            )
            new_page.insert_text(
                fitz.Point(center_x - 50, strip_y + 66),
                "Verify at: [your website URL]",
                fontsize=6,
                color=(0.4, 0.4, 0.4)
            )

    # ── Store encrypted CF in PDF metadata ──
    # This is hidden from normal viewers but readable by our system
    if encrypted_cf:
        metadata = dst.metadata
        metadata['keywords'] = f'SEALGUARD:{encrypted_cf}'
        dst.set_metadata(metadata)

    dst.save(output_pdf)
    dst.close()
    src.close()

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
):
    return AuditLog.objects.create(
        contract=contract,
        document_title=contract.title if contract else document_title,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        ip_address=get_client_ip(request),
        note=note,
        verification_source=verification_source,
        verification_result=verification_result,
        integrity_check=integrity_check,
        document_size=document_size,
    )
