import json
import os
import tempfile
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone
from django.urls import reverse

from .models import PassoRotinaFeito, PerfilDermatologico, Produto, RegraTratamento, Usuario
from .recomendacao import (
    MAPA_OBJETIVO_PREOCUPACAO, OBJETIVOS, ativo_nas_alergias, extrair_sinais_faciais, faixa_etaria,
    montar_rotina, normalizar_objetivo, pontuar_produto, preocupacoes_de_tratamento,
    preocupacoes_do_perfil, recomendar_produtos, rotulo_objetivo,
)
from .views import FOTOTIPOS, _alergias_do_perfil, _fototipo_do_perfil, _indicadores_do_perfil, _iniciais_do_nome


# ==========================================================================
# PERFIL E CONFIGURAÇÕES - abas Resumo, Dados, Perfil de pele, Segurança e Privacidade
# ==========================================================================

SENHA = 'senha-segura-123'


def criar_usuario_com_perfil(email='ana@email.com', com_perfil=True):
    usuario = Usuario.objects.create_user(email=email, nome_usuario='Ana Souza', password=SENHA)
    perfil = None
    if com_perfil:
        perfil = PerfilDermatologico.objects.create(
            usuario=usuario,
            idade=27,
            tipo_pele='mista',
            alergias='fragrância, parabenos',
            objetivo='controlar oleosidade',
            preferencia_produto='gel',
            usa_maquiagem_diariamente='nao',
            porcentagem_saude=78,
            fototipo=3,
            foto_rosto='https://exemplo.com/scan.jpg',
            dados_ia=json.dumps({'oiliness': {'score': 90}, 'moisture': {'score': 60}}),
        )
    return usuario, perfil


class PaginasLegaisTests(TestCase):
    def test_pagina_de_termos_de_uso(self):
        resposta = self.client.get(reverse('termosdeuso'))
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, 'core/termosdeuso.html')
        self.assertContains(resposta, 'TERMOS DE USO')

    def test_pagina_de_politica_de_privacidade(self):
        resposta = self.client.get(reverse('politica_privacidade'))
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, 'core/politica_privacidade.html')
        self.assertContains(resposta, 'POLÍTICA DE PRIVACIDADE')


class PerfilBaseTestCase(TestCase):
    def setUp(self):
        self.usuario, self.perfil = criar_usuario_com_perfil()
        self.client.login(username='ana@email.com', password=SENHA)


class FuncoesAuxiliaresTests(TestCase):
    def test_iniciais_do_nome(self):
        self.assertEqual(_iniciais_do_nome('Ana Souza'), 'AS')
        self.assertEqual(_iniciais_do_nome('Maria'), 'MA')
        self.assertEqual(_iniciais_do_nome(''), 'U')

    def test_alergias_ignora_respostas_sim_nao_do_questionario(self):
        _, perfil = criar_usuario_com_perfil()
        perfil.alergias = '1'
        self.assertEqual(_alergias_do_perfil(perfil), [])
        perfil.alergias = '0'
        self.assertEqual(_alergias_do_perfil(perfil), [])
        perfil.alergias = 'fragrância; parabenos, látex'
        self.assertEqual(_alergias_do_perfil(perfil), ['fragrância', 'parabenos', 'látex'])

    def test_indicadores_do_ultimo_scan(self):
        _, perfil = criar_usuario_com_perfil()
        indicadores = {i['chave']: i for i in _indicadores_do_perfil(perfil)}
        self.assertEqual(indicadores['oleosidade']['nota'], 90)
        self.assertEqual(indicadores['oleosidade']['classificacao'], 'Ótimo')
        self.assertEqual(indicadores['oleosidade']['classe_selo'], 'badge-otimo')
        self.assertEqual(indicadores['hidratacao']['classificacao'], 'Regular')
        self.assertIsNone(indicadores['textura']['nota'])

    def test_sem_scan_nao_ha_indicadores(self):
        _, perfil = criar_usuario_com_perfil()
        perfil.dados_ia = None
        self.assertEqual(_indicadores_do_perfil(perfil), [])
        perfil.dados_ia = 'isto nao e json'
        self.assertEqual(_indicadores_do_perfil(perfil), [])


class TelaPerfilTests(PerfilBaseTestCase):
    def test_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse('perfil'))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse('login'), resposta.url)

    def test_abre_na_aba_resumo_por_padrao(self):
        resposta = self.client.get(reverse('perfil'))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['aba_ativa'], 'resumo')
        self.assertContains(resposta, 'Indicadores do último scan')
        self.assertContains(resposta, 'AS')  # iniciais do avatar

    def test_abre_a_aba_pedida_e_ignora_aba_invalida(self):
        for aba in ('dados', 'pele', 'seguranca', 'privacidade'):
            resposta = self.client.get(reverse('perfil'), {'aba': aba})
            self.assertEqual(resposta.context['aba_ativa'], aba)
        resposta = self.client.get(reverse('perfil'), {'aba': 'qualquer-coisa'})
        self.assertEqual(resposta.context['aba_ativa'], 'resumo')

    def test_usuario_sem_questionario_ainda_acessa_conta_e_seguranca(self):
        self.client.logout()
        criar_usuario_com_perfil('sem-perfil@email.com', com_perfil=False)
        self.client.login(username='sem-perfil@email.com', password=SENHA)
        for aba in ('resumo', 'dados', 'pele', 'seguranca', 'privacidade'):
            resposta = self.client.get(reverse('perfil'), {'aba': aba})
            self.assertEqual(resposta.status_code, 200)


class DadosPessoaisTests(PerfilBaseTestCase):
    def test_atualiza_nome_e_idade(self):
        resposta = self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana Lima', 'idade': '30'})
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=dados")
        self.usuario.refresh_from_db()
        self.perfil.refresh_from_db()
        self.assertEqual(self.usuario.nome_usuario, 'Ana Lima')
        self.assertEqual(self.perfil.idade, 30)

    def test_nome_vazio_nao_salva(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': '   ', 'idade': '30'})
        self.usuario.refresh_from_db()
        self.perfil.refresh_from_db()
        self.assertEqual(self.usuario.nome_usuario, 'Ana Souza')
        self.assertEqual(self.perfil.idade, 27)

    def test_idade_invalida_mantem_a_anterior(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana', 'idade': 'abc'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.idade, 27)

    def test_email_nao_e_alterado(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana', 'email': 'outro@email.com'})
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, 'ana@email.com')


class PerfilDePeleTests(PerfilBaseTestCase):
    def test_atualiza_preferencias(self):
        resposta = self.client.post(reverse('editar_perfil'), {
            'tipo_pele': 'seca',
            'objetivo': 'hidratar',
            'alergias': 'látex',
            'preferencia_produto': 'creme',
            'usa_maquiagem_diariamente': 'sim',
        })
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=pele")
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_pele, 'seca')
        self.assertEqual(self.perfil.objetivo, 'hidratar')
        self.assertEqual(self.perfil.alergias, 'látex')
        self.assertEqual(self.perfil.preferencia_produto, 'creme')
        self.assertEqual(self.perfil.usa_maquiagem_diariamente, 'sim')

    def test_valores_invalidos_sao_ignorados(self):
        self.client.post(reverse('editar_perfil'), {
            'tipo_pele': 'inventada',
            'preferencia_produto': 'pomada',
            'usa_maquiagem_diariamente': 'talvez',
        })
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_pele, 'mista')
        self.assertEqual(self.perfil.preferencia_produto, 'gel')
        self.assertEqual(self.perfil.usa_maquiagem_diariamente, 'nao')

    def test_sem_perfil_manda_para_o_questionario(self):
        self.client.logout()
        criar_usuario_com_perfil('novo@email.com', com_perfil=False)
        self.client.login(username='novo@email.com', password=SENHA)
        resposta = self.client.post(reverse('editar_perfil'), {'tipo_pele': 'seca'})
        self.assertRedirects(resposta, reverse('questionario'))

    def test_objetivo_editado_continua_gerando_preocupacoes(self):
        # bug da Fase 0: o texto da aba de perfil nao era entendido pelo mapa de recomendacao
        for codigo, preocupacoes in MAPA_OBJETIVO_PREOCUPACAO.items():
            self.client.post(reverse('editar_perfil'), {'objetivo': codigo})
            self.perfil.refresh_from_db()
            self.assertEqual(self.perfil.objetivo, codigo)
            self.assertEqual(preocupacoes_do_perfil(self.perfil, {}), preocupacoes)

    def test_texto_antigo_do_objetivo_e_convertido_ao_salvar(self):
        self.client.post(reverse('editar_perfil'), {'objetivo': 'Prevenir sinais de idade'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.objetivo, 'anti-idade')

    def test_select_de_objetivo_mostra_o_objetivo_atual_selecionado(self):
        self.perfil.objetivo = 'reduzir acne'
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="reduzir acne" selected>Reduzir acne</option>', html=True)
        # as 6 opcoes do questionario existem no select
        for codigo in OBJETIVOS:
            self.assertContains(resposta, f'<option value="{codigo}"')

    def test_objetivo_antigo_salvo_no_banco_aparece_selecionado(self):
        self.perfil.objetivo = 'Hidratar a pele'  # gravado pela versao antiga da aba
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="hidratar" selected>Hidratar</option>', html=True)

    def test_objetivo_texto_livre_continua_na_lista(self):
        self.perfil.objetivo = 'pele de porcelana'
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="pele de porcelana" selected>pele de porcelana</option>', html=True)


class ObjetivoTests(TestCase):
    def test_normalizar_objetivo(self):
        self.assertEqual(normalizar_objetivo('reduzir acne'), 'reduzir acne')
        self.assertEqual(normalizar_objetivo('Controlar oleosidade'), 'controlar oleosidade')
        self.assertEqual(normalizar_objetivo('Hidratar a pele'), 'hidratar')
        self.assertEqual(normalizar_objetivo('Prevenir sinais de idade'), 'anti-idade')
        self.assertEqual(normalizar_objetivo('  Meu Objetivo  '), 'Meu Objetivo')  # texto livre nao muda
        self.assertEqual(normalizar_objetivo(None), '')

    def test_rotulo_objetivo(self):
        self.assertEqual(rotulo_objetivo('anti-idade'), 'Anti-idade')
        self.assertEqual(rotulo_objetivo('Hidratar a pele'), 'Hidratar')
        self.assertEqual(rotulo_objetivo('pele de porcelana'), 'pele de porcelana')

    def test_todo_objetivo_tem_preocupacao_no_mapa(self):
        self.assertEqual(set(OBJETIVOS), set(MAPA_OBJETIVO_PREOCUPACAO))

    def test_questionario_oferece_os_mesmos_objetivos(self):
        resposta = self.client.get(reverse('questionario'))
        for codigo in OBJETIVOS:
            self.assertContains(resposta, f'<option value="{codigo}">')

    def test_telas_mostram_o_texto_e_nao_o_codigo(self):
        usuario, _ = criar_usuario_com_perfil()
        self.client.login(username='ana@email.com', password=SENHA)
        for nome in ('dashboard', 'produtos'):
            self.assertContains(self.client.get(reverse(nome)), 'Controlar oleosidade')


# ==========================================================================
# QUESTIONÁRIO - o POST precisa gravar todas as respostas no perfil
# ==========================================================================

def respostas_do_questionario(**mudancas):
    respostas = {
        'idade': '25', 'tipo_pele': 'oleosa', 'alergias': '0', 'maquiagem': '0',
        'reacao_sol': '2', 'base_produto': 'gel', 'objetivo': 'reduzir acne',
        'sono_horas': 'curto', 'exercicio_frequencia': '5x+', 'protetor_solar': '2',
    }
    respostas.update(mudancas)
    return respostas


class QuestionarioBase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(email='nova@email.com', nome_usuario='Nova', password=SENHA)
        self.client.login(username='nova@email.com', password=SENHA)

    def perfil(self):
        return PerfilDermatologico.objects.get(usuario=self.usuario)


class QuestionarioTests(QuestionarioBase):
    def test_primeiro_envio_salva_sono_exercicio_protetor_e_objetivo(self):
        resposta = self.client.post(reverse('questionario'), respostas_do_questionario())
        self.assertRedirects(resposta, reverse('dashboard'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'curto')
        self.assertEqual(perfil.exercicio_frequencia, '5x+')
        self.assertEqual(perfil.protetor_solar, 2)
        self.assertEqual(perfil.objetivo, 'reduzir acne')

    def test_responder_de_novo_atualiza_os_campos(self):
        self.client.post(reverse('questionario'), respostas_do_questionario())
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='longo', exercicio_frequencia='nunca', protetor_solar='0'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'longo')
        self.assertEqual(perfil.exercicio_frequencia, 'nunca')
        self.assertEqual(perfil.protetor_solar, 0)  # 0 e uma resposta valida, nao "vazio"

    def test_valores_invalidos_no_primeiro_envio_ficam_com_o_padrao(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='dormi', exercicio_frequencia='sempre', protetor_solar='9'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'adequado')
        self.assertEqual(perfil.exercicio_frequencia, '1-2x')
        self.assertEqual(perfil.protetor_solar, 1)

    def test_valores_invalidos_mantem_o_que_ja_estava(self):
        self.client.post(reverse('questionario'), respostas_do_questionario())
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='dormi', exercicio_frequencia='', protetor_solar='abc'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'curto')
        self.assertEqual(perfil.exercicio_frequencia, '5x+')
        self.assertEqual(perfil.protetor_solar, 2)

    def test_objetivo_do_questionario_gera_preocupacao(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(objetivo='reduzir acne'))
        self.assertEqual(preocupacoes_do_perfil(self.perfil(), {}), {'Acne'})


