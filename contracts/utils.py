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

# ─── PDF Seal Stamping ─────────────────────────────────────
def stamp_seal_on_pdf(input_pdf, output_pdf, seal_path):
    """
    Stamps the steganographic seal onto every page of the PDF.
    Placed at bottom-right, semi-transparent.
    """
    pdf = fitz.open(input_pdf)

    for page in pdf:
        rect = page.rect
        x = rect.width - 160
        y = rect.height - 160

        page.insert_image(
            fitz.Rect(x, y, x + 140, y + 140),
            filename=seal_path,
            overlay=True,
            alpha=80
        )

    pdf.save(output_pdf)
    pdf.close()