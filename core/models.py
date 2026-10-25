from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser


class UsuarioManager(BaseUserManager):
    # gerenciador customizado, porque nosso Usuario nao tem o campo "username" padrao do Django
    def create_user(self, email, nome_usuario, password=None):
        if not email:
            raise ValueError('É obrigatório informar um email')

        usuario = self.model(
            email=self.normalize_email(email),
            nome_usuario=nome_usuario,
        )
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario


class Usuario(AbstractBaseUser):
    # esse model substitui o User padrao do Django, pra bater com a tabela "usuario" do nosso banco
    id_usuario = models.AutoField(primary_key=True)
    nome_usuario = models.CharField(max_length=100)
    email = models.EmailField(max_length=200, unique=True)
    password = models.CharField(max_length=100, db_column='senha_usuario')
    data_cadastro = models.DateTimeField(auto_now_add=True)

    # tira o campo last_login que o AbstractBaseUser adiciona sozinho,
    # porque a tabela "usuario" do nosso banco nao tem essa coluna
    last_login = None

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome_usuario']

    objects = UsuarioManager()

    class Meta:
        db_table = 'usuario'

    def __str__(self):
        return self.email


class PerfilDermatologico(models.Model):
    usuario = models.OneToOneField(Usuario, on_delete=models.CASCADE, db_column='id_usuario')

    idade = models.IntegerField()
    TIPO_PELE_CHOICES = [
        ('oleosa', 'Oleosa'),
        ('seca', 'Seca'),
        ('mista', 'Mista'),
        ('normal', 'Normal'),
    ]

    tipo_pele = models.CharField(max_length=10, choices=TIPO_PELE_CHOICES)
    alergias = models.TextField(blank=True, null=True)
    objetivo = models.CharField(max_length=200)
    preferencia_produto = models.CharField(max_length=10, choices=[('creme', 'Creme'), ('gel', 'Gel')])
    usa_maquiagem_diariamente = models.CharField(max_length=10)

    SONO_CHOICES = [
        ('curto', '<5h'),
        ('reduzido', '5-7h'),
        ('adequado', '7-9h'),
        ('longo', '>9h'),
    ]
    sono_horas = models.CharField(max_length=10, choices=SONO_CHOICES, default='adequado')

    PROTETOR_CHOICES = [
        (0, 'Nunca ou raramente'),
        (1, 'Às vezes'),
        (2, 'Todos os dias'),
    ]
    protetor_solar = models.IntegerField(choices=PROTETOR_CHOICES, default=1)

    EXERCICIO_CHOICES = [
        ('nunca', 'Nunca ou raramente'),
        ('1-2x', '1-2x por semana'),
        ('3-4x', '3-4x por semana'),
        ('5x+', '5x ou mais por semana'),
    ]
    exercicio_frequencia = models.CharField(max_length=10, choices=EXERCICIO_CHOICES, default='1-2x')

    # Usadas pela rotina de tratamento (recomendacao.py). Padrao False = quem
    # respondeu o questionario antes dessas perguntas continua funcionando.
    sensibilidade = models.BooleanField(default=False)  # pele sensivel (arde/avermelha com facilidade)
    gestante = models.BooleanField(default=False)

    # Mesmos nomes do dataset de tratamento (coluna Tipo_Interno). Vazio = "nao sei":
    # a rotina usa Comedonal (o mais leve) e avisa para procurar um dermatologista.
    TIPO_ACNE_CHOICES = [(t, t) for t in ['Comedonal', 'Inflamatória', 'Nodular', 'Cística']]
    tipo_acne = models.CharField(max_length=20, choices=TIPO_ACNE_CHOICES, blank=True, default='')

    foto_rosto = models.CharField(max_length=500, blank=True, null=True)
    dados_ia = models.TextField(blank=True, null=True)

    porcentagem_saude = models.IntegerField()
    fototipo = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'perfil_dermatologico'


class Artigo(models.Model):
    titulo = models.CharField(max_length=200)
    autor = models.CharField(max_length=150)
    ano = models.IntegerField()
    resumo = models.TextField(blank=True, null=True)
    url_capa = models.URLField(max_length=300, blank=True, null=True)
    url_leitura = models.URLField(max_length=300)

    class Meta:
        db_table = 'artigo'

    def __str__(self):
        return self.titulo

class Especialista(models.Model):
    nome = models.CharField(max_length=150)
    especialidade = models.CharField(max_length=100)  # ex: dermatologista, esteticista
    crm = models.CharField(max_length=30, blank=True, null=True)
    telefone_whatsapp = models.CharField(max_length=20)  # formato: 5511999999999
    url_foto = models.URLField(max_length=300, blank=True, null=True)

    class Meta:
        db_table = 'especialista'

    def __str__(self):
        return self.nome