class NovasPerguntasQuestionarioTests(QuestionarioBase):
    def test_salva_sensibilidade_gestante_e_tipo_acne(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sensibilidade='1', gestante='1', tipo_acne='Nodular'))
        perfil = self.perfil()
        self.assertIs(perfil.sensibilidade, True)
        self.assertIs(perfil.gestante, True)
        self.assertEqual(perfil.tipo_acne, 'Nodular')

    def test_respostas_nao_salvam_false(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(sensibilidade='0', gestante='0'))
        perfil = self.perfil()
        self.assertIs(perfil.sensibilidade, False)
        self.assertIs(perfil.gestante, False)

    def test_nao_sei_no_tipo_de_acne_fica_vazio(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(tipo_acne='nao-sei'))
        self.assertEqual(self.perfil().tipo_acne, '')

    def test_tipo_de_acne_invalido_fica_vazio(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(tipo_acne='grave'))
        self.assertEqual(self.perfil().tipo_acne, '')

    def test_tipo_de_acne_so_vale_com_objetivo_acne(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(
            objetivo='hidratar', tipo_acne='Cística'))
        self.assertEqual(self.perfil().tipo_acne, '')

    def test_sem_os_campos_novos_usa_o_padrao(self):
        # formulario antigo / requisicao sem as perguntas novas
        self.client.post(reverse('questionario'), respostas_do_questionario())
        perfil = self.perfil()
        self.assertIs(perfil.sensibilidade, False)
        self.assertIs(perfil.gestante, False)
        self.assertEqual(perfil.tipo_acne, '')

    def test_responder_de_novo_sem_os_campos_novos_mantem_o_que_ja_estava(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sensibilidade='1', gestante='1', tipo_acne='Inflamatória'))
        self.client.post(reverse('questionario'), respostas_do_questionario())
        perfil = self.perfil()
        self.assertIs(perfil.sensibilidade, True)
        self.assertIs(perfil.gestante, True)
        self.assertEqual(perfil.tipo_acne, 'Inflamatória')

    def test_questionario_mostra_as_perguntas_novas(self):
        resposta = self.client.get(reverse('questionario'))
        for texto in ('name="sensibilidade"', 'name="gestante"', 'name="tipo_acne"'):
            self.assertContains(resposta, texto)
        for tipo in PerfilDermatologico.TIPO_ACNE_CHOICES:
            self.assertContains(resposta, f'<option value="{tipo[0]}">')


class NovasPerguntasPerfilTests(PerfilBaseTestCase):
    def test_usuario_antigo_tem_os_padroes_e_a_tela_abre(self):
        self.assertIs(self.perfil.sensibilidade, False)
        self.assertIs(self.perfil.gestante, False)
        self.assertEqual(self.perfil.tipo_acne, '')
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertEqual(resposta.status_code, 200)

    def test_edita_sensibilidade_e_gestante(self):
        self.client.post(reverse('editar_perfil'), {'sensibilidade': 'sim', 'gestante': 'sim'})
        self.perfil.refresh_from_db()
        self.assertIs(self.perfil.sensibilidade, True)
        self.assertIs(self.perfil.gestante, True)
        self.client.post(reverse('editar_perfil'), {'sensibilidade': 'nao', 'gestante': 'nao'})
        self.perfil.refresh_from_db()
        self.assertIs(self.perfil.sensibilidade, False)
        self.assertIs(self.perfil.gestante, False)

    def test_valor_invalido_de_sensibilidade_e_ignorado(self):
        self.perfil.sensibilidade = True
        self.perfil.save()
        self.client.post(reverse('editar_perfil'), {'sensibilidade': 'talvez', 'gestante': ''})
        self.perfil.refresh_from_db()
        self.assertIs(self.perfil.sensibilidade, True)
        self.assertIs(self.perfil.gestante, False)

    def test_edita_tipo_de_acne_com_objetivo_acne(self):
        self.client.post(reverse('editar_perfil'), {'objetivo': 'reduzir acne', 'tipo_acne': 'Cística'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_acne, 'Cística')
        # "Não sei" volta para vazio
        self.client.post(reverse('editar_perfil'), {'objetivo': 'reduzir acne', 'tipo_acne': ''})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_acne, '')

    def test_trocar_o_objetivo_limpa_o_tipo_de_acne(self):
        self.client.post(reverse('editar_perfil'), {'objetivo': 'reduzir acne', 'tipo_acne': 'Nodular'})
        self.client.post(reverse('editar_perfil'), {'objetivo': 'hidratar'})  # sem o campo no POST
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_acne, '')

    def test_editar_outros_campos_nao_apaga_o_tipo_de_acne(self):
        self.client.post(reverse('editar_perfil'), {'objetivo': 'reduzir acne', 'tipo_acne': 'Nodular'})
        self.client.post(reverse('editar_perfil'), {'tipo_pele': 'seca'})  # objetivo e acne nao enviados
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_acne, 'Nodular')

    def test_aba_mostra_os_valores_salvos(self):
        self.perfil.sensibilidade = True
        self.perfil.objetivo = 'reduzir acne'
        self.perfil.tipo_acne = 'Nodular'
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<input type="radio" name="sensibilidade" value="sim" checked>', html=True)
        self.assertContains(resposta, 'value="Nodular" selected')
        self.assertNotContains(resposta, 'id="campo-tipo-acne" hidden')

    def test_campo_de_acne_fica_escondido_com_outro_objetivo(self):
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, 'id="campo-tipo-acne" hidden')


