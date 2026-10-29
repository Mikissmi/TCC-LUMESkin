from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login as auth_login, authenticate, logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages
from functools import wraps
from .models import (
    Artigo,
    Especialista,
    PassoRotinaFeito,
    PerfilDermatologico,
    Produto,
    Usuario,
)
from .recomendacao import (
    OBJETIVOS,
    montar_rotina,
    normalizar_objetivo,
    recomendar_produtos,
    rotulo_objetivo,
    classificar_indicador,
)
from .youcam_utils import _extract_score
import requests
import hmac
import io
import json
import logging
import time
import os
import zipfile
import urllib.request
from urllib.parse import unquote

logger = logging.getLogger(__name__)


# ==========================================================================
# ACESSO DO ADMIN - decorator que protege as telas administrativas
# ==========================================================================

# substitui o @login_required nas telas de administradores: aqui o acesso
# nao depende de um Usuario cadastrado, so da flag de sessao setada no
# login com o email/senha fixos do admin (ver tela_login)
def admin_required(view_func):
    @wraps(view_func)
    def view_wrapper(request, *args, **kwargs):
        if not request.session.get('admin_logado'):
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return view_wrapper


# ==========================================================================
# INTEGRAÇÃO COM A YOUCAM - configuração, envio da foto e espera do resultado
# ==========================================================================

# URLs da YouCam
YOUCAM_FILE_URL = 'https://yce-api-01.makeupar.com/s2s/v2.0/file'
YOUCAM_URL = 'https://yce-api-01.makeupar.com/s2s/v2.1/task/skin-analysis'

# Ações que pedimos à YouCam (consome 9 creditos por analise, independente da quantidade de indicadores)
YOUCAM_ACOES_BASE = ['acne', 'skin_type']
YOUCAM_ACOES_COMPLETAS = ['acne', 'pore', 'texture', 'oiliness', 'moisture', 'age_spot', 'skin_type']


# cabeçalho da autenticação; lê a chave do arquivo .env
def _youcam_headers():
    api_key = os.getenv('YOUCAM_API_KEY') or getattr(settings, 'YOUCAM_API_KEY', '')
    if not api_key:
        raise RuntimeError('YOUCAM_API_KEY não foi configurada no arquivo .env.')
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


# registra o erro da YouCam (HTTP + detalhe) no log
def _youcam_error(response, etapa):
    try:
        detalhe = response.json()
    except ValueError:
        detalhe = response.text
    logger.error('[YouCam] %s - HTTP %s: %s', etapa, response.status_code, detalhe)


# ==========================================================================
# LEITURA DO RESULTADO DA YOUCAM - extrai notas e imagens do json
# ==========================================================================

# o json muda de formato conforme o recurso (e o plano), entao nao da pra
# apontar um caminho fixo: as funcoes abaixo procuram o valor em qualquer nivel
SUFIXOS_IMAGEM = ('.png', '.jpg', '.jpeg', '.webp')
INDICADORES_LIDOS = ('oiliness', 'moisture', 'acne', 'pore', 'texture', 'age_spot')


# corta a URL no ? para ver so o nome do arquivo (URLs assinadas tem parametros)
def _caminho_da_url(valor):
    return valor.split('?', 1)[0].lower() if isinstance(valor, str) else ''


# procura uma URL de IMAGEM em qualquer nivel do JSON (ignora .zip/.json)
def _extract_url(value):
    if isinstance(value, str):
        if value.startswith('https://') and _caminho_da_url(value).endswith(SUFIXOS_IMAGEM):
            return value
    elif isinstance(value, dict):
        for key in ('overlay_image_url', 'result_image_url', 'image_url'):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(('http://', 'https://')) \
                    and not _caminho_da_url(candidate).endswith(('.zip', '.json')):
                return candidate
        for item in value.values():
            found = _extract_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_url(item)
            if found:
                return found
    return None


# verifica se algum dos indicadores lidos tem nota (ou seja, se a YouCam mediu algum score)
def _tem_algum_score(resultados):
    return any(_extract_score(resultados, chave) is not None for chave in INDICADORES_LIDOS)


# URLs https de arquivos .zip em qualquer nivel do JSON
def _achar_zips(value):
    achados = []
    if isinstance(value, str):
        if value.startswith('https://') and _caminho_da_url(value).endswith('.zip'):
            achados.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            achados.extend(_achar_zips(item))
    elif isinstance(value, list):
        for item in value:
            achados.extend(_achar_zips(item))
    return achados


# baixa o zip de resultados da YouCam e le o score_info.json dentro dele (ate 20 MB)
def _ler_score_info_do_zip(url):
    try:
        resposta = requests.get(url, timeout=30)
        if not resposta.ok or len(resposta.content) > 20 * 1024 * 1024:
            logger.warning(f'[YouCam] Não consegui baixar o zip de resultados (HTTP {resposta.status_code}).')
            return None
        with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo:
            for nome in arquivo.namelist():
                if nome.lower().endswith('score_info.json'):
                    return json.loads(arquivo.read(nome).decode('utf-8'))
        logger.warning('[YouCam] O zip de resultados não tem score_info.json.')
    except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
        logger.warning(f'[YouCam] Erro ao ler o zip de resultados: {exc}')
    return None


# garante um dict e, se os scores nao vierem no JSON, busca-os no .zip de resultados
def _completar_resultados(resultados):
    if not isinstance(resultados, dict):
        resultados = {'output': resultados}
    if not _tem_algum_score(resultados):
        for url in _achar_zips(resultados):
            info = _ler_score_info_do_zip(url)
            if info is not None:
                resultados['score_info'] = info
                break
    return resultados


# mostra (e, em DEBUG, grava em arquivo) a resposta crua quando nenhum score foi reconhecido
def _guardar_resposta_para_diagnostico(resultados):
    texto = json.dumps(resultados, ensure_ascii=False, indent=2, default=str)
    logger.warning('[YouCam] Nenhum score reconhecido na resposta. Resposta recebida:')
    logger.warning('%s', texto[:6000])
    if settings.DEBUG:
        try:
            with open(os.path.join(settings.BASE_DIR, 'youcam_ultima_resposta.json'), 'w', encoding='utf-8') as arq:
                arq.write(texto)
            logger.warning('[YouCam] Resposta completa gravada em youcam_ultima_resposta.json')
        except OSError as exc:
            logger.warning(f'[YouCam] Não consegui gravar o arquivo de diagnóstico: {exc}')


# ==========================================================================
# FLUXO COMPLETO DA ANÁLISE - criar a tarefa, esperar e devolver os resultados
# ==========================================================================

