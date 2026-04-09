import hashlib
import fitz  # PyMuPDF
import base64
import os
from PIL import Image
from Crypto.Cipher import AES
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
        combined += page.get_text()

        # h_images
        for img in page.get_images(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            combined += hashlib.sha256(base_image["image"]).hexdigest()

    # h_vectors / metadata
    combined += str(doc.metadata)
    doc.close()

    cf = hashlib.sha256(combined.encode()).hexdigest()
    return cf

# ─── AES-256-CBC Encryption ────────────────────────────────
def encrypt_cf(cf: str):
    """
    Encrypts the canonical fingerprint using AES-256-CBC.
    Returns: (encrypted_b64, key_b64, iv_b64)
    Store all three in the database.
    """
    key = get_random_bytes(32)
    iv = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(cf.encode(), AES.block_size))

    return (
        base64.b64encode(encrypted).decode(),
        base64.b64encode(key).decode(),
        base64.b64encode(iv).decode()
    )

def decrypt_cf(encrypted_b64: str, key_b64: str, iv_b64: str):
    """
    Decrypts the canonical fingerprint for verification.
    """
    key = base64.b64decode(key_b64)
    iv = base64.b64decode(iv_b64)
    encrypted = base64.b64decode(encrypted_b64)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(encrypted), AES.block_size).decode()

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
    Run once on your default_seal.png.
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
    pdf = fitz.open(input_pdf)

    for page in pdf:
        rect = page.rect
        x = rect.width - 160
        y = rect.height - 160

        page.insert_image(
            fitz.Rect(x, y, x + 140, y + 140),
            filename=seal_path,
            overlay=True,
            alpha=80        # ← this makes it semi-transparent
        )

    pdf.save(output_pdf)
    pdf.close()