class SegurancaTests(PerfilBaseTestCase):
    def test_troca_de_senha_mantem_o_usuario_logado(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': SENHA,
            'new_password1': 'outra-senha-forte-456',
            'new_password2': 'outra-senha-forte-456',
        })
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=seguranca")
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password('outra-senha-forte-456'))
        # continua logado: abrir o perfil nao redireciona pro login
        self.assertEqual(self.client.get(reverse('perfil')).status_code, 200)

    def test_senha_atual_errada_mostra_erro_e_nao_troca(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': 'errada',
            'new_password1': 'outra-senha-forte-456',
            'new_password2': 'outra-senha-forte-456',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['aba_ativa'], 'seguranca')
        self.assertTrue(resposta.context['form_senha'].errors)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))

    def test_senha_fraca_e_recusada(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': SENHA,
            'new_password1': '12345678',
            'new_password2': '12345678',
        })
        self.assertEqual(resposta.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))


class PrivacidadeTests(PerfilBaseTestCase):
    def test_apagar_imagem_do_scan(self):
        resposta = self.client.post(reverse('apagar_foto_perfil'))
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=privacidade")
        self.perfil.refresh_from_db()
        self.assertIsNone(self.perfil.foto_rosto)
        # a analise e o score continuam
        self.assertEqual(self.perfil.porcentagem_saude, 78)
        self.assertIsNotNone(self.perfil.dados_ia)

    def test_apagar_so_aceita_post(self):
        self.client.get(reverse('apagar_foto_perfil'))
        self.perfil.refresh_from_db()
        self.assertIsNotNone(self.perfil.foto_rosto)

    def test_exportar_dados_baixa_json(self):
        resposta = self.client.get(reverse('exportar_dados'))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('attachment', resposta['Content-Disposition'])
        dados = json.loads(resposta.content)
        self.assertEqual(dados['conta']['email'], 'ana@email.com')
        self.assertEqual(dados['perfil_dermatologico']['tipo_pele'], 'mista')
        self.assertEqual(dados['perfil_dermatologico']['analise_ia']['oiliness']['score'], 90)
        self.assertNotIn('password', json.dumps(dados))

    def test_exportar_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse('exportar_dados'))
        self.assertEqual(resposta.status_code, 302)

    def test_excluir_conta_exige_a_palavra_de_confirmacao(self):
        self.client.post(reverse('excluir_conta'), {'senha': SENHA, 'confirmacao': 'sim'})
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())

    def test_excluir_conta_exige_a_senha_correta(self):
        self.client.post(reverse('excluir_conta'), {'senha': 'errada', 'confirmacao': 'EXCLUIR'})
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())

    def test_excluir_conta_apaga_usuario_e_perfil(self):
        resposta = self.client.post(reverse('excluir_conta'), {'senha': SENHA, 'confirmacao': 'excluir'})
        self.assertRedirects(resposta, reverse('login'))
        self.assertFalse(Usuario.objects.filter(pk=self.usuario.pk).exists())
        self.assertFalse(PerfilDermatologico.objects.filter(pk=self.perfil.pk).exists())
        # a sessao foi encerrada
        self.assertEqual(self.client.get(reverse('perfil')).status_code, 302)

    def test_excluir_so_aceita_post(self):
        self.client.get(reverse('excluir_conta'))
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())


# ==========================================================================
# TRATAMENTO - model RegraTratamento e comando import_tratamento_csv
# ==========================================================================

CABECALHO_TRATAMENTO = (
    'Faixa_Etária,Tipo_de_Pele,Subtipo_de_Pele,Sensibilidade,Preocupação,Tipo_Interno,'
    'Ingredientes,Concentrações,Efeitos'
)

# 3 linhas de exemplo: acne (tipo interno), rugas ("Geral") e uma sem sensibilidade
CSV_TRATAMENTO = f"""{CABECALHO_TRATAMENTO}
14-18,Oleosa,Oleosa a Normal,Sim,Acne,Comedonal,PCA de Zinco + Peróxido de Benzoíla + Ácido Salicílico,0.5% + 2% + 5%,"Controla o sebo, Desobstrui os poros"
45+,Seca,Extremamente Seca,Não,Rugas,Geral,Retinol + Ácido Hialurônico + Peptídeos,10% + 1% + 2%,Estimula o colágeno
25-36,Mista,Zona T Oleosa e Bochechas Secas,Não,Poros Dilatados,Geral,Niacinamida + Ácido Salicílico + Argila,5% + 2% + 3%,Disfarça os poros
"""


def importar_tratamento(texto):
    # o CSV real vem com BOM (utf-8-sig); os exemplos tambem, para testar isso
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8-sig') as arquivo:
        arquivo.write(texto)
    try:
        saida = StringIO()
        call_command('import_tratamento_csv', arquivo.name, stdout=saida)
        return saida.getvalue()
    finally:
        os.unlink(arquivo.name)


class ImportarTratamentoTests(TestCase):
    def test_importa_as_linhas_do_csv(self):
        saida = importar_tratamento(CSV_TRATAMENTO)
        self.assertEqual(RegraTratamento.objects.count(), 3)
        self.assertIn('3 criadas, 0 atualizadas', saida)

    def test_converte_os_campos_para_o_formato_do_perfil(self):
        importar_tratamento(CSV_TRATAMENTO)
        acne = RegraTratamento.objects.get(preocupacao='Acne')
        self.assertEqual(acne.faixa_etaria, '14-18')
        self.assertEqual(acne.tipo_pele, 'oleosa')       # minusculo, como no perfil
        self.assertIs(acne.sensibilidade, True)          # "Sim"
        self.assertEqual(acne.tipo_interno, 'Comedonal')
        self.assertEqual(acne.efeitos, 'Controla o sebo, Desobstrui os poros')
        rugas = RegraTratamento.objects.get(preocupacao='Rugas')
        self.assertIs(rugas.sensibilidade, False)        # "Não"
        self.assertEqual(rugas.tipo_interno, '')         # "Geral" vira vazio

    def test_rodar_duas_vezes_nao_duplica(self):
        importar_tratamento(CSV_TRATAMENTO)
        saida = importar_tratamento(CSV_TRATAMENTO)
        self.assertEqual(RegraTratamento.objects.count(), 3)
        self.assertIn('0 criadas, 3 atualizadas', saida)

    def test_reimportar_atualiza_os_ingredientes(self):
        importar_tratamento(CSV_TRATAMENTO)
        importar_tratamento(CSV_TRATAMENTO.replace('Estimula o colágeno', 'Efeito novo'))
        self.assertEqual(RegraTratamento.objects.get(preocupacao='Rugas').efeitos, 'Efeito novo')
        self.assertEqual(RegraTratamento.objects.count(), 3)

    def test_concentracoes_nao_sao_importadas(self):
        importar_tratamento(CSV_TRATAMENTO)
        campos = {campo.name for campo in RegraTratamento._meta.get_fields()}
        self.assertFalse(any('concentra' in campo for campo in campos))
        for regra in RegraTratamento.objects.all():
            self.assertNotIn('%', regra.ingredientes + regra.efeitos)

    def test_lista_ingredientes(self):
        importar_tratamento(CSV_TRATAMENTO)
        regra = RegraTratamento.objects.get(preocupacao='Rugas')
        self.assertEqual(regra.lista_ingredientes(), ['Retinol', 'Ácido Hialurônico', 'Peptídeos'])

    def test_arquivo_inexistente(self):
        with self.assertRaises(CommandError):
            call_command('import_tratamento_csv', '/caminho/que/nao/existe.csv')

    def test_csv_sem_coluna_obrigatoria(self):
        with self.assertRaises(CommandError):
            importar_tratamento('Faixa_Etária,Tipo_de_Pele\n14-18,Oleosa\n')

    def test_sensibilidade_invalida_nao_importa_nada(self):
        linhas = CSV_TRATAMENTO.splitlines()
        linhas[3] = linhas[3].replace(',Não,Poros', ',Talvez,Poros')
        with self.assertRaises(CommandError):
            importar_tratamento('\n'.join(linhas))
        self.assertEqual(RegraTratamento.objects.count(), 0)  # tudo ou nada

    def test_dataset_completo_tem_1120_regras(self):
        caminho = os.path.join(settings.BASE_DIR, 'core', 'data', 'tratamento_skincare.csv')
        call_command('import_tratamento_csv', caminho, stdout=StringIO())
        self.assertEqual(RegraTratamento.objects.count(), 1120)
        call_command('import_tratamento_csv', caminho, stdout=StringIO())
        self.assertEqual(RegraTratamento.objects.count(), 1120)
        # nenhuma regra com tipo de pele fora dos valores do perfil
        tipos = set(RegraTratamento.objects.values_list('tipo_pele', flat=True))
        self.assertEqual(tipos, {'normal', 'seca', 'oleosa', 'mista'})


# ==========================================================================
# MOTOR DA ROTINA - montar_rotina, regras de seguranca e pontuacao
# ==========================================================================

CHAVES_DO_DASHBOARD = {'class', 'title', 'description', 'time', 'action', 'icon'}


def criar_produto(nome, categoria, ingrediente, tipo_pele='Todos os tipos', modo_uso='Manhã e Noite',
                  preocupacao='Hidratação', avaliacao=None):
    return Produto.objects.create(
        marca='Marca', nome=nome, categoria=categoria, tipo_pele=tipo_pele, preocupacao=preocupacao,
        modo_uso=modo_uso, ingrediente_principal=ingrediente, tamanho_ml=50, avaliacao=avaliacao,
        slug=nome.lower().replace(' ', '-'),
    )


def criar_regra(faixa, tipo_pele, preocupacao, ingredientes, tipo_interno='', sensibilidade=False,
                subtipo='Subtipo A'):
    return RegraTratamento.objects.create(
        faixa_etaria=faixa, tipo_pele=tipo_pele, subtipo_pele=subtipo, sensibilidade=sensibilidade,
        preocupacao=preocupacao, tipo_interno=tipo_interno, ingredientes=ingredientes,
    )


