from .pdf_storage import open_pdf, read_pdf, write_pdf
import base64
import hashlib
import json
import re
from difflib import SequenceMatcher
from io import BytesIO

import fitz
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from PIL import Image, ImageChops, ImageEnhance, ImageOps, ImageStat


TOKEN_PREFIX = 'SGP1.'
RENDER_DPI = 120
TEXT_DIFFERENCE_THRESHOLD = 0.90
VISUAL_DIFFERENCE_THRESHOLD = 0.82
MANUAL_REVIEW_MINIMUM = 0.55


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _b64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def normalize_text(value):
    return re.sub(r'\s+', ' ', (value or '').casefold()).strip()


def _render_page(page):
    pixmap = page.get_pixmap(dpi=RENDER_DPI, colorspace=fitz.csGRAY, alpha=False)
    return Image.open(BytesIO(pixmap.tobytes('png'))).convert('L')


def _image_hash(image):
    normalized = ImageOps.fit(image, (1024, 1448), method=Image.Resampling.LANCZOS)
    return hashlib.sha256(normalized.tobytes()).hexdigest()


def build_manifest(contract_id, version_number, pdf_path, complete_fingerprint, issued_at):
    pages = []
    with open_pdf(pdf_path) as document:
        for index, page in enumerate(document):
            official_hash = _image_hash(_render_page(page))
            page_id = hashlib.sha256(
                f'{contract_id}:{version_number}:{index + 1}:{official_hash}'.encode()
            ).hexdigest()[:24]
            pages.append({
                'page_number': index + 1,
                'official_page_hash': official_hash,
                'official_text': normalize_text(page.get_text('text')),
                'page_id': page_id,
            })

    core = {
        'schema': 1,
        'document_id': str(contract_id),
        'contract_id': contract_id,
        'version_number': version_number,
        'total_pages': len(pages),
        'issue_timestamp': issued_at.isoformat(),
        'pages': pages,
        'complete_document_fingerprint': complete_fingerprint,
    }
    manifest_id = hashlib.sha256(canonical_json(core).encode()).hexdigest()
    manifest = {**core, 'manifest_id': manifest_id}
    tokens = [encode_page_token(manifest, page) for page in pages]
    return manifest, tokens


def encode_page_token(manifest, page):
    payload = {
        'd': manifest['document_id'], 'v': manifest['version_number'],
        'p': page['page_number'], 'n': manifest['total_pages'],
        'm': manifest['manifest_id'], 'i': page['page_id'],
    }
    return TOKEN_PREFIX + _b64url_encode(canonical_json(payload).encode())


def decode_page_token(token):
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError('Unsupported page token')
    payload = json.loads(_b64url_decode(token[len(TOKEN_PREFIX):]).decode('utf-8'))
    if set(payload) != {'d', 'v', 'p', 'n', 'm', 'i'}:
        raise ValueError('Malformed page token')
    return payload


def sign_manifest(manifest, private_key_path):
    with open(private_key_path, 'rb') as key_file:
        key = RSA.import_key(key_file.read())
    digest = SHA256.new(canonical_json(manifest).encode())
    return base64.b64encode(pkcs1_15.new(key).sign(digest)).decode('ascii')


def verify_manifest_signature(manifest, signature, public_key_path):
    try:
        with open(public_key_path, 'rb') as key_file:
            key = RSA.import_key(key_file.read())
        digest = SHA256.new(canonical_json(manifest).encode())
        pkcs1_15.new(key).verify(digest, base64.b64decode(signature, validate=True))
        core = {key: value for key, value in manifest.items() if key != 'manifest_id'}
        expected_id = hashlib.sha256(canonical_json(core).encode()).hexdigest()
        return expected_id == manifest.get('manifest_id')
    except (ValueError, TypeError, OSError):
        return False


def validate_token_membership(payload, manifest):
    try:
        page_number = int(payload['p'])
        expected = manifest['pages'][page_number - 1]
        return (
            payload['d'] == manifest['document_id']
            and int(payload['v']) == manifest['version_number']
            and int(payload['n']) == manifest['total_pages']
            and payload['m'] == manifest['manifest_id']
            and expected['page_number'] == page_number
            and payload['i'] == expected['page_id']
        )
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def compare_page(scan_page, official_page, expected_text):
    scan_image = _render_page(scan_page)
    official_image = _render_page(official_page)
    scan_text = normalize_text(scan_page.get_text('text'))
    official_text = normalize_text(expected_text)
    text_similarity = (
        SequenceMatcher(None, scan_text, official_text).ratio()
        if scan_text and official_text else None
    )
    scan_norm = ImageOps.fit(scan_image, (512, 724), method=Image.Resampling.LANCZOS)
    official_norm = ImageOps.fit(official_image, (512, 724), method=Image.Resampling.LANCZOS)
    scan_norm = ImageOps.autocontrast(ImageEnhance.Contrast(scan_norm).enhance(1.1))
    official_norm = ImageOps.autocontrast(official_norm)
    mean_difference = ImageStat.Stat(ImageChops.difference(scan_norm, official_norm)).mean[0] / 255.0
    visual_similarity = max(0.0, 1.0 - mean_difference)

    if text_similarity is not None and text_similarity < TEXT_DIFFERENCE_THRESHOLD:
        state = 'differences'
    elif visual_similarity < MANUAL_REVIEW_MINIMUM:
        state = 'manual_review'
    elif visual_similarity < VISUAL_DIFFERENCE_THRESHOLD:
        state = 'differences' if text_similarity is not None else 'manual_review'
    elif text_similarity is None:
        state = 'manual_review'
    else:
        state = 'verified'
    return {'state': state, 'text_similarity': text_similarity, 'visual_similarity': visual_similarity}