# cria a tarefa de analise com os recursos `acoes` e espera o resultado.
# Devolve (resultados, motivo). motivo: 'ok', 'recusada' (a YouCam rejeitou a tarefa),
# 'erro_na_tarefa', 'sem_task_id', 'sem_resultados' ou 'tempo_esgotado'
def _analisar_na_youcam(headers, file_id, acoes):
    task_payload = {
        'src_file_id': file_id,
        'dst_actions': list(acoes),
        'miniserver_args': {
            'enable_mask_overlay': True,
        },
        'format': 'json',
        'pf_camera_kit': False,
    }

    resposta_task = requests.post(
        YOUCAM_URL,
        headers=headers,
        json=task_payload,
        timeout=30,
    )

    if not resposta_task.ok:
        _youcam_error(resposta_task, 'Criação da tarefa Skin Analysis')
        return None, 'recusada'

    task_id = resposta_task.json().get('data', {}).get('task_id')
    if not task_id:
        logger.warning(f'[YouCam] Tarefa criada sem task_id: {resposta_task.text}')
        return None, 'sem_task_id'

    # Consulta o resultado. A API é assíncrona.
    for tentativa in range(30):
        time.sleep(2)

        checagem = requests.get(
            f'{YOUCAM_URL}/{task_id}',
            headers=headers,
            timeout=20,
        )

        if not checagem.ok:
            _youcam_error(checagem, f'Consulta da tarefa (tentativa {tentativa + 1})')
            continue

        payload = checagem.json()
        data = payload.get('data', {})
        status = data.get('task_status')

        logger.debug(f'[YouCam] tarefa={task_id} status={status}')

        if status == 'success':
            resultados = data.get('results')
            if not resultados:
                logger.warning(f'[YouCam] Tarefa concluída, mas sem results: {payload}')
                return None, 'sem_resultados'

            # Guarda a URL do resultado, caso exista, para o mapeamento facial.
            if isinstance(resultados, dict):
                resultado_url = _extract_url(resultados)
                if resultado_url and 'result_image_url' not in resultados:
                    resultados['result_image_url'] = resultado_url

            return resultados, 'ok'

        if status == 'error':
            logger.warning(f'[YouCam] Tarefa terminou com erro: {payload}')
            return None, 'erro_na_tarefa'

    logger.warning('[YouCam] Tempo limite aguardando o resultado da análise.')
    return None, 'tempo_esgotado'


# envia uma imagem local para a YouCam. Passos:
# 1- File API -> recebe file_id + URL assinada
# 2- PUT da imagem na URL assinada
# 3- cria a tarefa Skin Analysis v2.1 usando src_file_id
# 4- faz polling ate success/error
# Devolve o dict de resultados ou None se algo falhar.
def comunicar_youcam_api(foto_arquivo):
    try:
        headers = _youcam_headers()

        # Lê a imagem uma única vez e mantém os bytes disponíveis para o PUT.
        foto_arquivo.seek(0)
        conteudo = foto_arquivo.read()
        if not conteudo:
            logger.warning('[YouCam] A imagem recebida está vazia.')
            return None

        content_type = foto_arquivo.content_type or 'image/jpeg'
        if content_type == 'image/jpg':
            content_type = 'image/jpeg'

        # 1. Solicita URL de upload e file_id.
        file_payload = {
            'files': [{
                'content_type': content_type,
                'file_name': foto_arquivo.name or 'scan.jpg',
                'file_size': len(conteudo),
            }]
        }

        resposta_file = requests.post(
            YOUCAM_FILE_URL,
            headers=headers,
            json=file_payload,
            timeout=30,
        )

        if not resposta_file.ok:
            _youcam_error(resposta_file, 'File API')
            return None

        file_data = resposta_file.json().get('data', {}).get('files', [])
        if not file_data:
            logger.warning(f'[YouCam] File API não retornou files: {resposta_file.text}')
            return None

        file_info = file_data[0]
        file_id = file_info.get('file_id')
        requests_info = file_info.get('requests') or []
        upload_request = next((item for item in requests_info if item.get('method', 'PUT').upper() == 'PUT'), None)
        upload_url = upload_request.get('url') if upload_request else None

        if not file_id or not upload_url:
            logger.warning(f'[YouCam] File API não retornou file_id/upload URL: {file_info}')
            return None

        # 2. Envia a imagem para a URL assinada.
        upload_headers = dict(upload_request.get('headers') or {})
        upload_headers['Content-Type'] = content_type
        upload_headers['Content-Length'] = str(len(conteudo))

        resposta_upload = requests.put(
            upload_url,
            headers=upload_headers,
            data=conteudo,
            timeout=60,
        )

        if not resposta_upload.ok:
            _youcam_error(resposta_upload, 'Upload da imagem')
            return None

        # 3 e 4. Cria a tarefa e espera o resultado. Primeiro pede todos os recursos que o
        # scanner mostra; se a YouCam recusar (plano ou nome de recurso), repete so com os
        # basicos para a analise nao parar de funcionar.
        resultados, motivo = _analisar_na_youcam(headers, file_id, YOUCAM_ACOES_COMPLETAS)

        if resultados is None and motivo in ('recusada', 'erro_na_tarefa'):
            logger.warning('[YouCam] Tentando de novo só com os recursos básicos.')
            resultados, motivo = _analisar_na_youcam(headers, file_id, YOUCAM_ACOES_BASE)

        return resultados

    except requests.RequestException as exc:
        logger.warning(f'[Erro de conexão YouCam API]: {exc}')
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(f'[Erro ao interpretar resposta YouCam API]: {exc}')
    except Exception as exc:
        logger.warning(f'[Erro YouCam API]: {exc}')

    return None


# ==========================================================================
# SCORE DE SAÚDE DA PELE - calculo feito so com as respostas do questionario
# ==========================================================================

# score de saude (0 a 100): media ponderada das respostas do questionario.
# cada resposta vira uma nota e os pesos somam 1.0, entao o resultado
# tambem fica entre 0 e 100. se a resposta vier invalida, o .get() usa 70
PESO_FOTOTIPO = 0.40
PESO_TIPO_PELE = 0.30
PESO_MAQUIAGEM = 0.15
PESO_ALERGIA = 0.15

# Nota de 0 a 100 pra cada resposta possivel
NOTAS_FOTOTIPO = {
    1: 20,   # sempre queima, nunca bronzeia == pouca protecao natural contra UV
    2: 45,   # sempre queima, bronzeia pouco
    3: 75,   # queima moderado, bronzeia gradualmente
    4: 85,   # raramente queima == mais protecao natural contra UV
    5: 95,   # quase nunca queima == muita protecao natural contra UV
    6: 100,  # nunca queima (fototipo VI) == maxima protecao natural contra UV
}

NOTAS_TIPO_PELE = {
    'normal': 100,  # pele equilibrada
    'mista': 75,    # duas tendencias ao mesmo tempo (oleosa na zona T, seca nas bochechas)
    'seca': 60,     # barreira cutanea mais fragil, mais sensivel a irritacao
    'oleosa': 55,   # mais producao de sebo, mais tendencia a cravos e acne
}

NOTA_MAQUIAGEM_SIM = 50
NOTA_MAQUIAGEM_NAO = 100

NOTA_ALERGIA_SIM = 60
NOTA_ALERGIA_NAO = 100