def novo_perfil(**campos):
    # perfil sem salvar no banco: o motor so le os atributos (e assim o teste nao cria usuario/senha)
    dados = dict(
        idade=25, tipo_pele='oleosa', alergias='', objetivo='reduzir acne', preferencia_produto='gel',
        usa_maquiagem_diariamente='nao', porcentagem_saude=70,
    )
    dados.update(campos)
    return PerfilDermatologico(**dados)


def titulos(passos):
    return [passo['title'] for passo in passos]


def produtos_dos_passos(passos):
    return [passo['produto_id'] for passo in passos]


def textos(rotina):
    return [aviso['texto'] for aviso in rotina['avisos']]


class MotorBase(TestCase):
    # catalogo pequeno, com um produto de cada passo
    def setUp(self):
        self.limpeza = criar_produto('Gel de Limpeza', 'Limpeza', 'Glicerina')
        self.micelar = criar_produto('Agua Micelar', 'Água Micelar', 'Água Micelar')
        self.hidratante = criar_produto('Hidratante Basico', 'Hidratante', 'Ceramidas')
        self.protetor = criar_produto('Protetor FPS', 'Protetor Solar', 'Filtro Solar', modo_uso='Manhã')


class RotinaEstruturaTests(MotorBase):
    def test_formato_do_dashboard_e_passos_do_esqueleto(self):
        rotina = montar_rotina(novo_perfil(objetivo='hidratar'))
        self.assertEqual(set(rotina), {'manha', 'noite', 'avisos'})
        for passo in rotina['manha'] + rotina['noite']:
            self.assertTrue(CHAVES_DO_DASHBOARD <= set(passo))
            self.assertIn('produto_id', passo)
        # "hidratar" nao tem regra: o passo de tratamento usa o melhor produto pela pontuacao
        # (aqui o catalogo nao tem serum, entao o passo aparece generico)
        self.assertEqual(titulos(rotina['manha']), ['Limpeza', 'Tratamento', 'Hidratante', 'Protetor solar'])
        self.assertEqual(titulos(rotina['noite']), ['Limpeza e demaquilante', 'Tratamento', 'Hidratante'])  # sem protetor

    def test_sem_perfil_devolve_rotina_vazia(self):
        self.assertEqual(montar_rotina(None), {'manha': [], 'noite': [], 'avisos': []})

    def test_sempre_traz_o_aviso_de_conferir_o_rotulo_por_ultimo(self):
        rotina = montar_rotina(novo_perfil())
        self.assertIn('Confira o rótulo', rotina['avisos'][-1]['texto'])

    def test_protetor_so_de_manha_e_ids_dos_produtos(self):
        rotina = montar_rotina(novo_perfil(objetivo='hidratar'))
        self.assertEqual(rotina['manha'][-1]['produto_id'], self.protetor.id)
        self.assertEqual(rotina['manha'][2]['produto_id'], self.hidratante.id)

    def test_passo_sem_produto_compativel_aparece_generico(self):
        Produto.objects.all().delete()
        rotina = montar_rotina(novo_perfil(objetivo='hidratar', tipo_pele='seca'))
        self.assertEqual(titulos(rotina['manha']), ['Limpeza', 'Tratamento', 'Hidratante', 'Protetor solar'])
        for passo in rotina['manha'] + rotina['noite']:
            self.assertIsNone(passo['produto_id'])
        self.assertEqual(rotina['manha'][2]['description'], 'Hidratante para pele seca')

    def test_so_usa_produtos_do_periodo(self):
        criar_produto('Limpeza Noturna', 'Limpeza', 'Glicerina', modo_uso='Noite', avaliacao=5)
        rotina = montar_rotina(novo_perfil(objetivo='hidratar'))
        self.assertNotIn('Limpeza Noturna', rotina['manha'][0]['description'])

    def test_quem_usa_maquiagem_prefere_agua_micelar_a_noite(self):
        rotina = montar_rotina(novo_perfil(objetivo='hidratar', usa_maquiagem_diariamente='sim'))
        self.assertEqual(rotina['noite'][0]['produto_id'], self.micelar.id)


