import hashlib
import fitz  # PyMuPDF
from PIL import Image
from cryptography.fernet import Fernet

# ─── Hashing ───────────────────────────────────────────────
def generate_file_hash(file):
    sha256 = hashlib.sha256()
    for chunk in file.chunks():
        sha256.update(chunk)
    return sha256.hexdigest()

# ─── Encryption ────────────────────────────────────────────
key = Fernet.generate_key()
cipher = Fernet(key)

def encrypt_data(data):
    return cipher.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    return cipher.decrypt(encrypted_data.encode()).decode()

# ─── LSB Steganography ─────────────────────────────────────
def embed_data_in_image(input_image_path, output_image_path, data):
    img = Image.open(input_image_path)
    img = img.convert('RGB')

    binary_data = ''.join(format(ord(i), '08b') for i in data)
    data_len = len(binary_data)

    pixels = list(img.getdata())
    new_pixels = []
    data_index = 0

    for pixel in pixels:
        r, g, b = pixel

        if data_index < data_len:
            r = (r & ~1) | int(binary_data[data_index])
            data_index += 1
        if data_index < data_len:
            g = (g & ~1) | int(binary_data[data_index])
            data_index += 1
        if data_index < data_len:
            b = (b & ~1) | int(binary_data[data_index])
            data_index += 1

        new_pixels.append((r, g, b))

    img.putdata(new_pixels)
    img.save(output_image_path)
    return output_image_path

# ─── PDF Seal Stamping ─────────────────────────────────────
def stamp_seal_on_pdf(input_pdf, output_pdf, seal_path):
    pdf = fitz.open(input_pdf)

    for page in pdf:
        rect = page.rect
        x = rect.width / 2 - 100
        y = rect.height / 2 - 100
        page.insert_image(
            fitz.Rect(x, y, x + 200, y + 200),
            filename=seal_path
        )

    pdf.save(output_pdf)
    pdf.close()