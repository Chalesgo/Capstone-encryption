"""Authenticated encryption for PDF files; decrypted bytes stay in memory."""
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import fitz
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

MAGIC = b'SGPDF\x00\x01'


class PDFDecryptionError(ValueError):
    pass


@lru_cache(maxsize=8)
def _import_key(path, modified_ns, size):
    return RSA.import_key(Path(path).read_bytes())


def _key(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _import_key(str(path), stat.st_mtime_ns, stat.st_size)


def encrypt_pdf_bytes(plaintext):
    key = get_random_bytes(32)
    public_key = _key(settings.RSA_PUBLIC_KEY_PATH)
    wrapped = PKCS1_OAEP.new(public_key, hashAlgo=SHA256).encrypt(key)
    nonce = get_random_bytes(12)
    header = MAGIC + len(wrapped).to_bytes(2, 'big') + wrapped + nonce
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(header)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return header + tag + ciphertext


def decrypt_pdf_bytes(data):
    try:
        if not data.startswith(MAGIC):
            raise ValueError('Unencrypted PDF in protected storage')
        offset = len(MAGIC)
        key_length = int.from_bytes(data[offset:offset + 2], 'big')
        if not 128 <= key_length <= 1024:
            raise ValueError('Invalid envelope')
        offset += 2
        wrapped = data[offset:offset + key_length]
        offset += key_length
        nonce = data[offset:offset + 12]
        offset += 12
        tag = data[offset:offset + 16]
        if len(nonce) != 12 or len(tag) != 16:
            raise ValueError('Truncated envelope')
        private_key = _key(settings.RSA_PRIVATE_KEY_PATH)
        key = PKCS1_OAEP.new(private_key, hashAlgo=SHA256).decrypt(wrapped)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(data[:offset])
        return cipher.decrypt_and_verify(data[offset + 16:], tag)
    except (ValueError, TypeError, IndexError) as error:
        raise PDFDecryptionError('The stored PDF could not be authenticated.') from error


def read_pdf(path, *, allow_plaintext=False):
    data = Path(path).read_bytes()
    if allow_plaintext and not data.startswith(MAGIC):
        return data
    return decrypt_pdf_bytes(data)


def open_pdf(path):
    """Internal PDF processing also accepts newly submitted, temporary plaintext."""
    return fitz.open(stream=read_pdf(path, allow_plaintext=True), filetype='pdf')


def write_pdf(path, plaintext):
    """Replace atomically, writing only ciphertext to the temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_pdf_bytes(plaintext)
    fd, temporary = tempfile.mkstemp(prefix='.sgpdf-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@deconstructible
class EncryptedPDFStorage(FileSystemStorage):
    def _save(self, name, content):
        content.seek(0)
        # FileFields always receive plaintext. Never trust an uploaded envelope.
        return super()._save(name, ContentFile(encrypt_pdf_bytes(content.read())))

    def _open(self, name, mode='rb'):
        if mode not in ('r', 'rb'):
            raise ValueError('Encrypted PDFs must be replaced using save().')
        return ContentFile(read_pdf(self.path(name)), name=name)


pdf_storage = EncryptedPDFStorage()