class RotinaCasosTests(MotorBase):
    def test_a_adolescente_de_16_com_acne_e_pele_oleosa(self):
        # candidata boa: 2 ativos seguros com produto. candidata ruim: retinol (bloqueado aos 16) + sem produto
        criar_regra('14-18', 'oleosa', 'Acne', 'PCA de Zinco + Peróxido de Benzoíla + Ácido Salicílico',
                    tipo_interno='Comedonal', subtipo='Oleosa a Normal')
        criar_regra('14-18', 'oleosa', 'Acne', 'Retinol + Ácido Azelaico + Vitamina K',
                    tipo_interno='Comedonal', subtipo='Extremamente Oleosa')
        zinco = criar_produto('Serum Zinco', 'Sérum', 'Zinco PCA')   # nome do catalogo difere do dataset
        salicilico = criar_produto('Tonico Salicilico', 'Tônico', 'Ácido Salicílico', modo_uso='Noite')
        retinol = criar_produto('Serum Retinol', 'Sérum', 'Retinol', modo_uso='Noite')

        rotina = montar_rotina(novo_perfil(idade=16))

        tratamento_manha = [p['produto_id'] for p in rotina['manha'] if p['title'] == 'Tratamento']
        tratamento_noite = [p['produto_id'] for p in rotina['noite'] if p['title'] == 'Tratamento']
        self.assertEqual(tratamento_manha, [zinco.id])               # o salicilico e so de noite
        self.assertEqual(tratamento_noite, [zinco.id, salicilico.id])
        self.assertNotIn(retinol.id, tratamento_noite)
        # nao sabe o tipo de acne: usou Comedonal e avisou
        self.assertTrue(any('tipo de acne' in t for t in textos(rotina)))

    def test_a_tipo_de_acne_do_perfil_escolhe_a_regra(self):
        criar_regra('14-18', 'oleosa', 'Acne', 'Niacinamida + Retinol + Argila', tipo_interno='Comedonal')
        criar_regra('14-18', 'oleosa', 'Acne', 'Ácido Salicílico + Vitamina C + Argila', tipo_interno='Inflamatória')
        niacinamida = criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')
        salicilico = criar_produto('Serum Salicilico', 'Sérum', 'Ácido Salicílico')
        comedonal = montar_rotina(novo_perfil(idade=16, tipo_acne=''))
        inflamatoria = montar_rotina(novo_perfil(idade=16, tipo_acne='Inflamatória', objetivo='reduzir acne'))
        self.assertIn(niacinamida.id, produtos_dos_passos(comedonal['manha']))
        self.assertIn(salicilico.id, produtos_dos_passos(inflamatoria['manha']))
        self.assertFalse(any('tipo de acne' in t for t in textos(inflamatoria)))

    def test_acne_nodular_ou_cistica_mostra_aviso_destacado(self):
        for tipo in ('Nodular', 'Cística'):
            rotina = montar_rotina(novo_perfil(idade=20, tipo_acne=tipo))
            destacados = [a['texto'] for a in rotina['avisos'] if a['destaque']]
            self.assertEqual(len(destacados), 1)
            self.assertIn('dermatologista', destacados[0])
            self.assertEqual(rotina['avisos'][0]['texto'], destacados[0])  # destaque vem primeiro

    def test_b_gestante_com_manchas_sem_retinol_e_com_aviso(self):
        criar_regra('25-36', 'mista', 'Manchas Escuras', 'Retinol + Vitamina C + Ácido Glicólico')
        retinol = criar_produto('Serum Retinol', 'Sérum', 'Retinol')
        vitamina = criar_produto('Serum Vitamina C', 'Sérum', 'Vitamina C')
        glicolico = criar_produto('Esfoliante Glicolico', 'Esfoliante', 'Ácido Glicólico', modo_uso='Noite')

        rotina = montar_rotina(novo_perfil(idade=30, tipo_pele='mista', objetivo='reduzir manchas', gestante=True))

        usados = produtos_dos_passos(rotina['manha']) + produtos_dos_passos(rotina['noite'])
        self.assertNotIn(retinol.id, usados)
        self.assertIn(vitamina.id, usados)
        self.assertIn(glicolico.id, usados)  # acido pode, mas com aviso
        self.assertTrue(any('Retinol' in t and 'gravidez' in t for t in textos(rotina)))
        self.assertTrue(any('grávida' in t and 'médico' in t for t in textos(rotina)))

    def test_retinol_nao_aparece_antes_dos_18_anos(self):
        criar_regra('14-18', 'oleosa', 'Rugas', 'Retinol + Niacinamida + Argila')
        retinol = criar_produto('Serum Retinol', 'Sérum', 'Retinol')
        rotina = montar_rotina(novo_perfil(idade=17, objetivo='anti-idade'))
        todos = produtos_dos_passos(rotina['manha']) + produtos_dos_passos(rotina['noite'])
        self.assertNotIn(retinol.id, todos)
        self.assertTrue(any('Retinol' in t and '18 anos' in t for t in textos(rotina)))
        # aos 18 ja pode
        criar_regra('14-18', 'oleosa', 'Rugas', 'Retinol + Niacinamida + Argila', tipo_interno='x')  # nao interfere
        rotina18 = montar_rotina(novo_perfil(idade=18, objetivo='anti-idade'))
        self.assertIn(retinol.id, produtos_dos_passos(rotina18['noite']))

    def test_c_50_anos_pele_seca_rugas_retinol_so_a_noite(self):
        criar_regra('45+', 'seca', 'Rugas', 'Retinol + Peptídeos + Ácido Hialurônico')
        retinol = criar_produto('Serum Retinol', 'Sérum', 'Retinol', modo_uso='Manhã e Noite')
        hialuronico = criar_produto('Serum Hialuronico', 'Sérum', 'Ácido Hialurônico')
        peptideos = criar_produto('Serum Peptideos', 'Sérum', 'Peptídeos')

        rotina = montar_rotina(novo_perfil(idade=50, tipo_pele='seca', objetivo='anti-idade'))

        self.assertNotIn(retinol.id, produtos_dos_passos(rotina['manha']))   # retinol nunca de manha
        self.assertIn(retinol.id, produtos_dos_passos(rotina['noite']))
        tratamentos_noite = [p for p in rotina['noite'] if p['title'] == 'Tratamento']
        self.assertEqual(len(tratamentos_noite), 2)                          # no maximo 2
        self.assertEqual(sorted(produtos_dos_passos(tratamentos_noite)), sorted([retinol.id, peptideos.id]))
        tratamentos_manha = [p['produto_id'] for p in rotina['manha'] if p['title'] == 'Tratamento']
        self.assertEqual(tratamentos_manha, [peptideos.id, hialuronico.id])

    def test_ativos_que_conflitam_no_mesmo_periodo_mantem_o_primeiro(self):
        criar_regra('25-36', 'oleosa', 'Rugas', 'Retinol + Vitamina C + Niacinamida')
        retinol = criar_produto('Serum Retinol', 'Sérum', 'Retinol', modo_uso='Noite')
        vitamina = criar_produto('Serum Vitamina C', 'Sérum', 'Vitamina C')
        niacinamida = criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')

        rotina = montar_rotina(novo_perfil(idade=30, objetivo='anti-idade'))

        noite = produtos_dos_passos(rotina['noite'])
        self.assertIn(retinol.id, noite)
        self.assertNotIn(vitamina.id, noite)                 # conflita com o retinol
        self.assertIn(niacinamida.id, noite)                 # o proximo ativo ocupa a vaga
        self.assertIn(vitamina.id, produtos_dos_passos(rotina['manha']))  # de manha nao ha retinol
        self.assertTrue(any('noites alternadas' in t for t in textos(rotina)))

    def test_d_alergia_a_niacinamida_tira_o_ativo_e_os_produtos(self):
        criar_regra('25-36', 'oleosa', 'Acne', 'Niacinamida + Ácido Salicílico + Argila', tipo_interno='Comedonal')
        niacinamida = criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')
        salicilico = criar_produto('Serum Salicilico', 'Sérum', 'Ácido Salicílico')
        criar_produto('Hidratante Niacinamida', 'Hidratante', 'Niacinamida', avaliacao=5)

        for alergias in ('NIACINAMIDA', 'fragrância, niacinamida'):
            rotina = montar_rotina(novo_perfil(idade=30, alergias=alergias))
            usados = produtos_dos_passos(rotina['manha']) + produtos_dos_passos(rotina['noite'])
            self.assertNotIn(niacinamida.id, usados)
            self.assertIn(salicilico.id, usados)
            descricoes = ' '.join(p['description'] for p in rotina['manha'] + rotina['noite'])
            self.assertNotIn('Niacinamida', descricoes)       # nem o hidratante com o ativo
            self.assertTrue(any('Niacinamida' in t and 'alergias' in t for t in textos(rotina)))

    def test_alergia_ignora_acento_e_maiuscula_e_respostas_sim_nao(self):
        self.assertTrue(ativo_nas_alergias('Ácido Salicílico', 'acido SALICILICO'))
        self.assertTrue(ativo_nas_alergias('PCA de Zinco', 'zinco pca'))   # sinonimo do catalogo
        self.assertFalse(ativo_nas_alergias('Niacinamida', 'fragrância'))
        self.assertFalse(ativo_nas_alergias('Niacinamida', '1'))
        self.assertFalse(ativo_nas_alergias('Niacinamida', '0'))
        self.assertFalse(ativo_nas_alergias('Niacinamida', None))

    def test_alergia_sim_sem_descricao_gera_aviso(self):
        rotina = montar_rotina(novo_perfil(alergias='1'))
        self.assertTrue(any('não descreveu' in t for t in textos(rotina)))

    def test_e_pele_sensivel_prefere_produto_sensivel(self):
        criar_regra('25-36', 'oleosa', 'Acne', 'Niacinamida + Argila + Retinol', tipo_interno='Comedonal',
                    sensibilidade=True)
        criar_regra('25-36', 'oleosa', 'Acne', 'Niacinamida + Argila + Retinol', tipo_interno='Comedonal',
                    sensibilidade=False)
        comum = criar_produto('Serum Comum', 'Sérum', 'Niacinamida', avaliacao=5)
        sensivel = criar_produto('Serum Sensivel', 'Sérum', 'Niacinamida', tipo_pele='Todos os tipos;Sensível', avaliacao=3)

        com = montar_rotina(novo_perfil(idade=30, sensibilidade=True))
        sem = montar_rotina(novo_perfil(idade=30, sensibilidade=False))
        self.assertIn(sensivel.id, produtos_dos_passos(com['manha']))
        self.assertNotIn(comum.id, produtos_dos_passos(com['manha']))
        self.assertIn(comum.id, produtos_dos_passos(sem['manha']))   # sem sensibilidade vale a pontuacao

    def test_produto_so_para_sensivel_nao_serve_a_quem_nao_e_sensivel(self):
        exclusivo = criar_produto('Hidratante Sensivel', 'Hidratante', 'Pantenol', tipo_pele='Sensível', avaliacao=5)
        sem = montar_rotina(novo_perfil(objetivo='hidratar', sensibilidade=False))
        com = montar_rotina(novo_perfil(objetivo='hidratar', sensibilidade=True))
        self.assertNotIn(exclusivo.id, produtos_dos_passos(sem['manha']))
        self.assertIn(exclusivo.id, produtos_dos_passos(com['manha']))

    def test_f_preocupacao_sem_produto_no_catalogo_nao_gera_erro(self):
        criar_regra('25-36', 'oleosa', 'Acne', 'Ácido Azelaico + Peróxido de Benzoíla + Vitamina K',
                    tipo_interno='Comedonal')
        serum = criar_produto('Serum Qualquer', 'Sérum', 'Glicerina', preocupacao='Acne')
        rotina = montar_rotina(novo_perfil(idade=30))
        # nenhum ativo da regra tem produto: usa o melhor produto de tratamento pela pontuacao
        self.assertEqual([p['produto_id'] for p in rotina['manha'] if p['title'] == 'Tratamento'], [serum.id])

    def test_f_sem_nenhum_produto_de_tratamento_o_passo_fica_generico(self):
        criar_regra('25-36', 'oleosa', 'Acne', 'Ácido Azelaico + Peróxido de Benzoíla + Vitamina K',
                    tipo_interno='Comedonal')
        rotina = montar_rotina(novo_perfil(idade=30))
        tratamento = [p for p in rotina['manha'] if p['title'] == 'Tratamento']
        self.assertEqual(len(tratamento), 1)
        self.assertIsNone(tratamento[0]['produto_id'])
        self.assertIn('Ácido Azelaico', tratamento[0]['description'])

    def test_f_sem_regra_para_o_perfil_nao_gera_erro(self):
        # nenhuma RegraTratamento criada
        rotina = montar_rotina(novo_perfil(idade=30, objetivo='anti-idade'))
        self.assertEqual(titulos(rotina['manha'])[0], 'Limpeza')

    def test_g_perfil_de_12_anos_so_limpeza_hidratante_e_protetor(self):
        criar_regra('14-18', 'oleosa', 'Acne', 'Niacinamida + Argila + Ácido Salicílico', tipo_interno='Comedonal')
        criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')
        criar_produto('Hidratante Salicilico', 'Hidratante', 'Ácido Salicílico', avaliacao=5)
        rotina = montar_rotina(novo_perfil(idade=12))
        self.assertEqual(titulos(rotina['manha']), ['Limpeza', 'Hidratante', 'Protetor solar'])
        self.assertEqual(titulos(rotina['noite']), ['Limpeza e demaquilante', 'Hidratante'])
        self.assertEqual(rotina['avisos'][0]['destaque'], True)
        self.assertIn('médico', rotina['avisos'][0]['texto'])
        self.assertNotIn('Salicílico', ' '.join(p['description'] for p in rotina['manha'] + rotina['noite']))

    def test_gestante_nao_ganha_acido_que_a_regra_nao_pediu(self):
        criar_produto('Hidratante Salicilico', 'Hidratante', 'Ácido Salicílico', avaliacao=5)
        rotina = montar_rotina(novo_perfil(objetivo='hidratar', gestante=True))
        self.assertEqual(rotina['manha'][2]['produto_id'], self.hidratante.id)

    def test_mesmo_ativo_nao_se_repete_no_periodo(self):
        criar_regra('25-36', 'oleosa', 'Acne', 'Ácido Salicílico + Argila + Niacinamida', tipo_interno='Comedonal')
        criar_produto('Tonico Salicilico', 'Tônico', 'Ácido Salicílico')
        criar_produto('Hidratante Salicilico', 'Hidratante', 'Ácido Salicílico', avaliacao=5)
        rotina = montar_rotina(novo_perfil(idade=30))
        descricoes = [p['description'] for p in rotina['manha']]
        self.assertEqual(sum('Salicílico' in d or 'Salicilico' in d for d in descricoes), 1)