class Produto(models.Model):
    CATEGORIA_CHOICES = [(c, c) for c in [
        'Limpeza', 'Água Micelar', 'Tônico', 'Sérum', 'Hidratante',
        'Protetor Solar', 'Esfoliante', 'Máscara Facial', 'Óleo Facial',
        'Contorno de Olhos',
    ]]
    MODO_USO_CHOICES = [(v, v) for v in ['Manhã', 'Noite', 'Manhã e Noite']]

    marca = models.CharField(max_length=100)
    nome = models.CharField(max_length=200)
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES)

    # multivalorados, guardados como no CSV: "Oleosa;Mista", "Acne;Oleosidade"
    tipo_pele = models.CharField(max_length=100)
    preocupacao = models.CharField(max_length=255)

    modo_uso = models.CharField(max_length=20, choices=MODO_USO_CHOICES)
    ingrediente_principal = models.CharField(max_length=100)
    fps = models.PositiveSmallIntegerField(blank=True, null=True)
    tamanho_ml = models.PositiveIntegerField()

    # Ficam em branco ate serem confirmados numa fonte real (site oficial ou
    # loja) - nunca mostrar um preco/avaliacao fabricado como se fosse dado
    # real. "fonte" identifica de onde veio o valor (ex: "Época Cosméticos"),
    # exibido junto do preco/avaliacao na tela para o usuario poder conferir.
    preco = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    avaliacao = models.DecimalField(max_digits=2, decimal_places=1, blank=True, null=True)
    num_avaliacoes = models.PositiveIntegerField(blank=True, null=True)
    fonte = models.CharField(max_length=100, blank=True)

    descricao = models.TextField(blank=True)
    imagem_url = models.URLField(max_length=300, blank=True)
    link_produto = models.URLField(max_length=300, blank=True)
    slug = models.SlugField(max_length=250, unique=True)

    class Meta:
        db_table = 'produto'

    def __str__(self):
        return f'{self.marca} - {self.nome}'

    def tipos_pele(self):
        return [t.strip() for t in self.tipo_pele.split(';') if t.strip()]

    def preocupacoes(self):
        return [p.strip() for p in self.preocupacao.split(';') if p.strip()]


class PassoRotinaFeito(models.Model):
    # Um passo da rotina (ex.: "Limpeza" da manha) que a pessoa marcou como feito em
    # um dia. Existir a linha = feito; desmarcar apaga a linha. As linhas dos dias
    # anteriores ficam guardadas (historico).
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, db_column='id_usuario')
    data = models.DateField()
    periodo = models.CharField(max_length=10)        # 'manha' ou 'noite'
    titulo = models.CharField(max_length=50)         # 'Limpeza', 'Tratamento', 'Hidratante'...
    produto_id = models.PositiveIntegerField(default=0)  # 0 = passo sem produto; sem FK: o produto pode sair do catalogo
    marcado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'passo_rotina_feito'
        # o dia + periodo + passo identificam a marcacao; o "Tratamento" aparece 2x, com produtos diferentes
        unique_together = [('usuario', 'data', 'periodo', 'titulo', 'produto_id')]

    def __str__(self):
        return f'{self.data} {self.periodo}: {self.titulo}'


class RegraTratamento(models.Model):
    # Uma linha do dataset de tratamento: para este perfil (faixa de idade, tipo de pele,
    # sensibilidade...) e esta preocupacao, quais ATIVOS usar. Nao e uma rotina: os passos
    # e horarios quem monta e o recomendacao.py. A coluna "Concentracoes" do CSV nao e
    # importada de proposito (os valores nao sao confiaveis).
    faixa_etaria = models.CharField(max_length=10)   # '14-18', '19-24', '25-36', '37-45', '45+'
    tipo_pele = models.CharField(max_length=10)      # minusculo, igual ao do perfil: 'oleosa'...
    subtipo_pele = models.CharField(max_length=50)   # o perfil nao pergunta; usado so para desempatar
    sensibilidade = models.BooleanField()
    preocupacao = models.CharField(max_length=50)    # 'Acne', 'Manchas Escuras', 'Rugas'...
    tipo_interno = models.CharField(max_length=30, blank=True)  # vazio quando o CSV diz "Geral"

    # ativos separados por " + ", como no CSV: "Niacinamida + Ácido Salicílico + Retinol"
    ingredientes = models.CharField(max_length=200)
    efeitos = models.TextField(blank=True)

    class Meta:
        db_table = 'regra_tratamento'
        # a combinacao identifica a linha; o importador usa isso para nao duplicar
        unique_together = [
            ('faixa_etaria', 'tipo_pele', 'subtipo_pele', 'sensibilidade', 'preocupacao', 'tipo_interno'),
        ]

    def __str__(self):
        return f'{self.preocupacao} ({self.faixa_etaria}, {self.tipo_pele})'

    def lista_ingredientes(self):
        return [i.strip() for i in self.ingredientes.split('+') if i.strip()]
