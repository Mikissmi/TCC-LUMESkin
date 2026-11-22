from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser


class UsuarioManager(BaseUserManager):
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
    id_usuario = models.AutoField(primary_key=True)
    nome_usuario = models.CharField(max_length=100)
    email = models.EmailField(max_length=200, unique=True)
    password = models.CharField(max_length=100, db_column='senha_usuario')
    data_cadastro = models.DateTimeField(auto_now_add=True)

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

    gestante = models.BooleanField(default=False)

    TIPO_ACNE_CHOICES = [(t, t) for t in ['Comedonal', 'Inflamatória', 'Nodular', 'Cística']]
    tipo_acne = models.CharField(max_length=20, choices=TIPO_ACNE_CHOICES, blank=True, default='')

    url_mapeamento_facial = models.CharField(max_length=500, blank=True, null=True)
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
    especialidade = models.CharField(max_length=100) 
    crm = models.CharField(max_length=30, blank=True, null=True)
    telefone_whatsapp = models.CharField(max_length=20)  
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

    tipo_pele = models.CharField(max_length=100)
    preocupacao = models.CharField(max_length=255)

    modo_uso = models.CharField(max_length=20, choices=MODO_USO_CHOICES)
    ingrediente_principal = models.CharField(max_length=100)
    fps = models.PositiveSmallIntegerField(blank=True, null=True)
    tamanho_ml = models.PositiveIntegerField()

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
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, db_column='id_usuario')
    data = models.DateField()
    periodo = models.CharField(max_length=10)       
    titulo = models.CharField(max_length=50)      
    produto_id = models.PositiveIntegerField(default=0) 
    marcado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'passo_rotina_feito'
        unique_together = [('usuario', 'data', 'periodo', 'titulo', 'produto_id')]

    def __str__(self):
        return f'{self.data} {self.periodo}: {self.titulo}'


class RegraTratamento(models.Model):
    faixa_etaria = models.CharField(max_length=10)   
    tipo_pele = models.CharField(max_length=10)    
    subtipo_pele = models.CharField(max_length=50)  
    sensibilidade = models.BooleanField()
    preocupacao = models.CharField(max_length=50)   
    tipo_interno = models.CharField(max_length=30, blank=True)  
    ingredientes = models.CharField(max_length=200)
    efeitos = models.TextField(blank=True)

    class Meta:
        db_table = 'regra_tratamento'
        unique_together = [
            ('faixa_etaria', 'tipo_pele', 'subtipo_pele', 'sensibilidade', 'preocupacao', 'tipo_interno'),
        ]

    def __str__(self):
        return f'{self.preocupacao} ({self.faixa_etaria}, {self.tipo_pele})'

    def lista_ingredientes(self):
        return [i.strip() for i in self.ingredientes.split('+') if i.strip()]