class FaixasEPreocupacoesTests(TestCase):
    def test_faixa_etaria(self):
        casos = {13: None, 14: '14-18', 18: '14-18', 19: '19-24', 24: '19-24', 25: '25-36', 36: '25-36',
                 37: '37-45', 45: '37-45', 46: '45+', 80: '45+'}
        for idade, esperada in casos.items():
            self.assertEqual(faixa_etaria(idade), esperada, idade)

    def test_objetivo_primeiro_e_depois_um_sinal_facial(self):
        sinais = {'oleosidade_alta': True, 'manchas_presentes': True, 'acne_presente': True}
        perfil = novo_perfil(objetivo='anti-idade')
        # maximo 2: o objetivo e o sinal de maior prioridade (acne)
        self.assertEqual(preocupacoes_de_tratamento(perfil, sinais), ['Rugas', 'Acne'])

    def test_sinal_igual_ao_objetivo_nao_repete(self):
        perfil = novo_perfil(objetivo='reduzir acne')
        self.assertEqual(preocupacoes_de_tratamento(perfil, {'acne_presente': True, 'manchas_presentes': True}),
                         ['Acne', 'Manchas Escuras'])

    def test_hidratar_e_outro_nao_tem_regra_mas_aceitam_sinais(self):
        self.assertEqual(preocupacoes_de_tratamento(novo_perfil(objetivo='hidratar'), {}), [])
        self.assertEqual(preocupacoes_de_tratamento(novo_perfil(objetivo='outro'), {'baixa_hidratacao': True}), [])
        self.assertEqual(
            preocupacoes_de_tratamento(novo_perfil(objetivo='hidratar'), {'textura_irregular': True, 'oleosidade_alta': True}),
            ['Cravos Brancos / Cravos Pretos', 'Poros Dilatados'])

    def test_cada_objetivo_do_mapa_vira_uma_preocupacao_de_tratamento(self):
        esperado = {'reduzir acne': 'Acne', 'reduzir manchas': 'Manchas Escuras', 'anti-idade': 'Rugas',
                    'controlar oleosidade': 'Poros Dilatados', 'melhorar textura': 'Cravos Brancos / Cravos Pretos'}
        for objetivo, preocupacao in esperado.items():
            self.assertEqual(preocupacoes_de_tratamento(novo_perfil(objetivo=objetivo), {}), [preocupacao])

    def test_sinal_facial_da_youcam_vira_preocupacao_na_rotina(self):
        criar_regra('25-36', 'oleosa', 'Poros Dilatados', 'Niacinamida + Argila + Ácido Salicílico')
        criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')
        formato_lista = json.dumps({'output': [{'type': 'oiliness', 'ui_score': 40}]})
        rotina = montar_rotina(novo_perfil(idade=30, objetivo='hidratar', dados_ia=formato_lista))
        self.assertIn('Tratamento', titulos(rotina['manha']))
        self.assertIn('Niacinamida', ' '.join(p['description'] for p in rotina['manha']))


class SinaisFaciaisTests(TestCase):
    # extrair_sinais_faciais precisa entender os mesmos formatos do scanner
    def test_formatos_da_youcam(self):
        formatos = {
            'simples': {'oiliness': {'score': 40}},
            'lista': {'output': [{'type': 'oiliness', 'ui_score': 40}]},
            'hd_e_whole': {'hd_oiliness': {'whole': {'ui_score': 40}}},
            'score_info': {'score_info': {'oiliness': {'ui_score': 40}}},
            'numero_puro': {'oiliness': 40},
        }
        for nome, dados in formatos.items():
            self.assertEqual(extrair_sinais_faciais(json.dumps(dados)), {'oleosidade_alta': True}, nome)

    def test_nota_boa_nao_gera_sinal(self):
        dados = {'oiliness': {'ui_score': 90}, 'acne': {'ui_score': 70}, 'moisture': {'ui_score': 49}}
        self.assertEqual(extrair_sinais_faciais(json.dumps(dados)), {'baixa_hidratacao': True})

    def test_json_invalido_ou_estranho_nao_quebra(self):
        for bruto in (None, '', 'isto nao e json', '[]', '42', 'null'):
            self.assertEqual(extrair_sinais_faciais(bruto), {})


class PontuacaoTests(TestCase):
    def test_pontuar_produto(self):
        produto = criar_produto('Serum', 'Sérum', 'Niacinamida', preocupacao='Acne;Oleosidade', avaliacao=4)
        self.assertAlmostEqual(pontuar_produto(produto, {'Acne', 'Oleosidade'}, {'Sérum'}), 2 * 2 + 1 + 0.4)
        self.assertEqual(pontuar_produto(produto, set(), set()), 0.4)
        sem_nota = criar_produto('Serum 2', 'Sérum', 'Niacinamida')
        self.assertEqual(pontuar_produto(sem_nota, {'Acne'}, set()), 0)

    def test_h_recomendar_produtos_continua_ordenando_como_antes(self):
        criar_produto('Gel Oleosidade', 'Limpeza', 'Glicerina', tipo_pele='Oleosa', preocupacao='Oleosidade;Acne', avaliacao=3)
        criar_produto('Serum Acne', 'Sérum', 'Ácido Salicílico', tipo_pele='Oleosa', preocupacao='Acne', avaliacao=5)
        criar_produto('Hidratante Creme', 'Hidratante', 'Ceramidas', tipo_pele='Todos os tipos', preocupacao='Hidratação', avaliacao=4)
        criar_produto('Creme Seca', 'Hidratante', 'Karité', tipo_pele='Seca', preocupacao='Acne', avaliacao=5)   # fora: tipo de pele
        criar_produto('Creme Madura', 'Hidratante', 'Colágeno', tipo_pele='Madura', preocupacao='Rugas e Linhas Finas')
        perfil = novo_perfil(idade=25, tipo_pele='oleosa', objetivo='reduzir acne', preferencia_produto='creme')
        # preocupacao do objetivo = {Acne}. Pontos: Serum Acne = 2+0+0.5 | Gel Oleosidade = 2+0+0.3
        # | Hidratante Creme = 0+1 (creme preferido)+0.4. 'Creme Seca' (tipo) e 'Creme Madura' (idade) ficam fora.
        self.assertEqual([p.nome for p in recomendar_produtos(perfil)], ['Serum Acne', 'Gel Oleosidade', 'Hidratante Creme'])
        self.assertEqual([p.nome for p in recomendar_produtos(perfil, limite=2)], ['Serum Acne', 'Gel Oleosidade'])

    def test_h_recomendar_produtos_sem_perfil_e_madura(self):
        criar_produto('A', 'Sérum', 'X', tipo_pele='Madura')
        criar_produto('B', 'Sérum', 'Y', tipo_pele='Oleosa')
        self.assertEqual(len(recomendar_produtos(None)), 2)
        madura = novo_perfil(idade=45, tipo_pele='normal', objetivo='hidratar')
        self.assertEqual([p.nome for p in recomendar_produtos(madura)], ['A'])


# ==========================================================================
# DASHBOARD - mostra a rotina real (Manha/Noite), os avisos e o link dos produtos
# ==========================================================================

class DashboardRotinaTests(PerfilBaseTestCase):
    # usuario logado da PerfilBaseTestCase: 27 anos, pele mista, objetivo "controlar oleosidade"
    def setUp(self):
        super().setUp()
        self.limpeza = criar_produto('Gel de Limpeza', 'Limpeza', 'Glicerina')
        self.hidratante = criar_produto('Hidratante Basico', 'Hidratante', 'Ceramidas')
        self.protetor = criar_produto('Protetor FPS', 'Protetor Solar', 'Filtro Solar', modo_uso='Manhã')
        criar_regra('25-36', 'mista', 'Poros Dilatados', 'Niacinamida + Argila + Ácido Salicílico')
        self.serum = criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida', modo_uso='Manhã e Noite')

    def abrir(self):
        return self.client.get(reverse('dashboard'))

    def test_mostra_produtos_reais_e_nao_o_texto_fixo(self):
        resposta = self.abrir()
        self.assertContains(resposta, 'Marca - Serum Niacinamida (Niacinamida)')
        self.assertContains(resposta, 'Marca - Hidratante Basico')
        self.assertNotContains(resposta, 'Rotina de limpeza diária')
        self.assertNotContains(resposta, 'Sérum calmante')

    def test_produto_vira_link_para_a_tela_de_detalhe(self):
        resposta = self.abrir()
        url = reverse('produto_detalhe', args=[self.serum.id])
        self.assertContains(resposta, f'<a href="{url}">Marca - Serum Niacinamida (Niacinamida)</a>', html=True)

    def test_as_duas_abas_chegam_ao_javascript_com_as_chaves_e_a_url(self):
        resposta = self.abrir()
        for chave, titulo_noite in (('rotina_manha', 'Limpeza'), ('rotina_noite', 'Limpeza e demaquilante')):
            passos = resposta.context[chave]
            self.assertEqual(passos[0]['title'], titulo_noite)
            for passo in passos:
                self.assertTrue(CHAVES_DO_DASHBOARD | {'produto_id', 'url'} <= set(passo))
        self.assertContains(resposta, 'id="rotina-manha-data"')
        self.assertContains(resposta, 'id="rotina-noite-data"')
        self.assertEqual(resposta.context['rotina_manha'][-1]['title'], 'Protetor solar')   # so de manha
        self.assertNotEqual(resposta.context['rotina_noite'][-1]['title'], 'Protetor solar')

    def test_passo_sem_produto_aparece_generico_e_sem_link(self):
        Produto.objects.filter(categoria='Hidratante').delete()
        resposta = self.abrir()
        self.assertContains(resposta, 'Hidratante para pele mista')
        passo = [p for p in resposta.context['rotina_manha'] if p['title'] == 'Hidratante'][0]
        self.assertIsNone(passo['produto_id'])
        self.assertIsNone(passo['url'])

    def test_avisos_e_aviso_fixo_do_dermatologista(self):
        resposta = self.abrir()
        self.assertContains(resposta, 'Confira o rótulo')
        self.assertContains(resposta, 'Esta rotina não substitui a avaliação de um dermatologista.')

    def test_aviso_destacado_para_acne_nodular(self):
        self.perfil.objetivo = 'reduzir acne'
        self.perfil.tipo_acne = 'Nodular'
        self.perfil.save()
        resposta = self.abrir()
        self.assertContains(resposta, 'class="rotina-aviso destaque"')
        self.assertContains(resposta, 'Acne nodular: procure um dermatologista')

    def test_sem_acne_nodular_nao_ha_aviso_destacado(self):
        self.assertNotContains(self.abrir(), 'rotina-aviso destaque')

    def test_menor_de_14_anos_ve_so_limpeza_hidratante_e_protetor(self):
        self.perfil.idade = 12
        self.perfil.save()
        resposta = self.abrir()
        self.assertEqual([p['title'] for p in resposta.context['rotina_manha']], ['Limpeza', 'Hidratante', 'Protetor solar'])
        self.assertContains(resposta, 'class="rotina-aviso destaque"')

    def test_sem_perfil_mostra_a_mensagem_atual(self):
        self.client.logout()
        criar_usuario_com_perfil('sem-perfil@email.com', com_perfil=False)
        self.client.login(username='sem-perfil@email.com', password=SENHA)
        resposta = self.abrir()
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum perfil encontrado')
        self.assertNotContains(resposta, 'rotina-avisos')

    def test_visitante_sem_login_mostra_a_mensagem_atual(self):
        self.client.logout()
        resposta = self.abrir()
        self.assertContains(resposta, 'Nenhum perfil encontrado')
        self.assertEqual(resposta.context['rotina_manha'], [])

    def test_perfil_antigo_sem_campos_novos_abre_o_dashboard(self):
        # usuario que respondeu o questionario antes das Fases 2 e 3: so os padroes dos campos novos
        PerfilDermatologico.objects.filter(pk=self.perfil.pk).update(sensibilidade=False, gestante=False, tipo_acne='')
        self.assertEqual(self.abrir().status_code, 200)