# calcula o score so com as respostas do questionario (sem depender da ia)
def calcular_porcentagem_saude(tipo_pele, fototipo, usa_maquiagem, tem_alergia):
    nota_fototipo = NOTAS_FOTOTIPO.get(fototipo, 70)
    nota_tipo_pele = NOTAS_TIPO_PELE.get(tipo_pele, 70)
    nota_maquiagem = NOTA_MAQUIAGEM_SIM if usa_maquiagem == 'sim' else NOTA_MAQUIAGEM_NAO
    nota_alergia = NOTA_ALERGIA_SIM if tem_alergia == '1' else NOTA_ALERGIA_NAO

    score = (
        nota_fototipo * PESO_FOTOTIPO +
        nota_tipo_pele * PESO_TIPO_PELE +
        nota_maquiagem * PESO_MAQUIAGEM +
        nota_alergia * PESO_ALERGIA
    )

    return round(score)


# ==========================================================================
# VALIDAÇÕES - checagem de foto enviada e conversão segura de números
# ==========================================================================

TAMANHO_MAX_FOTO = 5 * 1024 * 1024  # 5 MB (mesmo limite do formulario)


# descobre o tipo REAL da imagem pelos primeiros bytes (nao confia no que o navegador informa)
def _tipo_imagem(arquivo):
    arquivo.seek(0)
    inicio = arquivo.read(12)
    arquivo.seek(0)
    if inicio.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if inicio.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if inicio[:4] == b'RIFF' and inicio[8:12] == b'WEBP':
        return 'image/webp'
    return None


# True se for JPG/PNG/WebP de ate 5 MB. Ajusta o content_type para o tipo real da imagem
def _foto_valida(arquivo):
    if arquivo.size > TAMANHO_MAX_FOTO:
        return False
    tipo = _tipo_imagem(arquivo)
    if tipo is None:
        return False
    arquivo.content_type = tipo
    return True


# converte para int sem estourar erro 500 quando o valor vem vazio, texto ou fora da faixa
def _inteiro(valor, padrao=0, minimo=None, maximo=None):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    if (minimo is not None and numero < minimo) or (maximo is not None and numero > maximo):
        return padrao
    return numero


# ==========================================================================
# AUTENTICAÇÃO - cadastro, login e logout
# ==========================================================================

def _erro_no_cadastro(nome, email, senha):
    # None se os dados servem para criar a conta; senao, a mensagem pro usuario
    if not nome or not email or not senha:
        return 'Preencha nome, e-mail e senha.'
    if Usuario.objects.filter(email__iexact=email).exists():
        return 'Já existe uma conta com este e-mail.'
    try:
        validate_password(senha, user=Usuario(email=email, nome_usuario=nome))
    except ValidationError as erro:
        return ' '.join(erro.messages)
    return None


def tela_cadastro(request):
    # verifica se o navegador está enviando dados através de um formulário (POST)
    if request.method == "POST":
        nome = request.POST.get('nome', '').strip()
        email = request.POST.get('email', '').strip()
        senha = request.POST.get('senha', '')

        erro = _erro_no_cadastro(nome, email, senha)
        if erro:
            return render(request, 'core/cadastro.html', {
                'erro_cadastro': erro,
                'nome': nome,
                'email': email,
            })

        # cria e salva um novo registro na tabela usuario, já com a senha criptografada
        novo_usuario = Usuario.objects.create_user(
            email=email,
            nome_usuario=nome,
            password=senha,
        )

        # faz o login automático e manda o usuário para a tela do questionário
        auth_login(request, novo_usuario)
        return redirect('questionario')

    # se a requisição NÃO for POST, exibe a tela com o formulário de cadastro limpo.
    return render(request, 'core/cadastro.html')


# compara com o email/senha do admin (hmac.compare_digest nao vaza, pelo tempo de
# resposta, quantos caracteres estavam certos)
def _eh_login_do_admin(email, senha):
    email_certo = hmac.compare_digest((email or '').encode(), settings.ADMIN_LOGIN_EMAIL.encode())
    senha_certa = hmac.compare_digest((senha or '').encode(), settings.ADMIN_LOGIN_SENHA.encode())
    return email_certo and senha_certa


def tela_login(request):
    if request.method == "POST":
        email = request.POST.get('email')
        senha = request.POST.get('senha')

        # login do administrador: mesma tela/form, mas com email e senha fixos
        # (definidos em ADMIN_LOGIN_EMAIL / ADMIN_LOGIN_SENHA) em vez de um
        # Usuario cadastrado no banco
        if _eh_login_do_admin(email, senha):
            request.session['admin_logado'] = True
            return redirect('administradores')

        usuario = authenticate(request, username=email, password=senha)

        if usuario is not None:
            auth_login(request, usuario)
            return redirect('dashboard')
        else:
            return render(request, 'core/cadastro.html', {'erro': 'E-mail ou senha incorretos.'})

    return render(request, 'core/cadastro.html')


def tela_logout(request):
    # so desloga por POST (evita logout por link)
    if request.method == "POST":
        auth_logout(request)
    return redirect('login')


# ==========================================================================
# QUESTIONÁRIO E DASHBOARD - coleta das respostas, calculo do score e tela inicial
# ==========================================================================


