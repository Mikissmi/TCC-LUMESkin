from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login as auth_login, authenticate, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.urls import reverse
from django.contrib import messages
from functools import wraps
from .models import Usuario, PerfilDermatologico, Artigo, Especialista, Produto
from .recomendacao import recomendar_produtos, classificar_indicador
import requests
import io
import json
import time
import os
import zipfile
import json
import urllib.request
from urllib.parse import unquote

from django.http import JsonResponse


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


# imprime o erro da YouCam (HTTP + detalhe) no terminal
def _youcam_error(response, etapa):
    try:
        detalhe = response.json()
    except ValueError:
        detalhe = response.text
    print(f'[YouCam] {etapa} - HTTP {response.status_code}: {detalhe}')


# ==========================================================================
# LEITURA DO RESULTADO DA YOUCAM - extrai notas e imagens do json
# ==========================================================================

# o json muda de formato conforme o recurso (e o plano), entao nao da pra
# apontar um caminho fixo: as funcoes abaixo procuram o valor em qualquer nivel
SUFIXOS_IMAGEM = ('.png', '.jpg', '.jpeg', '.webp')
CHAVES_NOTA = ('ui_score', 'raw_score', 'score', 'value', 'severity')
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


# garante que o valor e um numero (int ou float) e nao um booleano. Se nao for, devolve None
def _numero(valor):
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


# nota de um indicador: numero direto, {"ui_score": 80, ...} ou por regiao ({"whole": {...}, ...})
# ordem de tentativa: numero puro -> chave de nota (a primeira que existir) -> "whole" (rosto inteiro) -> media das regioes
def _nota_de(item):
    direto = _numero(item)
    if direto is not None:
        return direto
    if not isinstance(item, dict):
        return None
    for chave in CHAVES_NOTA:
        nota = _numero(item.get(chave))
        if nota is not None:
            return nota
    if isinstance(item.get('whole'), dict):
        nota = _nota_de(item['whole'])
        if nota is not None:
            return nota
    regioes = [_nota_de(v) for v in item.values() if isinstance(v, dict)]
    regioes = [n for n in regioes if n is not None]
    return sum(regioes) / len(regioes) if regioes else None


