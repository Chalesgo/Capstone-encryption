from django.db import models

class Contract(models.Model):
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='contracts/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title