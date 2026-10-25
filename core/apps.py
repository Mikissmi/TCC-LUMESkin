from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # o Django tenta atualizar o campo last_login toda vez que alguem loga,
        # mas a gente tirou essa coluna do Usuario, entao desliga esse aviso automatico
        from django.contrib.auth.signals import user_logged_in
        from django.contrib.auth.models import update_last_login
        user_logged_in.disconnect(update_last_login)