# procura a nota de um indicador em qualquer nivel do JSON da YouCam. Aceita:
# {"oiliness": {"ui_score": 80}}, {"hd_oiliness": {...}}, {"oiliness": 80}
# [{"type": "oiliness", "ui_score": 80, "raw_score": 78.2, "mask_urls": [...]}]
def _extract_score(value, key):
    nomes = (key, 'hd_' + key)
    if isinstance(value, dict):
        if value.get('type') in nomes:
            nota = _nota_de(value)
            if nota is not None:
                return nota
        for nome in nomes:
            if nome in value:
                nota = _nota_de(value[nome])
                if nota is not None:
                    return nota
        for item in value.values():
            found = _extract_score(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_score(item, key)
            if found is not None:
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
            print(f'[YouCam] Não consegui baixar o zip de resultados (HTTP {resposta.status_code}).')
            return None
        with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo:
            for nome in arquivo.namelist():
                if nome.lower().endswith('score_info.json'):
                    return json.loads(arquivo.read(nome).decode('utf-8'))
        print('[YouCam] O zip de resultados não tem score_info.json.')
    except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
        print(f'[YouCam] Erro ao ler o zip de resultados: {exc}')
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
    print('[YouCam] Nenhum score reconhecido na resposta. Resposta recebida:')
    print(texto[:6000])
    if settings.DEBUG:
        try:
            with open(os.path.join(settings.BASE_DIR, 'youcam_ultima_resposta.json'), 'w', encoding='utf-8') as arq:
                arq.write(texto)
            print('[YouCam] Resposta completa gravada em youcam_ultima_resposta.json')
        except OSError as exc:
            print(f'[YouCam] Não consegui gravar o arquivo de diagnóstico: {exc}')


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
        print(f'[YouCam] Tarefa criada sem task_id: {resposta_task.text}')
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

        print(f'[YouCam] tarefa={task_id} status={status}')

        if status == 'success':
            resultados = data.get('results')
            if not resultados:
                print(f'[YouCam] Tarefa concluída, mas sem results: {payload}')
                return None, 'sem_resultados'

            # Guarda a URL do resultado, caso exista, para o mapeamento facial.
            if isinstance(resultados, dict):
                resultado_url = _extract_url(resultados)
                if resultado_url and 'result_image_url' not in resultados:
                    resultados['result_image_url'] = resultado_url

            return resultados, 'ok'

        if status == 'error':
            print(f'[YouCam] Tarefa terminou com erro: {payload}')
            return None, 'erro_na_tarefa'

    print('[YouCam] Tempo limite aguardando o resultado da análise.')
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
            print('[YouCam] A imagem recebida está vazia.')
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
            print(f'[YouCam] File API não retornou files: {resposta_file.text}')
            return None

        file_info = file_data[0]
        file_id = file_info.get('file_id')
        requests_info = file_info.get('requests') or []
        upload_request = next((item for item in requests_info if item.get('method', 'PUT').upper() == 'PUT'), None)
        upload_url = upload_request.get('url') if upload_request else None

        if not file_id or not upload_url:
            print(f'[YouCam] File API não retornou file_id/upload URL: {file_info}')
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
            print('[YouCam] Tentando de novo só com os recursos básicos.')
            resultados, motivo = _analisar_na_youcam(headers, file_id, YOUCAM_ACOES_BASE)

        return resultados

    except requests.RequestException as exc:
        print(f'[Erro de conexão YouCam API]: {exc}')
    except (ValueError, KeyError, TypeError) as exc:
        print(f'[Erro ao interpretar resposta YouCam API]: {exc}')
    except Exception as exc:
        print(f'[Erro YouCam API]: {exc}')

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

def tela_cadastro(request):
    # verifica se o navegador está enviando dados através de um formulário (POST)
    if request.method == "POST":
        nome = request.POST.get('nome')
        email = request.POST.get('email')
        senha = request.POST.get('senha')

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


def tela_login(request):
    if request.method == "POST":
        email = request.POST.get('email')
        senha = request.POST.get('senha')

        # login do administrador: mesma tela/form, mas com email e senha fixos
        # (definidos em ADMIN_LOGIN_EMAIL / ADMIN_LOGIN_SENHA) em vez de um
        # Usuario cadastrado no banco
        if email == settings.ADMIN_LOGIN_EMAIL and senha == settings.ADMIN_LOGIN_SENHA:
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

            if foto_usuario and _foto_valida(foto_usuario):
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
            perfil, created = PerfilDermatologico.objects.get_or_create(
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

            # Só substitui a análise anterior se houve um novo scan
            if dados_ia_json:
                perfil.dados_ia = dados_ia_json
                perfil.foto_rosto = foto_salva

            perfil.save()

            # Redireciona conforme o fluxo realizado
            if redirecionar_para_scanner:
                return redirect('scanner')

            return redirect('dashboard')

    return render(request, 'core/questionario.html')



def dashboard_view(request):
    # sem login nao mostra perfil (antes pegava o primeiro do banco, que e de outra pessoa)
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    rotina_manha = []
    rotina_noite = []
    if perfil:
        rotina_manha = [
            {
                'class': 'completed',
                'title': 'Limpeza Suave',
                'description': f'Rotina de limpeza diária para pele {perfil.tipo_pele}',
                'time': '08:00',
                'action': None,
                'icon': 'fa-check',
            },
            {
                'class': 'action-required' if perfil.porcentagem_saude < 80 else 'completed',
                'title': 'Hidratação & Tratamento',
                'description': f'Sérum recomendado para objetivo "{perfil.objetivo}"',
                'time': None if perfil.porcentagem_saude < 80 else '19:00',
                'action': 'Fazer agora' if perfil.porcentagem_saude < 80 else None,
                'icon': 'fa-check' if perfil.porcentagem_saude >= 80 else None,
            },
            {
                'class': 'pending' if perfil.fototipo and perfil.fototipo < 5 else 'completed',
                'title': 'Proteção Solar',
                'description': f'FPS 50+ para fototipo {perfil.fototipo or "1"}',
                'time': '12:00' if perfil.fototipo and perfil.fototipo < 5 else 'Já feito',
                'action': None,
                'icon': 'fa-check' if perfil.fototipo and perfil.fototipo >= 5 else None,
            },
        ]
        rotina_noite = [
            {
                'class': 'completed',
                'title': 'Remoção de Maquiagem',
                'description': 'Demaquilante suave antes de dormir',
                'time': '21:00',
                'action': None,
                'icon': 'fa-check',
            },
            {
                'class': 'completed',
                'title': 'Tratamento Noturno',
                'description': f'Sérum calmante para {perfil.tipo_pele}',
                'time': '21:30',
                'action': None,
                'icon': 'fa-check',
            },
            {
                'class': 'pending',
                'title': 'Hidratação Profunda',
                'description': 'Creme nutritivo para reparar enquanto dorme',
                'time': '22:00',
                'action': 'Aplicar agora',
                'icon': None,
            },
        ]

    context = {
        'perfil': perfil,
        'rotina_manha': rotina_manha,
        'rotina_noite': rotina_noite,
    }
    return render(request, 'core/dashboard.html', context)


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
    return render(request, 'core/scanner.html')


# ==========================================================================
# PERFIL DO USUÁRIO
# ==========================================================================

def tela_perfil(request):
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    context = {
        'perfil': perfil,
    }
    return render(request, 'core/perfil.html', context)


@login_required
def editar_perfil(request):
    # o formulario de edicao fica embutido direto na aba "Preferencias da Conta"
    # de core/perfil.html, entao essa view so processa o POST e volta pra la
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    if not perfil:
        return redirect('questionario')

    destino = f"{reverse('perfil')}?aba=conta"

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
        perfil.objetivo = objetivo
    if preferencia_produto in ('creme', 'gel'):
        perfil.preferencia_produto = preferencia_produto
    if usa_maquiagem_diariamente in ('sim', 'nao'):
        perfil.usa_maquiagem_diariamente = usa_maquiagem_diariamente

    perfil.save()
    return redirect(destino)


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
        "objetivo_usuario": perfil.objetivo if perfil else "não definido",
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

    artigo.titulo = request.POST.get('titulo', '').strip()
    artigo.autor = request.POST.get('autor', '').strip()
    artigo.ano = request.POST.get('ano') or artigo.ano
    artigo.resumo = request.POST.get('resumo', '').strip()
    artigo.url_capa = request.POST.get('url_capa', '').strip()
    artigo.url_leitura = request.POST.get('url_leitura', '').strip()
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

    especialista.nome = request.POST.get('nome', '').strip()
    especialista.especialidade = request.POST.get('especialidade', '').strip()
    especialista.crm = request.POST.get('crm', '').strip()
    especialista.telefone_whatsapp = request.POST.get('telefone_whatsapp', '').strip()
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
# ÁREA UV - 
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