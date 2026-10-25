import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import RegraTratamento

# colunas que o CSV precisa ter ("Concentrações" existe no arquivo, mas é ignorada)
COLUNAS = [
    'Faixa_Etária', 'Tipo_de_Pele', 'Subtipo_de_Pele', 'Sensibilidade',
    'Preocupação', 'Tipo_Interno', 'Ingredientes', 'Efeitos',
]


class Command(BaseCommand):
    help = 'Importa as regras de tratamento a partir do CSV do dataset (Tratamento_Skincare_Dataset_PT.csv)'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)

    def handle(self, *args, **options):
        csv_path = options['csv_path']
        criadas = 0
        atualizadas = 0

        # utf-8-sig: o arquivo vem com BOM, que senão grudaria no nome da 1ª coluna
        try:
            arquivo = open(csv_path, encoding='utf-8-sig')
        except FileNotFoundError:
            raise CommandError(f'Arquivo não encontrado: {csv_path}')

        with arquivo:
            leitor = csv.DictReader(arquivo)
            faltando = [c for c in COLUNAS if c not in (leitor.fieldnames or [])]
            if faltando:
                raise CommandError(f'Colunas que faltam no CSV: {", ".join(faltando)}')

            # tudo ou nada: se uma linha der erro, nenhuma regra fica pela metade no banco
            with transaction.atomic():
                for numero, linha in enumerate(leitor, start=2):  # linha 1 é o cabeçalho
                    sensibilidade = linha['Sensibilidade'].strip().lower()
                    if sensibilidade not in ('sim', 'não'):
                        raise CommandError(f'Linha {numero}: Sensibilidade deve ser Sim ou Não.')

                    tipo_interno = linha['Tipo_Interno'].strip()
                    chave = {
                        'faixa_etaria': linha['Faixa_Etária'].strip(),
                        'tipo_pele': linha['Tipo_de_Pele'].strip().lower(),
                        'subtipo_pele': linha['Subtipo_de_Pele'].strip(),
                        'sensibilidade': sensibilidade == 'sim',
                        'preocupacao': linha['Preocupação'].strip(),
                        'tipo_interno': '' if tipo_interno == 'Geral' else tipo_interno,
                    }
                    defaults = {
                        'ingredientes': linha['Ingredientes'].strip(),
                        'efeitos': linha['Efeitos'].strip(),
                    }
                    _, criada = RegraTratamento.objects.update_or_create(**chave, defaults=defaults)
                    if criada:
                        criadas += 1
                    else:
                        atualizadas += 1

        self.stdout.write(self.style.SUCCESS(
            f'Importação concluída: {criadas} criadas, {atualizadas} atualizadas.'
        ))