class DashboardFototipoTests(PerfilBaseTestCase):
    # perfil da PerfilBaseTestCase: fototipo 3
    def test_card_mostra_numero_romano_escala_e_texto(self):
        resposta = self.client.get(reverse('dashboard'))
        self.assertContains(resposta, '<h2>Fototipo</h2>', html=True)
        self.assertContains(resposta, '<span class="fototipo-numero">III</span>', html=True)
        self.assertContains(resposta, 'escala Fitzpatrick')
        self.assertContains(resposta, 'Sensibilidade moderada ao sol. Use protetor solar todos os dias, mesmo em dias nublados.')
        self.assertContains(resposta, 'aria-label="Fototipo III de VI na escala Fitzpatrick"')
        self.assertNotContains(resposta, 'Perfil Dermatológico')

    def test_barra_tem_6_tons_e_so_o_do_perfil_esta_marcado(self):
        resposta = self.client.get(reverse('dashboard'))
        tons = resposta.context['fototipo']['tons']
        self.assertEqual([t['romano'] for t in tons], ['I', 'II', 'III', 'IV', 'V', 'VI'])
        self.assertEqual([t['atual'] for t in tons], [False, False, True, False, False, False])
        self.assertContains(resposta, 'class="atual" style="background-color: #c98e63"')
        self.assertContains(resposta, 'background-color: #f3d9c8')   # tom I
        self.assertContains(resposta, 'background-color: #4a2d1b')   # tom VI

    def test_todos_os_fototipos_do_questionario_funcionam(self):
        # o questionario grava de 1 a 6 (reacao_sol + 1)
        for numero, (romano, _, texto) in FOTOTIPOS.items():
            self.perfil.fototipo = numero
            info = _fototipo_do_perfil(self.perfil)
            self.assertEqual(info['romano'], romano)
            self.assertEqual(info['texto'], texto)
            self.assertEqual([t['atual'] for t in info['tons']].index(True), numero - 1)

    def test_sem_fototipo_ou_fora_da_escala_mostra_mensagem_e_nao_quebra(self):
        for valor in (None, 0, 7):
            PerfilDermatologico.objects.filter(pk=self.perfil.pk).update(fototipo=valor)
            resposta = self.client.get(reverse('dashboard'))
            self.assertEqual(resposta.status_code, 200)
            self.assertIsNone(resposta.context['fototipo'])
            self.assertContains(resposta, 'Fototipo não informado')
            self.assertNotContains(resposta, 'fototipo-escala')

    def test_sem_perfil_nao_tem_card(self):
        self.assertIsNone(_fototipo_do_perfil(None))


class TelaProdutosTests(PerfilBaseTestCase):
    def test_nao_mostra_o_alerta_de_uv(self):
        resposta = self.client.get(reverse('produtos'))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'níveis de UV')
        self.assertNotContains(resposta, 'box-alerta-uv')

    def test_continua_mostrando_objetivo_e_filtros(self):
        criar_produto('Gel de Limpeza', 'Limpeza', 'Glicerina', tipo_pele='Mista')
        resposta = self.client.get(reverse('produtos'))
        self.assertContains(resposta, 'Controlar oleosidade')
        self.assertContains(resposta, 'Rotina completa')
        self.assertContains(resposta, 'Gel de Limpeza')


# ==========================================================================
# MARCAR PASSOS COMO FEITOS - POST /rotina/marcar/ e o estado no dashboard
# ==========================================================================

