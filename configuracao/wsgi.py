import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'configuracao.settings')

application = get_wsgi_application()


# Prepara o banco sozinho quando o site sobe no Vercel:
# cria as tabelas e, se nao houver produtos, importa o CSV
def _preparar_banco():
    from django.core.management import call_command
    from core.models import Produto

    call_command('migrate', interactive=False, verbosity=0)
    if not Produto.objects.exists():
        csv_path = Path(__file__).resolve().parent.parent / 'core' / 'data' / 'produtos_skincare.csv'
        call_command('import_produtos_csv', str(csv_path))


try:
    _preparar_banco()
except Exception as exc:
    print(f'[LUME] Erro ao preparar o banco: {exc}', file=sys.stderr)