def tela_questionario(request):
    if request.method == "POST":
        # sem login nao ha perfil pra salvar (e a foto gastaria credito da YouCam a toa)
        if not request.user.is_authenticated:
            return redirect('login')

        # Pega as respostas do questionário
        idade = _inteiro(request.POST.get('idade'), 0, 0, 120)
        tipo_pele = request.POST.get('tipo_pele')
        alergias = request.POST.get('alergias')
        descricao_alergia = request.POST.get('descricaoalergia', '').strip()
        maquiagem = request.POST.get('maquiagem')
        pontos_sol = _inteiro(request.POST.get('reacao_sol'), 0, 0, 5)
        base_produto = request.POST.get('base_produto')
        objective = request.POST.get('objetivo')

        if objective == 'outro':
            objective = (
                request.POST.get('objetivo_outro', '').strip()[:100]
                or 'outro'
            )

        if base_produto == 'gel-creme':
            base_produto = 'gel'

        alergias_salvas = (
            descricao_alergia[:255]
            if alergias == '1' and descricao_alergia
            else alergias
        )

        quer_escanear = request.POST.get('quer_escanear') == 'sim'
        consentimento_foto = request.POST.get('consentimento_foto') == '1'
        usa_maquiagem = 'sim' if maquiagem == '1' else 'nao'

        dados_ia_json = None
        foto_salva = None
        resultados_ia = None
        redirecionar_para_scanner = False
        sono_horas = request.POST.get('sono_horas')
        exercicio_frequencia = request.POST.get('exercicio_frequencia')
        protetor_solar = request.POST.get('protetor_solar')
        sensibilidade = request.POST.get('sensibilidade')
        gestante = request.POST.get('gestante')
        tipo_acne = request.POST.get('tipo_acne')

        # Calcula a porcentagem usando as respostas do questionário
        porcentagem_regras = calcular_porcentagem_saude(
            tipo_pele=tipo_pele,
            fototipo=pontos_sol + 1,
            usa_maquiagem=usa_maquiagem,
            tem_alergia=alergias,
        )
        porcentagem_calculada = porcentagem_regras

        # Faz a análise facial somente com consentimento
        if quer_escanear and consentimento_foto:
            foto_usuario = request.FILES.get('foto_rosto')

            # mesmo limite por hora do scanner: cada analise gasta credito da YouCam
            if foto_usuario and _foto_valida(foto_usuario) and _passou_limite_scanner(request.user):
                resultados_ia = comunicar_youcam_api(foto_usuario)

        # Processa os resultados somente se a IA retornou dados
        if resultados_ia:
            resultados_ia = _completar_resultados(resultados_ia)
            dados_ia_json = json.dumps(resultados_ia)
            foto_salva = _url_https(_extract_url(resultados_ia))

            # Monta os indicadores para a tela do scanner
            indicadores = [
                montar_indicador(
                    'oleosidade',
                    _extract_score(resultados_ia, 'oiliness')
                ),
                montar_indicador(
                    'hidratacao',
                    _extract_score(resultados_ia, 'moisture')
                ),
                montar_indicador(
                    'acne_poros',
                    _media_ou_none(
                        _extract_score(resultados_ia, 'acne'),
                        _extract_score(resultados_ia, 'pore')
                    )
                ),
                montar_indicador(
                    'textura',
                    _extract_score(resultados_ia, 'texture')
                ),
                montar_indicador(
                    'manchas',
                    _extract_score(resultados_ia, 'age_spot')
                ),
            ]

            notas_disponiveis = [
                indicador['nota']
                for indicador in indicadores
                if indicador['nota'] is not None
            ]

            score_geral = (
                round(
                    sum(notas_disponiveis) / len(notas_disponiveis)
                )
                if notas_disponiveis
                else None
            )

            request.session['resultado_scanner_inicial'] = {
                'score_geral': score_geral,
                'indicadores': indicadores,
                'mapeamento_facial': foto_salva,
            }

            redirecionar_para_scanner = True

            # A IA contribui com 50% do score, usando os dados disponíveis
            porcentagem_ia = _media_ou_none(
                _extract_score(resultados_ia, 'acne'),
                _extract_score(resultados_ia, 'pore'),
                _extract_score(resultados_ia, 'oiliness'),
            )

            if porcentagem_ia is not None:
                porcentagem_calculada = round(
                    (porcentagem_ia + porcentagem_regras) / 2
                )

        # Salva ou atualiza o perfil
        if request.user.is_authenticated:
            perfil, _ = PerfilDermatologico.objects.get_or_create(
                usuario=request.user,
                defaults={
                    'idade': idade,
                    'tipo_pele': tipo_pele or 'normal',
                    'alergias': alergias_salvas or '',
                    'objetivo': objective or 'Melhorar a pele',
                    'preferencia_produto': (
                        base_produto
                        if base_produto in ('creme', 'gel')
                        else 'gel'
                    ),
                    'usa_maquiagem_diariamente': usa_maquiagem,
                    'porcentagem_saude': porcentagem_calculada,
                    'fototipo': pontos_sol + 1,
                    'dados_ia': dados_ia_json,
                    'foto_rosto': foto_salva,
                    'sono_horas': sono_horas if sono_horas in dict(PerfilDermatologico.SONO_CHOICES) else 'adequado',
                    'exercicio_frequencia': (
                        exercicio_frequencia
                        if exercicio_frequencia in dict(PerfilDermatologico.EXERCICIO_CHOICES)
                        else '1-2x'
                    ),
                    'protetor_solar': _inteiro(protetor_solar, 1, 0, 2),
                    'sensibilidade': sensibilidade == '1',
                    'gestante': gestante == '1',
                    'tipo_acne': (
                        tipo_acne
                        if objective == 'reduzir acne'
                        and tipo_acne in dict(PerfilDermatologico.TIPO_ACNE_CHOICES)
                        else ''
                    ),
                }
            )

            perfil.idade = idade or perfil.idade
            perfil.tipo_pele = tipo_pele or perfil.tipo_pele
            perfil.alergias = alergias_salvas or perfil.alergias
            perfil.usa_maquiagem_diariamente = usa_maquiagem
            perfil.fototipo = pontos_sol + 1
            perfil.objetivo = objective or perfil.objetivo

            if base_produto in ('creme', 'gel'):
                perfil.preferencia_produto = base_produto

            perfil.porcentagem_saude = porcentagem_calculada

            if sono_horas in dict(PerfilDermatologico.SONO_CHOICES):
                perfil.sono_horas = sono_horas
            if exercicio_frequencia in dict(PerfilDermatologico.EXERCICIO_CHOICES):
                perfil.exercicio_frequencia = exercicio_frequencia
            if protetor_solar is not None:
                protetor_valido = _inteiro(protetor_solar, None, 0, 2)
                if protetor_valido is not None:
                    perfil.protetor_solar = protetor_valido

            if 'sensibilidade' in request.POST:
                perfil.sensibilidade = sensibilidade == '1'
            if 'gestante' in request.POST:
                perfil.gestante = gestante == '1'
            if objective == 'reduzir acne' and tipo_acne in dict(PerfilDermatologico.TIPO_ACNE_CHOICES):
                perfil.tipo_acne = tipo_acne
            elif objective and objective != 'reduzir acne':
                perfil.tipo_acne = ''

            # Só substitui a análise anterior se houve um novo scan
            if dados_ia_json:
                perfil.dados_ia = dados_ia_json
                perfil.foto_rosto = foto_salva

            perfil.save()

            # Redireciona conforme o fluxo realizado
            if redirecionar_para_scanner:
                return redirect('scanner')

            return redirect('dashboard')

    return render(
        request,
        'core/questionario.html',
        {'tipos_acne': PerfilDermatologico.TIPO_ACNE_CHOICES},
    )