class RotinaMarcarTests(PerfilBaseTestCase):
    # perfil da PerfilBaseTestCase: 27 anos, mista, "controlar oleosidade" -> regra de Poros Dilatados
    def setUp(self):
        super().setUp()
        self.limpeza = criar_produto('Gel de Limpeza', 'Limpeza', 'Glicerina')
        self.hidratante = criar_produto('Hidratante Basico', 'Hidratante', 'Ceramidas')
        self.protetor = criar_produto('Protetor FPS', 'Protetor Solar', 'Filtro Solar', modo_uso='Manhã')
        criar_regra('25-36', 'mista', 'Poros Dilatados', 'Niacinamida + Argila + Ácido Salicílico')
        self.serum = criar_produto('Serum Niacinamida', 'Sérum', 'Niacinamida')
        self.salicilico = criar_produto('Tonico Salicilico', 'Tônico', 'Ácido Salicílico')

    def marcar(self, periodo='manha', titulo='Limpeza', produto_id=None, feito='1', cliente=None):
        dados = {'periodo': periodo, 'titulo': titulo, 'feito': feito}
        dados['produto_id'] = self.limpeza.id if produto_id is None else produto_id
        return (cliente or self.client).post(reverse('rotina_marcar'), dados)

    def estado_dos_passos(self, periodo='rotina_manha'):
        passos = self.client.get(reverse('dashboard')).context[periodo]
        return {(p['title'], p['produto_id']): p['feito'] for p in passos}

    def test_marcar_grava_a_linha_com_a_data_de_hoje(self):
        resposta = self.marcar()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {'ok': True, 'feito': True})
        marca = PassoRotinaFeito.objects.get()
        self.assertEqual((marca.usuario, marca.periodo, marca.titulo, marca.produto_id),
                         (self.usuario, 'manha', 'Limpeza', self.limpeza.id))
        self.assertEqual(marca.data, timezone.localdate())

    def test_dashboard_mostra_feito_em_verde_proximo_em_azul_e_o_resto_neutro(self):
        self.marcar()   # Limpeza feita -> o proximo passo e o primeiro Tratamento
        resposta = self.client.get(reverse('dashboard'))
        passos = resposta.context['rotina_manha']
        self.assertEqual([p['title'] for p in passos], ['Limpeza', 'Tratamento', 'Tratamento', 'Hidratante', 'Protetor solar'])
        self.assertEqual([p['class'] for p in passos],
                         ['completed', 'action-required', 'pending', 'pending', 'pending'])
        self.assertEqual((passos[0]['feito'], passos[0]['icon']), (True, 'fa-check'))
        self.assertEqual((passos[1]['feito'], passos[1]['icon'], passos[1]['action']), (False, None, 'Marcar como feito'))
        self.assertEqual([p['action'] for p in passos[2:]], [None, None, None])
        self.assertContains(resposta, 'class="rotina-item completed"')
        self.assertContains(resposta, 'class="rotina-item action-required"')
        self.assertContains(resposta, 'class="rotina-item pending"')
        self.assertContains(resposta, 'aria-label="Desmarcar Limpeza como feito"')
        self.assertContains(resposta, 'aria-label="Marcar Hidratante como feito"')

    def test_sem_marcar_nada_so_o_primeiro_e_o_proximo_passo(self):
        resposta = self.client.get(reverse('dashboard'))
        for chave in ('rotina_manha', 'rotina_noite'):
            passos = resposta.context[chave]
            self.assertTrue(all(not p['feito'] for p in passos))
            self.assertEqual(passos[0]['class'], 'action-required')
            self.assertEqual({p['class'] for p in passos[1:]}, {'pending'})

    def test_o_proximo_passo_avanca_conforme_marca(self):
        # marcar um passo do meio nao muda o proximo: e sempre o primeiro NAO feito
        self.marcar(titulo='Hidratante', produto_id=self.hidratante.id)
        classes = [p['class'] for p in self.client.get(reverse('dashboard')).context['rotina_manha']]
        self.assertEqual(classes, ['action-required', 'pending', 'pending', 'completed', 'pending'])
        self.marcar()   # Limpeza
        classes = [p['class'] for p in self.client.get(reverse('dashboard')).context['rotina_manha']]
        self.assertEqual(classes, ['completed', 'action-required', 'pending', 'completed', 'pending'])

    def test_com_tudo_feito_nao_ha_proximo_passo(self):
        for passo in self.client.get(reverse('dashboard')).context['rotina_manha']:
            self.marcar(titulo=passo['title'], produto_id=passo['produto_id'] or 0)
        resposta = self.client.get(reverse('dashboard'))
        self.assertEqual({p['class'] for p in resposta.context['rotina_manha']}, {'completed'})
        conteudo = resposta.content.decode()
        lista = conteudo[conteudo.index('id="rotina-lista"'):conteudo.index('id="rotina-erro"')]
        self.assertNotIn('rotina-botao', lista)      # sem botao: nao sobrou passo para fazer
        self.assertEqual(resposta.context['resumo_manha'], {'feitos': 5, 'total': 5, 'porcentagem': 100})

    def test_contador_e_barra_de_progresso(self):
        resposta = self.client.get(reverse('dashboard'))
        self.assertContains(resposta, 'Sua rotina de hoje')
        self.assertNotContains(resposta, 'Status de Hoje')
        self.assertContains(resposta, '0 de 5 passos concluídos')
        self.assertContains(resposta, '<span style="width: 0%"></span>', html=True)
        self.marcar()
        self.marcar(titulo='Hidratante', produto_id=self.hidratante.id)
        resposta = self.client.get(reverse('dashboard'))
        self.assertEqual(resposta.context['resumo_manha'], {'feitos': 2, 'total': 5, 'porcentagem': 40})
        self.assertContains(resposta, '2 de 5 passos concluídos')
        self.assertContains(resposta, 'aria-valuenow="2"')
        self.assertContains(resposta, 'aria-valuemax="5"')
        self.assertContains(resposta, '<span style="width: 40%"></span>', html=True)

    def test_botao_marcar_como_feito_so_no_proximo_passo_e_com_texto_de_proximo(self):
        resposta = self.client.get(reverse('dashboard'))
        self.assertContains(resposta, 'class="rotina-botao"', count=1)
        self.assertContains(resposta, 'Marcar como feito</span>')
        self.assertContains(resposta, 'Próximo passo &middot; <a href=', count=1)

    def test_desmarcar_apaga_a_linha(self):
        self.marcar()
        resposta = self.marcar(feito='0')
        self.assertEqual(resposta.json(), {'ok': True, 'feito': False})
        self.assertEqual(PassoRotinaFeito.objects.count(), 0)
        self.assertFalse(self.estado_dos_passos()[('Limpeza', self.limpeza.id)])

    def test_marcar_duas_vezes_nao_duplica_e_desmarcar_sem_marca_nao_da_erro(self):
        self.marcar()
        self.marcar()
        self.assertEqual(PassoRotinaFeito.objects.count(), 1)
        self.marcar(titulo='Hidratante', produto_id=self.hidratante.id, feito='0')  # nada marcado
        self.assertEqual(PassoRotinaFeito.objects.count(), 1)

    def test_manha_e_noite_sao_marcados_separadamente(self):
        self.marcar(periodo='manha')
        self.assertTrue(self.estado_dos_passos('rotina_manha')[('Limpeza', self.limpeza.id)])
        self.assertFalse(self.estado_dos_passos('rotina_noite')[('Limpeza e demaquilante', self.limpeza.id)]
                         if ('Limpeza e demaquilante', self.limpeza.id) in self.estado_dos_passos('rotina_noite') else False)
        self.marcar(periodo='noite', titulo='Limpeza e demaquilante')
        self.assertEqual(PassoRotinaFeito.objects.count(), 2)

    def test_os_dois_passos_de_tratamento_sao_independentes(self):
        self.marcar(titulo='Tratamento', produto_id=self.serum.id)
        estado = self.estado_dos_passos()
        self.assertTrue(estado[('Tratamento', self.serum.id)])
        self.assertFalse(estado[('Tratamento', self.salicilico.id)])

    def test_passo_sem_produto_usa_produto_id_zero(self):
        Produto.objects.filter(categoria='Hidratante').delete()
        resposta = self.marcar(titulo='Hidratante', produto_id=0)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(PassoRotinaFeito.objects.get().produto_id, 0)
        self.assertTrue(self.estado_dos_passos()[('Hidratante', None)])

    def test_passo_que_nao_existe_na_rotina_e_recusado(self):
        casos = [
            dict(periodo='tarde'),
            dict(titulo='Passo inventado'),
            dict(produto_id=99999),
            dict(periodo='noite', titulo='Protetor solar', produto_id=self.protetor.id),  # protetor so de manha
        ]
        for caso in casos:
            self.assertEqual(self.marcar(**caso).status_code, 400, caso)
        self.assertEqual(PassoRotinaFeito.objects.count(), 0)

    def test_so_aceita_post(self):
        self.assertEqual(self.client.get(reverse('rotina_marcar')).status_code, 405)

    def test_exige_login(self):
        self.client.logout()
        resposta = self.marcar()
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse('login'), resposta.url)
        self.assertEqual(PassoRotinaFeito.objects.count(), 0)

    def test_usuario_sem_perfil_nao_marca(self):
        self.client.logout()
        criar_usuario_com_perfil('sem-perfil@email.com', com_perfil=False)
        self.client.login(username='sem-perfil@email.com', password=SENHA)
        self.assertEqual(self.marcar().status_code, 400)

    def test_exige_o_token_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.login(username='ana@email.com', password=SENHA)
        self.assertEqual(self.marcar(cliente=cliente).status_code, 403)
        self.assertEqual(PassoRotinaFeito.objects.count(), 0)

    def test_marcas_de_outro_dia_nao_aparecem_hoje_mas_ficam_no_historico(self):
        ontem = timezone.localdate() - timedelta(days=1)
        PassoRotinaFeito.objects.create(usuario=self.usuario, data=ontem, periodo='manha',
                                        titulo='Limpeza', produto_id=self.limpeza.id)
        self.assertFalse(self.estado_dos_passos()[('Limpeza', self.limpeza.id)])
        self.marcar()   # hoje
        self.assertEqual(PassoRotinaFeito.objects.count(), 2)
        self.marcar(feito='0')   # desmarcar hoje nao mexe no historico de ontem
        self.assertEqual(PassoRotinaFeito.objects.get().data, ontem)

    def test_marcas_de_outro_usuario_nao_vazam(self):
        outro, _ = criar_usuario_com_perfil('outra@email.com')
        PassoRotinaFeito.objects.create(usuario=outro, data=timezone.localdate(), periodo='manha',
                                        titulo='Limpeza', produto_id=self.limpeza.id)
        self.assertFalse(self.estado_dos_passos()[('Limpeza', self.limpeza.id)])
        self.marcar(feito='0')   # nao apaga a marca da outra pessoa
        self.assertEqual(PassoRotinaFeito.objects.filter(usuario=outro).count(), 1)

    def test_horario_nao_aparece_mais_na_tela_mas_a_chave_time_continua_nos_dados(self):
        resposta = self.client.get(reverse('dashboard'))
        self.assertNotContains(resposta, 'rotina-time')
        conteudo = resposta.content.decode()
        lista = conteudo[conteudo.index('id="rotina-lista"'):conteudo.index('id="rotina-erro"')]
        self.assertNotRegex(lista, r'\d\d:\d\d')   # a lista visivel nao mostra horario
        self.assertTrue(all('time' in p for p in resposta.context['rotina_manha']))

    def test_aviso_do_rotulo_fica_na_nota_abaixo_do_card(self):
        resposta = self.client.get(reverse('dashboard'))
        self.assertNotIn('Confira o rótulo', ' '.join(a['texto'] for a in resposta.context['avisos']))
        conteudo = resposta.content.decode()
        nota = conteudo.index('class="rotina-nota"')
        self.assertIn('Confira o rótulo', conteudo[nota:nota + 400])
        self.assertIn('não substitui a avaliação de um dermatologista', conteudo[nota:nota + 400])
        # a nota vem depois do fim do card da rotina
        self.assertGreater(nota, conteudo.index('id="rotina-manha-data"'))

    def test_sem_avisos_no_card_nao_mostra_a_caixa_vazia(self):
        self.assertEqual(self.client.get(reverse('dashboard')).context['avisos'], [])
        self.assertNotContains(self.client.get(reverse('dashboard')), 'class="rotina-avisos"')

    def test_excluir_a_conta_apaga_as_marcas(self):
        self.marcar()
        self.client.post(reverse('excluir_conta'), {'senha': SENHA, 'confirmacao': 'EXCLUIR'})
        self.assertEqual(PassoRotinaFeito.objects.count(), 0)

    def test_exportar_dados_inclui_historico_e_campos_novos_do_perfil(self):
        ontem = timezone.localdate() - timedelta(days=1)
        PassoRotinaFeito.objects.create(usuario=self.usuario, data=ontem, periodo='noite',
                                        titulo='Hidratante', produto_id=0)
        self.perfil.sensibilidade = True
        self.perfil.tipo_acne = 'Nodular'
        self.perfil.save()
        dados = json.loads(self.client.get(reverse('exportar_dados')).content)
        self.assertEqual(dados['rotina_feita'], [
            {'data': str(ontem), 'periodo': 'noite', 'passo': 'Hidratante', 'produto_id': None}])
        self.assertIs(dados['perfil_dermatologico']['pele_sensivel'], True)
        self.assertIs(dados['perfil_dermatologico']['gestante'], False)
        self.assertEqual(dados['perfil_dermatologico']['tipo_acne'], 'Nodular')
