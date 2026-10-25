from django.urls import path
from . import views

urlpatterns = [
    # Rota raiz do site direcionando para cadastro
    path('', views.tela_cadastro, name='home'),

    # Rota para a tela de cadastro (página inicial alternativa)
    path('cadastro/', views.tela_cadastro, name='cadastro_view'),
    
    # Rota para o questionário
    path('questionario/', views.tela_questionario, name='questionario'),
    
    # Rota para a página de login
    path('login/', views.tela_login, name='login'),

    # Rota para encerrar a sessão do usuário
    path('logout/', views.tela_logout, name='logout'),

    # Rota para o Dashboard (que criamos nos passos anteriores)
    # Certifique-se de que a função 'dashboard_view' exista no seu views.py
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('rotina/marcar/', views.rotina_marcar, name='rotina_marcar'),

    # Rota para a página do scanner
    path('scanner/', views.tela_scanner, name='scanner'),

    # Rotas do backend do scanner (chamadas via fetch/JS pela core/scanner.html)
    path('scanner/analisar/', views.scanner_analisar, name='scanner_analisar'),
    path('scanner/salvar/', views.scanner_salvar, name='scanner_salvar'),

    # Rota para a página do perfil do usuário
    path('perfil/', views.tela_perfil, name='perfil'),

    # Rota para editar as preferências do perfil do usuário (aba "Perfil de pele")
    path('perfil/editar/', views.editar_perfil, name='editar_perfil'),

    # Rotas das outras abas de perfil e configurações
    path('perfil/dados/', views.editar_dados_pessoais, name='editar_dados_pessoais'),
    path('perfil/senha/', views.alterar_senha, name='alterar_senha'),
    path('perfil/foto/apagar/', views.apagar_foto_perfil, name='apagar_foto_perfil'),
    path('perfil/exportar/', views.exportar_dados, name='exportar_dados'),
    path('perfil/excluir/', views.excluir_conta, name='excluir_conta'),
    
    path('termosdeuso/', views.tela_termos_de_uso, name='termosdeuso'),
    path('politica-privacidade/', views.tela_politica_privacidade, name='politica_privacidade'),

    # Rota para a página dos especialistas
    path('especialistas/', views.tela_especialistas, name='especialistas'),

    # Rota para a página dos produtos recomendados
    path('produtos/', views.tela_produtos, name='produtos'),
    
    # Rota para a página de detalhes de um produto específico
    path('produtos/<int:produto_id>/', views.produto_detalhe, name='produto_detalhe'),

    # Rota para a página dos artigos
    path('artigos/', views.tela_artigos, name='artigos'),

    # Rota para o painel administrativo (gerencia artigos e especialistas)
    path('administradores/', views.tela_administradores, name='administradores'),

    # CRUD de artigos usado pelo painel administrativo
    path('administradores/artigos/novo/', views.artigo_form, name='artigo_novo'),
    path('administradores/artigos/<int:artigo_id>/editar/', views.artigo_form, name='artigo_editar'),
    path('administradores/artigos/<int:artigo_id>/excluir/', views.artigo_excluir, name='artigo_excluir'),

    # CRUD de especialistas usado pelo painel administrativo
    path('administradores/especialistas/novo/', views.especialista_form, name='especialista_novo'),
    path('administradores/especialistas/<int:especialista_id>/editar/', views.especialista_form, name='especialista_editar'),
    path('administradores/especialistas/<int:especialista_id>/excluir/', views.especialista_excluir, name='especialista_excluir'),

    #  Rota para a página da openmeteo
    path("api/uv/", views.indice_uv, name="indice_uv"),
]