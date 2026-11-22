from django.apps import AppConfig

class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from django.contrib.auth.models import update_last_login
        user_logged_in.disconnect(update_last_login)