def dashboard_view(request):
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    rotina_manha = []
    rotina_noite = []
    avisos = []
    aviso_rotulo = (
        'Só conhecemos o ingrediente principal de cada produto. '
        'Confira o rótulo antes de usar, principalmente se você tem alergias.'
    )
    if perfil:
        rotina = montar_rotina(perfil)
        avisos = [
            aviso for aviso in rotina['avisos']
            if 'Confira o rótulo antes de usar' not in aviso['texto']
        ]
        marcados = set(
            PassoRotinaFeito.objects.filter(
                usuario=request.user,
                data=timezone.localdate(),
            ).values_list('periodo', 'titulo', 'produto_id')
        )

        def preparar_passos(passos, periodo):
            preparados = []
            proximo_definido = False
            for passo in passos:
                produto_id = passo['produto_id']
                chave_produto = produto_id or 0
                feito = (periodo, passo['title'], chave_produto) in marcados
                if feito:
                    estado = 'completed'
                elif not proximo_definido:
                    estado = 'action-required'
                    proximo_definido = True
                else:
                    estado = 'pending'
                preparados.append({
                    **passo,
                    'class': estado,
                    'feito': feito,
                    'action': 'Marcar como feito' if estado == 'action-required' else None,
                    'icon': 'fa-check' if feito else None,
                    'url': (
                        reverse('produto_detalhe', args=[produto_id])
                        if produto_id else None
                    ),
                })
            return preparados

        rotina_manha = preparar_passos(rotina['manha'], 'manha')
        rotina_noite = preparar_passos(rotina['noite'], 'noite')

    def resumo(passos):
        feitos = sum(passo['feito'] for passo in passos)
        total = len(passos)
        return {
            'feitos': feitos,
            'total': total,
            'porcentagem': round(feitos * 100 / total) if total else 0,
        }

    context = {
        'perfil': perfil,
        'rotina_manha': rotina_manha,
        'rotina_noite': rotina_noite,
        'resumo_manha': resumo(rotina_manha),
        'resumo_noite': resumo(rotina_noite),
        'avisos': avisos,
        'aviso_rotulo': aviso_rotulo,
        'fototipo': _fototipo_do_perfil(perfil),
        'objetivo_rotulo': rotulo_objetivo(perfil.objetivo) if perfil else '',
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def rotina_marcar(request):
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)

    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    if not perfil:
        return JsonResponse({'erro': 'Responda o questionário antes de marcar a rotina.'}, status=400)

    periodo = request.POST.get('periodo')
    titulo = request.POST.get('titulo')
    feito = request.POST.get('feito')
    produto_id = _inteiro(request.POST.get('produto_id'), None, 0)
    if periodo not in ('manha', 'noite') or feito not in ('0', '1') or produto_id is None:
        return JsonResponse({'erro': 'Passo de rotina inválido.'}, status=400)

    rotina = montar_rotina(perfil)
    passos = rotina['manha' if periodo == 'manha' else 'noite']
    passo_valido = any(
        passo['title'] == titulo and (passo['produto_id'] or 0) == produto_id
        for passo in passos
    )
    if not passo_valido:
        return JsonResponse({'erro': 'Este passo não existe na sua rotina.'}, status=400)

    chave = {
        'usuario': request.user,
        'data': timezone.localdate(),
        'periodo': periodo,
        'titulo': titulo,
        'produto_id': produto_id,
    }
    if feito == '1':
        PassoRotinaFeito.objects.get_or_create(**chave)
        return JsonResponse({'ok': True, 'feito': True})

    PassoRotinaFeito.objects.filter(**chave).delete()
    return JsonResponse({'ok': True, 'feito': False})


# ==========================================================================
# SCANNER DE PELE POR IA - endpoints usados pelo JS da core/scanner.html
# ==========================================================================

DESCRICOES_INDICADOR = {
    'oleosidade': {
        'Ótimo': 'Oleosidade sob controle.',
        'Bom': 'Nível de oleosidade equilibrado.',
        'Regular': 'Zona T pode precisar de atenção.',
        'Atenção': 'Oleosidade elevada, considere um produto de controle.',
    },
    'hidratacao': {
        'Ótimo': 'Hidratação excelente.',
        'Bom': 'Nível de hidratação adequado.',
        'Regular': 'Pele levemente desidratada.',
        'Atenção': 'Baixa hidratação, reforce o uso de hidratante.',
    },
    'acne_poros': {
        'Ótimo': 'Poucos sinais de acne ou poros dilatados.',
        'Bom': 'Poros e acne sob controle.',
        'Regular': 'Alguns poros dilatados e pequenas inflamações.',
        'Atenção': 'Sinais de acne e poros dilatados precisam de cuidado.',
    },
    'textura': {
        'Ótimo': 'Textura uniforme e lisa.',
        'Bom': 'Boa textura geral da pele.',
        'Regular': 'Leve irregularidade na textura.',
        'Atenção': 'Textura irregular, considere uma esfoliação leve.',
    },
    'manchas': {
        'Ótimo': 'Tonalidade bem uniforme.',
        'Bom': 'Poucas manchas visíveis.',
        'Regular': 'Algumas manchas de sol ou idade.',
        'Atenção': 'Manchas visíveis, reforce o uso de protetor solar.',
    },
}


def montar_indicador(chave, nota):
    # sem nota = a youcam nao mediu esse indicador nessa foto
    if nota is None:
        return {
            'chave': chave,
            'nota': None,
            'classificacao': None,
            'descricao': 'Não foi possível medir este indicador nesta foto.',
        }

    nota = max(0, min(100, round(nota)))
    classificacao = classificar_indicador(nota)
    descricao = DESCRICOES_INDICADOR[chave][classificacao]
    return {'chave': chave, 'nota': nota, 'classificacao': classificacao, 'descricao': descricao}


def _media_ou_none(*notas):
    # media das notas que existem (None se nao houver nenhuma)
    validas = [nota for nota in notas if nota is not None]
    return sum(validas) / len(validas) if validas else None


def _url_https(valor):
    # so aceita https (o link vai pro src da imagem)
    return valor if isinstance(valor, str) and valor.startswith('https://') else None


def _passou_limite_scanner(usuario):
    # cada analise gasta credito da youcam, entao limita a 3 por usuario por hora (usa o cache)
    chave = f'scanner_analises_{usuario.pk}'
    cache.add(chave, 0, 3600)
    try:
        usadas = cache.incr(chave)
    except ValueError:
        cache.set(chave, 1, 3600)
        usadas = 1
    return usadas <= 3


