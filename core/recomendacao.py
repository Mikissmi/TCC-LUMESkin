import json
import unicodedata
from itertools import zip_longest

from .models import Produto, RegraTratamento
from .youcam_utils import _extract_score

MAPA_TIPO_PELE = {
    'oleosa': 'Oleosa',
    'seca': 'Seca',
    'mista': 'Mista',
    'normal': 'Normal',
}
OBJETIVOS = {
    'hidratar': 'Hidratar',
    'controlar oleosidade': 'Controlar oleosidade',
    'reduzir manchas': 'Reduzir manchas',
    'anti-idade': 'Anti-idade',
    'reduzir acne': 'Reduzir acne',
    'melhorar textura': 'Melhorar textura',
}

OBJETIVOS_ANTIGOS = {
    'hidratar a pele': 'hidratar',
    'prevenir sinais de idade': 'anti-idade',
}

MAPA_OBJETIVO_PREOCUPACAO = {
    'hidratar': {'Hidratação'},
    'controlar oleosidade': {'Oleosidade'},
    'reduzir manchas': {'Manchas'},
    'anti-idade': {'Rugas e Linhas Finas', 'Fotoenvelhecimento'},
    'reduzir acne': {'Acne'},
    'melhorar textura': {'Textura Irregular'},
}

CATEGORIA_POR_PREFERENCIA = {
    'creme': {'Hidratante', 'Contorno de Olhos', 'Máscara Facial'},
    'gel': {'Limpeza', 'Sérum', 'Tônico', 'Água Micelar', 'Esfoliante'},
}

# ==========================================================================
# ROTINA DE TRATAMENTO 
# ==========================================================================

IDADE_MINIMA_TRATAMENTO = 14

FAIXAS_ETARIAS = [(18, '14-18'), (24, '19-24'), (36, '25-36'), (45, '37-45')]
FAIXA_MAIS_VELHA = '45+'

MAPA_OBJETIVO_TRATAMENTO = {
    'reduzir acne': 'Acne',
    'reduzir manchas': 'Manchas Escuras',
    'anti-idade': 'Rugas',
    'controlar oleosidade': 'Poros Dilatados',
    'melhorar textura': 'Cravos Brancos / Cravos Pretos',
}

SINAIS_TRATAMENTO = [
    ('acne_presente', 'Acne'),
    ('manchas_presentes', 'Manchas Escuras'),
    ('textura_irregular', 'Cravos Brancos / Cravos Pretos'),
    ('oleosidade_alta', 'Poros Dilatados'),
]

MAX_PREOCUPACOES = 2  
MAX_TRATAMENTOS = 2 
TIPO_ACNE_PADRAO = 'Comedonal'        
TIPOS_ACNE_GRAVES = {'Nodular', 'Cística'}

IDADE_MINIMA_RETINOIDE = 18
RETINOIDES = {'Retinol', 'Pró-Retinol'}                      
ACIDOS = {'Ácido Salicílico', 'Ácido Glicólico', 'Ácido Lático'} 
ATIVOS_SO_NOITE = RETINOIDES                                  
CONFLITOS = [(RETINOIDES, {'Vitamina C'} | ACIDOS)]
SINONIMOS_ATIVOS = [{'PCA de Zinco', 'Zinco PCA'}]

CATEGORIAS_TRATAMENTO = {'Sérum', 'Esfoliante', 'Tônico'}
CATEGORIAS_COM_ATIVO = CATEGORIAS_TRATAMENTO | {'Limpeza', 'Hidratante'}

HORARIOS = {
    'Manhã': {'limpeza': '07:00', 'tratamento': '07:05', 'hidratante': '07:10', 'protetor': '07:15'},
    'Noite': {'limpeza': '21:00', 'tratamento': '21:05', 'hidratante': '21:10'},
}

AVISO_ROTULO = (
    'Só conhecemos o ingrediente principal de cada produto. '
    'Confira o rótulo antes de usar, principalmente se você tem alergias.'
)
AVISO_MENOR_DE_14 = (
    'Para menores de 14 anos não indicamos produtos com ativos. '
    'Consulte um médico antes de usar qualquer tratamento.'
)


