from django.apps import AppConfig


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'

    def ready(self):
        from . import signals  # noqa: F401
        # Revision integrity checks run on demand for the document being viewed.
        # Never launch a whole-library scan during app startup.