def scanner_analisar(request):
    # recebe a foto do scanner e devolve a analise em json
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)

    # sem @login_required porque o js espera json, nao um redirect
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Faça login para usar o scanner.'}, status=401)

    # foto do rosto e dado sensivel: so com consentimento
    if request.POST.get('consentimento') != '1':
        return JsonResponse({'erro': 'É preciso aceitar o consentimento para analisar a foto.'}, status=400)

    foto = request.FILES.get('foto')
    if not foto:
        return JsonResponse({'erro': 'Nenhuma imagem enviada.'}, status=400)

    if not _foto_valida(foto):
        return JsonResponse({'erro': 'Envie uma imagem JPG, PNG ou WebP de até 5 MB.'}, status=400)

    if not _passou_limite_scanner(request.user):
        return JsonResponse({'erro': 'Você atingiu o limite de 3 análises por hora. Tente novamente mais tarde.'}, status=429)

    resultados_ia = comunicar_youcam_api(foto)

    if not resultados_ia:
        return JsonResponse(
            {
                'erro': 'Não foi possível analisar a imagem agora. '
                        'Tente novamente em instantes.'
            },
            status=502
        )
    resultados_ia = _completar_resultados(resultados_ia)

    # sem nota fica None (nota 0 e valida)
    nota_oleosidade = _extract_score(resultados_ia, 'oiliness')
    nota_hidratacao = _extract_score(resultados_ia, 'moisture')
    nota_acne = _extract_score(resultados_ia, 'acne')
    nota_poros = _extract_score(resultados_ia, 'pore')
    nota_textura = _extract_score(resultados_ia, 'texture')
    nota_manchas = _extract_score(resultados_ia, 'age_spot')

    indicadores = [
        montar_indicador('oleosidade', nota_oleosidade),
        montar_indicador('hidratacao', nota_hidratacao),
        montar_indicador('acne_poros', _media_ou_none(nota_acne, nota_poros)),
        montar_indicador('textura', nota_textura),
        montar_indicador('manchas', nota_manchas),
    ]

    notas_disponiveis = [indicador['nota'] for indicador in indicadores if indicador['nota'] is not None]
    if not notas_disponiveis:
        _guardar_resposta_para_diagnostico(resultados_ia)
        return JsonResponse({'erro': 'A análise não retornou nenhum indicador para esta foto. Tente outra foto.'}, status=502)

    score_geral = round(sum(notas_disponiveis) / len(notas_disponiveis))
    mapeamento_facial = _url_https(_extract_url(resultados_ia))

    # guarda o resultado na sessao ate o usuario confirmar o salvamento
    request.session['ultimo_scan'] = {
        'dados_ia': json.dumps(resultados_ia),
        'foto_rosto': mapeamento_facial,
        'porcentagem_saude': score_geral,
        'consentimento_em': time.time(),
    }

    return JsonResponse({
        'score_geral': score_geral,
        'indicadores': indicadores,
        'mapeamento_facial': mapeamento_facial,
    })


def scanner_salvar(request):
    # salva o ultimo resultado do scanner no perfil do usuario
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Você precisa estar logado para salvar o check-in.'}, status=401)

    ultimo_scan = request.session.get('ultimo_scan')
    if not ultimo_scan:
        return JsonResponse({'erro': 'Faça um scan antes de salvar o check-in.'}, status=400)

    # sem perfil nao salva: precisa responder o questionario antes
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    if not perfil:
        return JsonResponse({
            'erro': 'Responda o questionário antes de salvar um check-in.',
            'ir_para': reverse('questionario'),
        }, status=400)

    perfil.dados_ia = ultimo_scan['dados_ia']
    perfil.foto_rosto = ultimo_scan['foto_rosto']
    perfil.porcentagem_saude = ultimo_scan['porcentagem_saude']
    perfil.save()

    del request.session['ultimo_scan']
    return JsonResponse({'ok': True})


def tela_scanner(request):
    # se o usuario ja fez um scan (no questionario), mostra o ultimo resultado salvo no perfil
    ultimo_scan = None
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
        resultados_ia = None
        if perfil and perfil.dados_ia:
            try:
                resultados_ia = json.loads(perfil.dados_ia)
            except (json.JSONDecodeError, TypeError):
                resultados_ia = None

        if resultados_ia:
            indicadores = [
                montar_indicador('oleosidade', _extract_score(resultados_ia, 'oiliness')),
                montar_indicador('hidratacao', _extract_score(resultados_ia, 'moisture')),
                montar_indicador('acne_poros', _media_ou_none(
                    _extract_score(resultados_ia, 'acne'),
                    _extract_score(resultados_ia, 'pore'),
                )),
                montar_indicador('textura', _extract_score(resultados_ia, 'texture')),
                montar_indicador('manchas', _extract_score(resultados_ia, 'age_spot')),
            ]
            notas = [i['nota'] for i in indicadores if i['nota'] is not None]
            if notas:
                ultimo_scan = {
                    'score_geral': round(sum(notas) / len(notas)),
                    'indicadores': indicadores,
                    'mapeamento_facial': _url_https(perfil.foto_rosto),
                }
    return render(request, 'core/scanner.html', {'ultimo_scan': ultimo_scan})


# ==========================================================================
# PERFIL DO USUÁRIO
# ==========================================================================

def _iniciais_do_nome(nome):
    partes = (nome or '').strip().split()
    if not partes:
        return 'U'
    if len(partes) == 1:
        return (partes[0][:2] or 'U').upper()
    return (partes[0][0] + partes[-1][0]).upper()


def _alergias_do_perfil(perfil):
    alergias = (perfil.alergias or '').strip()
    if alergias in ('0', '1'):
        return []
    return [item.strip() for item in alergias.replace(';', ',').split(',') if item.strip()]


FOTOTIPOS = {
    1: ('I', '#f3d9c8', 'Pele muito clara. Sempre queima e nunca bronzeia.'),
    2: ('II', '#e9b99b', 'Pele clara. Queima com facilidade e bronzeia pouco.'),
    3: (
        'III',
        '#c98e63',
        'Sensibilidade moderada ao sol. Use protetor solar todos os dias, mesmo em dias nublados.',
    ),
    4: ('IV', '#a66b45', 'Pele morena clara. Raramente queima e bronzeia com facilidade.'),
    5: ('V', '#75452e', 'Pele morena escura. Quase nunca queima e bronzeia com facilidade.'),
    6: ('VI', '#4a2d1b', 'Pele negra. Nunca queima e tem alta pigmentação natural.'),
}


def _fototipo_do_perfil(perfil):
    if perfil is None or perfil.fototipo not in FOTOTIPOS:
        return None
    numero = perfil.fototipo
    romano, _, texto = FOTOTIPOS[numero]
    tons = [
        {
            'romano': dados[0],
            'cor': dados[1],
            'atual': indice == numero,
        }
        for indice, dados in FOTOTIPOS.items()
    ]
    return {'romano': romano, 'texto': texto, 'tons': tons}


def _indicadores_do_perfil(perfil):
    if not perfil or not perfil.dados_ia:
        return []
    try:
        resultados = json.loads(perfil.dados_ia)
    except (TypeError, ValueError):
        return []
    if not isinstance(resultados, (dict, list)) or not _tem_algum_score(resultados):
        return []

    notas = {
        chave: _extract_score(resultados, chave)
        for chave in INDICADORES_LIDOS
    }
    # indicador mostrado na tela -> nota (acne e poros viram uma nota so, pela media)
    notas_por_titulo = (
        ('oleosidade', notas['oiliness']),
        ('hidratacao', notas['moisture']),
        ('acne_poros', _media_ou_none(notas['acne'], notas['pore'])),
        ('textura', notas['texture']),
        ('manchas', notas['age_spot']),
    )
    valores = []
    for titulo, nota in notas_por_titulo:
        item = montar_indicador(titulo, nota)
        valores.append({
            **item,
            'titulo': {
                'oleosidade': 'Oleosidade',
                'hidratacao': 'Hidratação',
                'acne_poros': 'Acne e poros',
                'textura': 'Textura',
                'manchas': 'Manchas',
            }[titulo],
            'classe_selo': (
                'badge-' + item['classificacao'].lower().replace('ó', 'o').replace('ã', 'a')
                if item['classificacao'] else ''
            ),
        })
    return valores