def normalizar_objetivo(objetivo):
    texto = (objetivo or '').strip()
    codigo = OBJETIVOS_ANTIGOS.get(texto.lower(), texto.lower())
    return codigo if codigo in OBJETIVOS else texto


def rotulo_objetivo(objetivo):
    texto = normalizar_objetivo(objetivo)
    return OBJETIVOS.get(texto, texto)


def classificar_indicador(nota):
    if nota >= 85:
        return 'Ótimo'
    if nota >= 70:
        return 'Bom'
    if nota >= 50:
        return 'Regular'
    return 'Atenção'


def extrair_sinais_faciais(dados_ia_raw):
    if not dados_ia_raw:
        return {}
    try:
        dados = json.loads(dados_ia_raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    sinais = {}
    campos = {
        'oleosidade_alta': _extract_score(dados, 'oiliness'),
        'acne_presente': _extract_score(dados, 'acne'),
        'manchas_presentes': _extract_score(dados, 'age_spot'),
        'textura_irregular': _extract_score(dados, 'texture'),
        'baixa_hidratacao': _extract_score(dados, 'moisture'),
    }
    for sinal, nota in campos.items():
        if nota is not None and classificar_indicador(nota) in ('Regular', 'Atenção'):
            sinais[sinal] = True

    return sinais


def tipos_pele_compativeis(perfil, sinais_faciais):
    tipos = {'Todos os tipos'}
    tipo = MAPA_TIPO_PELE.get(perfil.tipo_pele)
    if tipo:
        tipos.add(tipo)
    if perfil.idade and perfil.idade >= 40:
        tipos.add('Madura')
    return tipos


def preocupacoes_do_perfil(perfil, sinais_faciais):
    preocupacoes = set(MAPA_OBJETIVO_PREOCUPACAO.get(normalizar_objetivo(perfil.objetivo), set()))

    if sinais_faciais.get('oleosidade_alta'):
        preocupacoes.add('Oleosidade')
    if sinais_faciais.get('acne_presente'):
        preocupacoes.add('Acne')
    if sinais_faciais.get('manchas_presentes'):
        preocupacoes.add('Manchas')
    if sinais_faciais.get('textura_irregular'):
        preocupacoes.add('Textura Irregular')
    if sinais_faciais.get('baixa_hidratacao'):
        preocupacoes.add('Hidratação')

    return preocupacoes


def pontuar_produto(produto, preocupacoes, categorias_preferidas):
    pontos = 2 * len(preocupacoes & set(produto.preocupacoes()))
    if produto.categoria in categorias_preferidas:
        pontos += 1
    if produto.avaliacao is not None:
        pontos += float(produto.avaliacao) * 0.1
    return pontos


def recomendar_produtos(perfil, limite=20):
    if perfil is None:
        return list(Produto.objects.all()[:limite])

    sinais_faciais = extrair_sinais_faciais(perfil.dados_ia)
    tipos_compat = tipos_pele_compativeis(perfil, sinais_faciais)
    preocupacoes = preocupacoes_do_perfil(perfil, sinais_faciais)
    categorias_preferidas = CATEGORIA_POR_PREFERENCIA.get(perfil.preferencia_produto, set())

    candidatos = [
        produto for produto in Produto.objects.all()
        if set(produto.tipos_pele()) & tipos_compat
    ]

    pontuados = [
        (pontuar_produto(produto, preocupacoes, categorias_preferidas), produto)
        for produto in candidatos
    ]

    pontuados.sort(key=lambda item: item[0], reverse=True)
    return [produto for _, produto in pontuados[:limite]]


# ==========================================================================
# ROTINA DE TRATAMENTO 
# ==========================================================================

def aviso(texto, destaque=False):
    return {'texto': texto, 'destaque': destaque}


def sem_acento(texto):
    decomposto = unicodedata.normalize('NFKD', texto or '')
    return ''.join(c for c in decomposto if not unicodedata.combining(c)).lower()


def faixa_etaria(idade):
    """Faixa do dataset ('14-18', '19-24'...). None abaixo de 14 anos."""
    if idade < IDADE_MINIMA_TRATAMENTO:
        return None
    for limite, faixa in FAIXAS_ETARIAS:
        if idade <= limite:
            return faixa
    return FAIXA_MAIS_VELHA


def preocupacoes_de_tratamento(perfil, sinais_faciais):
    preocupacoes = []
    do_objetivo = MAPA_OBJETIVO_TRATAMENTO.get(normalizar_objetivo(perfil.objetivo))
    if do_objetivo:
        preocupacoes.append(do_objetivo)

    for sinal, preocupacao in SINAIS_TRATAMENTO:
        if len(preocupacoes) >= MAX_PREOCUPACOES:
            break
        if sinais_faciais.get(sinal) and preocupacao not in preocupacoes:
            preocupacoes.append(preocupacao)
    return preocupacoes


def tipo_interno_da_preocupacao(perfil, preocupacao):
    if preocupacao == 'Acne':
        return perfil.tipo_acne or TIPO_ACNE_PADRAO
    return ''



def nomes_do_ativo(ativo):
    for grupo in SINONIMOS_ATIVOS:
        if ativo in grupo:
            return grupo
    return {ativo}


def ativo_nas_alergias(ativo, alergias):
    texto = sem_acento(alergias).strip()
    if texto in ('', '0', '1'):
        return False
    return any(sem_acento(nome) in texto for nome in nomes_do_ativo(ativo))


def motivo_ativo_bloqueado(ativo, perfil):
    if ativo in RETINOIDES:
        if perfil.gestante:
            return 'não é indicado na gravidez'
        if perfil.idade < IDADE_MINIMA_RETINOIDE:
            return f'não é indicado antes dos {IDADE_MINIMA_RETINOIDE} anos'
    if perfil.idade < IDADE_MINIMA_TRATAMENTO and ativo in RETINOIDES | ACIDOS:
        return f'não é indicado antes dos {IDADE_MINIMA_TRATAMENTO} anos'
    if ativo_nas_alergias(ativo, perfil.alergias):
        return 'está nas suas alergias'
    return None


def conflito_entre(ativo, outros):
    for grupo_a, grupo_b in CONFLITOS:
        for outro in outros:
            if (ativo in grupo_a and outro in grupo_b) or (ativo in grupo_b and outro in grupo_a):
                return outro
    return None


def tipos_pele_da_rotina(perfil):
    tipos = set(tipos_pele_compativeis(perfil, {}))
    if perfil.sensibilidade:
        tipos.add('Sensível')
    return tipos


def produto_seguro(produto, perfil, periodo):
    ativo = produto.ingrediente_principal
    if motivo_ativo_bloqueado(ativo, perfil):
        return False
    return not (periodo == 'Manhã' and ativo in ATIVOS_SO_NOITE)


def produtos_compativeis(produtos, perfil, periodo, categorias, ativo=None):
    tipos = tipos_pele_da_rotina(perfil)
    nomes = nomes_do_ativo(ativo) if ativo else None
    return [
        p for p in produtos
        if p.categoria in categorias
        and (nomes is None or p.ingrediente_principal in nomes)
        and set(p.tipos_pele()) & tipos
        and (periodo is None or p.modo_uso in (periodo, 'Manhã e Noite'))
        and produto_seguro(p, perfil, periodo)
    ]


def escolher_produto(produtos, perfil, periodo, categorias, preocupacoes,
                     ativo=None, ativos_desejados=(), ativos_em_uso=()):
    em_uso = set().union(*[nomes_do_ativo(a) for a in ativos_em_uso])
    candidatos = [
        p for p in produtos_compativeis(produtos, perfil, periodo, categorias, ativo)
        if p.ingrediente_principal not in em_uso
        and conflito_entre(p.ingrediente_principal, ativos_em_uso) is None
    ]
    if not candidatos:
        return None

    desejados = set().union(*[nomes_do_ativo(a) for a in ativos_desejados])
    preferidas = CATEGORIA_POR_PREFERENCIA.get(perfil.preferencia_produto, set())
    tipo = MAPA_TIPO_PELE.get(perfil.tipo_pele)
    return max(candidatos, key=lambda p: (
        not (perfil.gestante and p.ingrediente_principal in ACIDOS),
        perfil.sensibilidade and 'Sensível' in p.tipos_pele(),
        p.ingrediente_principal in desejados,
        tipo in p.tipos_pele(),
        pontuar_produto(p, preocupacoes, preferidas),
    ))


def buscar_regra(perfil, preocupacao, tipo_interno, produtos):
    candidatas = RegraTratamento.objects.filter(
        faixa_etaria=faixa_etaria(perfil.idade),
        tipo_pele=perfil.tipo_pele,
        sensibilidade=perfil.sensibilidade,
        preocupacao=preocupacao,
        tipo_interno=tipo_interno,
    ).order_by('id')

    def ativos_com_produto(regra):
        return sum(
            1 for ativo in regra.lista_ingredientes()
            if motivo_ativo_bloqueado(ativo, perfil) is None
            and produtos_compativeis(produtos, perfil, None, CATEGORIAS_COM_ATIVO, ativo)
        )

    return max(candidatas, key=ativos_com_produto, default=None)


def ativos_do_tratamento(perfil, sinais_faciais, produtos):
    listas = []
    avisos = []
    for preocupacao in preocupacoes_de_tratamento(perfil, sinais_faciais):
        tipo_interno = tipo_interno_da_preocupacao(perfil, preocupacao)
        if preocupacao == 'Acne':
            if not perfil.tipo_acne:
                avisos.append(aviso(
                    'Não sabemos o seu tipo de acne: usamos o mais leve (comedonal). '
                    'Procure um dermatologista para confirmar.'))
            if tipo_interno in TIPOS_ACNE_GRAVES:
                avisos.append(aviso(
                    f'Acne {tipo_interno.lower()}: procure um dermatologista. '
                    'Esta rotina é apenas apoio.', destaque=True))

        regra = buscar_regra(perfil, preocupacao, tipo_interno, produtos)
        if regra is None:
            continue
        seguros = []
        for ativo in regra.lista_ingredientes():
            motivo = motivo_ativo_bloqueado(ativo, perfil)
            if motivo is None:
                seguros.append(ativo)
            else:
                avisos.append(aviso(f'{ativo} não entrou na sua rotina: {motivo}.'))
        listas.append(seguros)

    intercalados = [a for grupo in zip_longest(*listas) for a in grupo if a]
    return list(dict.fromkeys(intercalados)), avisos


# --- 5. montagem ---------------------------------------------------------------

def montar_passo(titulo, horario, produto, descricao_generica, detalhe=''):
    descricao = descricao_generica
    if produto:
        descricao = f'{produto}' + (f' ({detalhe})' if detalhe else '')
    return {
        'class': 'pending',
        'title': titulo,
        'description': descricao,
        'time': horario,
        'action': None,
        'icon': None,
        'produto_id': produto.id if produto else None,
    }


def escolher_tratamentos(produtos, perfil, periodo, ativos, preocupacoes):
    escolhidos = []
    avisos = []
    for ativo in ativos:
        if len(escolhidos) == MAX_TRATAMENTOS:
            break
        produto = escolher_produto(produtos, perfil, periodo, CATEGORIAS_TRATAMENTO, preocupacoes, ativo=ativo)
        if produto is None:
            continue
        rival = conflito_entre(ativo, [a for a, _ in escolhidos])
        if rival:
            avisos.append(aviso(
                f'{rival} e {ativo} não devem ser usados juntos no mesmo período: a rotina mantém '
                f'{rival}. Use {ativo} em noites alternadas.'))
            continue
        escolhidos.append((ativo, produto))
    return escolhidos, avisos


def montar_periodo(periodo, perfil, produtos, ativos, com_tratamento, preocupacoes):
    horarios = HORARIOS[periodo]
    tipo = perfil.tipo_pele
    avisos = []

    tratamentos = []
    if com_tratamento:
        tratamentos, avisos = escolher_tratamentos(produtos, perfil, periodo, ativos, preocupacoes)
        if not tratamentos:
            produto = escolher_produto(produtos, perfil, periodo, CATEGORIAS_TRATAMENTO, preocupacoes)
            if produto:
                tratamentos = [(produto.ingrediente_principal, produto)]
    ativos_em_uso = [a for a, _ in tratamentos]
    restantes = [a for a in ativos if a not in ativos_em_uso]

    categorias_limpeza = {'Limpeza', 'Água Micelar'}
    limpeza = None
    if periodo == 'Noite' and perfil.usa_maquiagem_diariamente == 'sim':
        limpeza = escolher_produto(produtos, perfil, periodo, {'Água Micelar'}, preocupacoes,
                                   ativos_desejados=restantes, ativos_em_uso=ativos_em_uso)
    if limpeza is None:
        limpeza = escolher_produto(produtos, perfil, periodo, categorias_limpeza, preocupacoes,
                                   ativos_desejados=restantes, ativos_em_uso=ativos_em_uso)
    if limpeza:
        ativos_em_uso.append(limpeza.ingrediente_principal)

    hidratante = escolher_produto(produtos, perfil, periodo, {'Hidratante'}, preocupacoes,
                                  ativos_desejados=restantes, ativos_em_uso=ativos_em_uso)

    if periodo == 'Manhã':
        passos = [montar_passo('Limpeza', horarios['limpeza'], limpeza, f'Limpeza suave para pele {tipo}')]
    else:
        passos = [montar_passo('Limpeza e demaquilante', horarios['limpeza'], limpeza,
                               f'Demaquilante ou limpeza suave para pele {tipo}')]

    for ativo, produto in tratamentos:
        passos.append(montar_passo('Tratamento', horarios['tratamento'], produto, '', detalhe=ativo))
    if com_tratamento and not tratamentos:
        generica = f'Sérum de tratamento para pele {tipo}'
        if ativos:
            generica += f' (procure um com {", ".join(ativos[:2])})'
        passos.append(montar_passo('Tratamento', horarios['tratamento'], None, generica))

    passos.append(montar_passo('Hidratante', horarios['hidratante'], hidratante, f'Hidratante para pele {tipo}'))

    usados = [p for p in (limpeza, hidratante) if p] + [p for _, p in tratamentos]
    if periodo == 'Manhã':
        protetor = escolher_produto(produtos, perfil, periodo, {'Protetor Solar'}, preocupacoes)
        passos.append(montar_passo('Protetor solar', horarios['protetor'], protetor,
                                   f'Protetor solar para pele {tipo}'))
        if protetor:
            usados.append(protetor)
    return passos, usados, avisos


def montar_rotina(perfil):
    if perfil is None:
        return {'manha': [], 'noite': [], 'avisos': []}

    produtos = list(Produto.objects.order_by('id'))  
    sinais = extrair_sinais_faciais(perfil.dados_ia)
    preocupacoes = preocupacoes_do_perfil(perfil, sinais)
    avisos = []

    com_tratamento = perfil.idade >= IDADE_MINIMA_TRATAMENTO
    ativos = []
    if com_tratamento:
        ativos, avisos_regra = ativos_do_tratamento(perfil, sinais, produtos)
        avisos += avisos_regra
    else:
        avisos.append(aviso(AVISO_MENOR_DE_14, destaque=True))

    manha, usados_manha, avisos_manha = montar_periodo('Manhã', perfil, produtos, ativos, com_tratamento, preocupacoes)
    noite, usados_noite, avisos_noite = montar_periodo('Noite', perfil, produtos, ativos, com_tratamento, preocupacoes)
    avisos += avisos_manha + avisos_noite

    if perfil.gestante:
        for produto in {p.id: p for p in usados_manha + usados_noite}.values():
            if produto.ingrediente_principal in ACIDOS:
                avisos.append(aviso(
                    f'Você está grávida: consulte seu médico antes de usar {produto} '
                    f'({produto.ingrediente_principal}).'))

    if (perfil.alergias or '').strip() == '1':
        avisos.append(aviso(
            'Você indicou que tem alergia, mas não descreveu qual. '
            'Descreva no seu perfil para podermos evitar ingredientes.'))

    sem_repetir = list({a['texto']: a for a in avisos}.values())
    sem_repetir.sort(key=lambda a: not a['destaque'])
    sem_repetir.append(aviso(AVISO_ROTULO))
    return {'manha': manha, 'noite': noite, 'avisos': sem_repetir}
