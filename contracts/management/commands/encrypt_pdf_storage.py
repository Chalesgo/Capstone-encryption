"""Resumable conversion of existing media PDFs without changing their bytes."""
import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from contracts.pdf_storage import MAGIC, encrypt_pdf_bytes, decrypt_pdf_bytes, write_pdf
from contracts.models import Contract, ContractVersion, AuditLog


class Command(BaseCommand):
    help = 'Inspect PDF storage, or encrypt existing PDFs atomically with --apply.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        # Refuse to rewrite any files unless both configured keys are usable.
        probe = b'SealGuard storage encryption preflight'
        if decrypt_pdf_bytes(encrypt_pdf_bytes(probe)) != probe:
            raise CommandError('The configured encryption keys do not match.')
        root = Path(settings.MEDIA_ROOT).resolve()
        plaintext = encrypted = failed = converted = 0
        # Include registered PDFs even if an old filename has a non-PDF suffix.
        paths = {path for path in root.rglob('*') if path.suffix.lower() == '.pdf'}
        for model, field in ((Contract, 'file'), (ContractVersion, 'file'), (AuditLog, 'evidence_file')):
            paths.update(root / name for name in model.objects.values_list(field, flat=True) if name)
        for path in sorted(paths):
            if not path.is_file() or path.is_symlink():
                continue
            if not path.resolve().is_relative_to(root):
                continue
            try:
                before = path.read_bytes()
                if before.startswith(MAGIC):
                    decrypt_pdf_bytes(before)  # Also detect wrong keys or damaged ciphertext.
                    encrypted += 1
                    continue
                plaintext += 1
                if options['apply']:
                    digest = hashlib.sha256(before).digest()
                    write_pdf(path, before)
                    if hashlib.sha256(decrypt_pdf_bytes(path.read_bytes())).digest() != digest:
                        raise ValueError('Migration byte verification failed')
                    converted += 1
                    if converted % 250 == 0:
                        self.stdout.write(f'Encrypted and verified {converted} PDFs...')
            except Exception:
                failed += 1
                self.stderr.write('A PDF could not be processed; investigate storage before continuing.')
        self.stdout.write(f'Plaintext found: {plaintext}; already encrypted: {encrypted}; converted: {converted}; failed: {failed}')
        if failed:
            raise CommandError('Storage conversion has unresolved failures. Rerun after resolving them.')