def _contexto_perfil(request, perfil=None, form_senha=None, aba='resumo'):
    if perfil is None:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    objetivo_atual = normalizar_objetivo(perfil.objetivo) if perfil else ''
    alergias_lista = _alergias_do_perfil(perfil) if perfil else []
    objetivos = list(OBJETIVOS.items())
    if objetivo_atual and objetivo_atual not in OBJETIVOS:
        objetivos.append((objetivo_atual, objetivo_atual))
    return {
        'perfil': perfil,
        'aba_ativa': aba,
        'iniciais': _iniciais_do_nome(request.user.nome_usuario),
        'indicadores': _indicadores_do_perfil(perfil),
        'form_senha': form_senha or PasswordChangeForm(request.user),
        'objetivos': objetivos,
        'objetivo_atual': objetivo_atual,
        'objetivo_rotulo': rotulo_objetivo(perfil.objetivo) if perfil else '',
        'alergias_lista': alergias_lista,
    }


@login_required
def tela_perfil(request):
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    abas = {'resumo', 'dados', 'pele', 'seguranca', 'privacidade'}
    aba = request.GET.get('aba', 'resumo')
    if aba not in abas:
        aba = 'resumo'
    return render(request, 'core/perfil.html', _contexto_perfil(request, perfil, aba=aba))


@login_required
def editar_perfil(request):
    # o formulario de edicao fica embutido direto na aba "Preferencias da Conta"
    # de core/perfil.html, entao essa view so processa o POST e volta pra la
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    if not perfil:
        return redirect('questionario')

    destino = f"{reverse('perfil')}?aba=pele"

    if request.method != 'POST':
        return redirect(destino)

    nome_usuario = request.POST.get('nome_usuario', '').strip()
    idade = request.POST.get('idade')
    tipo_pele = request.POST.get('tipo_pele')
    alergias = request.POST.get('alergias', '').strip()
    objetivo = request.POST.get('objetivo', '').strip()
    preferencia_produto = request.POST.get('preferencia_produto')
    usa_maquiagem_diariamente = request.POST.get('usa_maquiagem_diariamente')

    if nome_usuario:
        request.user.nome_usuario = nome_usuario
        request.user.save()

    if idade:
        # valor invalido (texto ou fora de 0-120) mantem a idade que ja estava
        perfil.idade = _inteiro(idade, perfil.idade, 0, 120)
    if tipo_pele in dict(PerfilDermatologico.TIPO_PELE_CHOICES):
        perfil.tipo_pele = tipo_pele
    perfil.alergias = alergias
    if objetivo:
        perfil.objetivo = normalizar_objetivo(objetivo)
    if preferencia_produto in ('creme', 'gel'):
        perfil.preferencia_produto = preferencia_produto
    if usa_maquiagem_diariamente in ('sim', 'nao'):
        perfil.usa_maquiagem_diariamente = usa_maquiagem_diariamente
    if request.POST.get('sensibilidade') in ('sim', 'nao'):
        perfil.sensibilidade = request.POST['sensibilidade'] == 'sim'
    if request.POST.get('gestante') in ('sim', 'nao'):
        perfil.gestante = request.POST['gestante'] == 'sim'
    if objetivo:
        tipo_acne = request.POST.get('tipo_acne', '')
        perfil.tipo_acne = (
            tipo_acne
            if normalizar_objetivo(objetivo) == 'reduzir acne'
            and tipo_acne in dict(PerfilDermatologico.TIPO_ACNE_CHOICES)
            else ''
        )

    perfil.save()
    return redirect(f"{reverse('perfil')}?aba=pele")


@login_required
def editar_dados_pessoais(request):
    destino = f"{reverse('perfil')}?aba=dados"
    if request.method != 'POST':
        return redirect(destino)

    nome_usuario = request.POST.get('nome_usuario', '').strip()
    if not nome_usuario:
        messages.error(request, 'Informe um nome válido para salvar os dados.')
        return redirect(destino)

    request.user.nome_usuario = nome_usuario
    request.user.save(update_fields=['nome_usuario'])

    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    idade = request.POST.get('idade')
    if perfil and idade:
        perfil.idade = _inteiro(idade, perfil.idade, 0, 120)
        perfil.save(update_fields=['idade'])
    return redirect(destino)


@login_required
def alterar_senha(request):
    if request.method != 'POST':
        return redirect(f"{reverse('perfil')}?aba=seguranca")

    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        usuario = form.save()
        update_session_auth_hash(request, usuario)
        messages.success(request, 'Senha alterada com sucesso.')
        return redirect(f"{reverse('perfil')}?aba=seguranca")

    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    return render(
        request,
        'core/perfil.html',
        _contexto_perfil(request, perfil, form_senha=form, aba='seguranca'),
    )


@login_required
def apagar_foto_perfil(request):
    if request.method == 'POST':
        PerfilDermatologico.objects.filter(usuario=request.user).update(foto_rosto=None)
    return redirect(f"{reverse('perfil')}?aba=privacidade")


