import csv
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from core.models import Produto


class Command(BaseCommand):
    help = 'Importa produtos de skincare a partir do CSV do dataset (produtos_skincare.csv)'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)

    def handle(self, *args, **options):
        csv_path = options['csv_path']
        criados = 0
        atualizados = 0

        try:
            arquivo = open(csv_path, encoding='utf-8')
        except FileNotFoundError:
            raise CommandError(f'Arquivo não encontrado: {csv_path}')

        with arquivo:
            leitor = csv.DictReader(arquivo)
            for linha in leitor:
                defaults = {
                    'marca': linha['marca'],
                    'nome': linha['nome'],
                    'categoria': linha['categoria'],
                    'tipo_pele': linha['tipo_pele'],
                    'preocupacao': linha['preocupacao'],
                    'modo_uso': linha['modo_uso'],
                    'ingrediente_principal': linha['ingrediente_principal'],
                    'fps': int(linha['fps']) if linha['fps'] else None,
                    'tamanho_ml': int(linha['tamanho_ml']),
                    'preco': Decimal(linha['preco_brl']) if linha['preco_brl'] else None,
                    'avaliacao': Decimal(linha['avaliacao']) if linha['avaliacao'] else None,
                    'num_avaliacoes': int(linha['num_avaliacoes']) if linha['num_avaliacoes'] else None,
                    'fonte': linha.get('fonte', ''),
                    'descricao': linha['descricao'],
                    'imagem_url': linha['imagem_url'],
                    'link_produto': linha['link_produto'],
                }
                _, criado = Produto.objects.update_or_create(
                    slug=linha['slug'], defaults=defaults,
                )
                if criado:
                    criados += 1
                else:
                    atualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f'Importação concluída: {criados} criados, {atualizados} atualizados.'
        ))
