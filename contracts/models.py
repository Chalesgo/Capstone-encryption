from django.db import models

class Contract(models.Model):
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='contracts/')
    fingerprint = models.CharField(max_length=64, blank=True)
    encrypted_cf = models.TextField(blank=True)
    aes_key = models.TextField(blank=True)
    aes_iv = models.TextField(blank=True)
    wrapped_key = models.TextField(blank=True)    # RSA-wrapped AES key
    hmac_value = models.TextField(blank=True)     # HMAC of the CF
    seal_image = models.ImageField(upload_to='seals/', blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    # In models.py add this field
    original_fingerprint = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return self.title