@login_required
def exportar_dados(request):
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    dados_perfil = None
    if perfil:
        try:
            analise_ia = json.loads(perfil.dados_ia) if perfil.dados_ia else None
        except (TypeError, ValueError):
            analise_ia = None
        dados_perfil = {
            'idade': perfil.idade,
            'tipo_pele': perfil.tipo_pele,
            'alergias': perfil.alergias,
            'objetivo': perfil.objetivo,
            'preferencia_produto': perfil.preferencia_produto,
            'usa_maquiagem_diariamente': perfil.usa_maquiagem_diariamente,
            'sono_horas': perfil.sono_horas,
            'exercicio_frequencia': perfil.exercicio_frequencia,
            'protetor_solar': perfil.protetor_solar,
            'sensibilidade': perfil.sensibilidade,
            'pele_sensivel': perfil.sensibilidade,
            'gestante': perfil.gestante,
            'tipo_acne': perfil.tipo_acne,
            'porcentagem_saude': perfil.porcentagem_saude,
            'fototipo': perfil.fototipo,
            'foto_rosto': perfil.foto_rosto,
            'analise_ia': analise_ia,
        }

    rotina_feita = [
        {
            'data': str(marcacao.data),
            'periodo': marcacao.periodo,
            'passo': marcacao.titulo,
            'produto_id': marcacao.produto_id or None,
        }
        for marcacao in PassoRotinaFeito.objects.filter(usuario=request.user).order_by('data', 'marcado_em')
    ]
    dados = {
        'conta': {
            'nome': request.user.nome_usuario,
            'email': request.user.email,
            'data_cadastro': request.user.data_cadastro.isoformat(),
        },
        'perfil_dermatologico': dados_perfil,
        'rotina_feita': rotina_feita,
    }
    resposta = HttpResponse(
        json.dumps(dados, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
    )
    resposta['Content-Disposition'] = 'attachment; filename="lume-skin-dados.json"'
    return resposta


@login_required
def excluir_conta(request):
    if request.method == 'POST':
        senha = request.POST.get('senha', '')
        confirmacao = request.POST.get('confirmacao', '').strip().upper()
        if confirmacao == 'EXCLUIR' and request.user.check_password(senha):
            usuario = request.user
            auth_logout(request)
            usuario.delete()
            return redirect('login')
        messages.error(request, 'Confirme com EXCLUIR e informe sua senha correta para apagar a conta.')
    return redirect(f"{reverse('perfil')}?aba=privacidade")


def tela_termos_de_uso(request):
    return render(request, 'core/termosdeuso.html')


def tela_politica_privacidade(request):
    return render(request, 'core/politica_privacidade.html')


# ==========================================================================
# CATÁLOGO - produtos, artigos e especialistas (telas de consulta)
# ==========================================================================

CATEGORIA_BUCKET = {
    'limpeza': {'Limpeza', 'Água Micelar', 'Tônico', 'Esfoliante'},
    'tratamento': {'Sérum', 'Hidratante', 'Máscara Facial', 'Óleo Facial', 'Contorno de Olhos'},
    'protecao': {'Protetor Solar'},
}


def tela_produtos(request):
    # sem login nao existe perfil (filtrar por AnonymousUser estoura erro)
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    produtos = recomendar_produtos(perfil)

    filtro = request.GET.get("categoria")
    if filtro:
        categorias_do_filtro = CATEGORIA_BUCKET.get(filtro, set())
        produtos = [p for p in produtos if p.categoria in categorias_do_filtro]

    context = {
        "perfil": perfil,
        "objetivo_usuario": rotulo_objetivo(perfil.objetivo) if perfil else "não definido",
        "produtos": produtos,
        "filtro_ativo": filtro,
    }
    return render(request, "core/produtos.html", context)


def produto_detalhe(request, produto_id):
    produto = get_object_or_404(Produto, id=produto_id)
    context = {
        'produto': produto,
    }
    return render(request, 'core/produto_detalhe.html', context)


def tela_artigos(request):
    artigos = Artigo.objects.all().order_by('-ano')
    context = {
        "artigos": artigos,
    }
    return render(request, "core/artigos.html", context)


def tela_especialistas(request):
    especialistas = Especialista.objects.all().order_by('nome')

    filtro = request.GET.get("especialidade")

    if filtro:
        especialistas = especialistas.filter(especialidade=filtro)

    context = {
        "especialistas": especialistas,
        "filtro_ativo": filtro,
    }
    return render(request, "core/especialistas.html", context)


# ==========================================================================
# ÁREA ADMINISTRATIVA - CRUD de artigos e especialistas
# ==========================================================================

@admin_required
def tela_administradores(request):
    context = {
        "artigos": Artigo.objects.all().order_by('-ano'),
        "especialistas": Especialista.objects.all().order_by('nome'),
    }
    return render(request, "core/administradores.html", context)


@admin_required
def artigo_form(request, artigo_id=None):
    # os formularios de criar/editar artigo ficam embutidos direto em
    # core/administradores.html, entao essa view so processa o POST
    if request.method != 'POST':
        return redirect('administradores')

    artigo = get_object_or_404(Artigo, id=artigo_id) if artigo_id else Artigo()

    titulo = request.POST.get('titulo', '').strip()
    autor = request.POST.get('autor', '').strip()
    url_leitura = request.POST.get('url_leitura', '').strip()
    # ano invalido (vazio, texto) mantem o que ja estava; artigo novo precisa de um ano
    ano = _inteiro(request.POST.get('ano'), artigo.ano, 1900, 2100)
    if not titulo or not autor or not url_leitura or ano is None:
        messages.error(request, 'Informe título, autor, ano (entre 1900 e 2100) e o link de leitura do artigo.')
        return redirect('administradores')

    artigo.titulo = titulo
    artigo.autor = autor
    artigo.ano = ano
    artigo.resumo = request.POST.get('resumo', '').strip()
    artigo.url_capa = request.POST.get('url_capa', '').strip()
    artigo.url_leitura = url_leitura
    artigo.save()
    return redirect('administradores')


@admin_required
def artigo_excluir(request, artigo_id):
    artigo = get_object_or_404(Artigo, id=artigo_id)
    if request.method == 'POST':
        artigo.delete()
    return redirect('administradores')


@admin_required
def especialista_form(request, especialista_id=None):
    # os formularios de criar/editar especialista ficam embutidos direto em
    # core/administradores.html, entao essa view so processa o POST
    if request.method != 'POST':
        return redirect('administradores')

    especialista = get_object_or_404(Especialista, id=especialista_id) if especialista_id else Especialista()

    nome = request.POST.get('nome', '').strip()
    especialidade = request.POST.get('especialidade', '').strip()
    telefone = request.POST.get('telefone_whatsapp', '').strip()
    if not nome or not especialidade or not telefone:
        messages.error(request, 'Informe nome, especialidade e WhatsApp do especialista.')
        return redirect('administradores')

    especialista.nome = nome
    especialista.especialidade = especialidade
    especialista.crm = request.POST.get('crm', '').strip()
    especialista.telefone_whatsapp = telefone
    especialista.url_foto = request.POST.get('url_foto', '').strip()
    especialista.save()
    return redirect('administradores')


@admin_required
def especialista_excluir(request, especialista_id):
    especialista = get_object_or_404(Especialista, id=especialista_id)
    if request.method == 'POST':
        especialista.delete()
    return redirect('administradores')


# ==========================================================================
# ÁREA UV - índice ultravioleta da região do usuário (Open-Meteo)
# ==========================================================================

def indice_uv(request):
    # O Vercel informa a localização aproximada (pelo IP) nestes cabeçalhos.
    # Se não vierem, usa Porto Alegre como reserva.
    meta = request.META
    try:
        lat = float(meta.get("HTTP_X_VERCEL_IP_LATITUDE", "-30.03"))
        lon = float(meta.get("HTTP_X_VERCEL_IP_LONGITUDE", "-51.23"))
    except ValueError:
        lat, lon = -30.03, -51.23
    cidade = unquote(meta.get("HTTP_X_VERCEL_IP_CITY", "")) or "Porto Alegre"

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=uv_index&timezone=America/Sao_Paulo"
    )

    try:
        with urllib.request.urlopen(url, timeout=5) as resposta:
            dados = json.load(resposta)
        uv = round(dados["current"]["uv_index"])
    except Exception:
        return JsonResponse({"erro": "indisponível"}, status=502)

    # Classificação padrão da Organização Mundial da Saúde
    if uv <= 2:
        nivel = "baixo"
    elif uv <= 5:
        nivel = "moderado"
    elif uv <= 7:
        nivel = "alto"
    elif uv <= 10:
        nivel = "muito alto"
    else:
        nivel = "extremo"

    return JsonResponse({"uv": uv, "nivel": nivel, "cidade": cidade})