from django.apps import AppConfig


class DogfoodingConfig(AppConfig):
    name = "examples.dogfooding"
    label = "nova_dogfooding"
    default_auto_field = "django.db.models.BigAutoField"
