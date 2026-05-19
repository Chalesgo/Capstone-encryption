import hashlib
import hmac as hmac_lib
import fitz
import base64
import os
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
def generate_canonical_fingerprint(pdf_path):
    """
    Generates CF by hashing text, images, and metadata from the PDF.
    h_text + h_images + h_vectors all combined into one SHA-256 hash.
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

    cf = hashlib.sha256(combined.encode()).hexdigest()
    return cf

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

# ─── Seal Transparency ─────────────────────────────────────
def make_seal_transparent(input_path, output_path, threshold=240):
    """
    Removes white background from seal image.
    """
    img = Image.open(input_path).convert("RGBA")
    pixels = img.load()
    w, h = img.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if r > threshold and g > threshold and b > threshold:
                pixels[x, y] = (r, g, b, 0)

    img.save(output_path, "PNG")
    return output_path

def stamp_seal_on_pdf(input_pdf, output_pdf, seal_path, qr_path=None):
    """
    Creates a new PDF with extended page size to fit the signature strip below content.
    Print-compatible — maintains proper paper dimensions.
    """
    import fitz

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

        # ── Create a new page with extended height ──
        new_page = dst.new_page(width=page_width, height=new_height)

        # ── Copy original page content onto the new page exactly ──
        new_page.show_pdf_page(
            fitz.Rect(0, 0, page_width, page_height),
            src,
            src_page.number
        )

        strip_y = page_height

        # ── Draw footer strip background ──
        new_page.draw_rect(
            fitz.Rect(0, strip_y, page_width, new_height),
            color=(0.95, 0.95, 0.95),
            fill=(0.95, 0.95, 0.95)
        )

        # ── Top border line ──
        new_page.draw_line(
            fitz.Point(0, strip_y),
            fitz.Point(page_width, strip_y),
            color=(0.5, 0.5, 0.5),
            width=1
        )

        # ── Seal on the left ──
        seal_x = padding
        seal_y = strip_y + (strip_height - seal_size) // 2
        new_page.insert_image(
            fitz.Rect(seal_x, seal_y, seal_x + seal_size, seal_y + seal_size),
            filename=seal_path,
            overlay=True,
        )

        # ── Seal label ──
        new_page.insert_text(
            fitz.Point(seal_x + (seal_size // 2) - 20, seal_y + seal_size + 10),
            "Official Seal",
            fontsize=6,
            color=(0.4, 0.4, 0.4)
        )

        if qr_path:
            # ── QR on the right ──
            qr_x = page_width - qr_size - padding
            qr_y = strip_y + (strip_height - qr_size) // 2
            new_page.insert_image(
                fitz.Rect(qr_x, qr_y, qr_x + qr_size, qr_y + qr_size),
                filename=qr_path,
                overlay=True,
            )

            # ── QR label ──
            new_page.insert_text(
                fitz.Point(qr_x + (qr_size // 2) - 25, qr_y + qr_size + 10),
                "Scan to Verify",
                fontsize=6,
                color=(0.4, 0.4, 0.4)
            )

            # ── Center text ──
            center_x = (seal_x + seal_size + qr_x) / 2
            center_y = strip_y + (strip_height / 2) - 15

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