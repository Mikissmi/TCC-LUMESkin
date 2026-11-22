from django.urls import path
from . import views

urlpatterns = [
    path('', views.tela_cadastro, name='home'),

    path('cadastro/', views.tela_cadastro, name='cadastro_view'),
    
    path('questionario/', views.tela_questionario, name='questionario'),
    
    path('login/', views.tela_login, name='login'),

    path('logout/', views.tela_logout, name='logout'),

    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('rotina/marcar/', views.rotina_marcar, name='rotina_marcar'),

    path('scanner/', views.tela_scanner, name='scanner'),

    path('scanner/analisar/', views.scanner_analisar, name='scanner_analisar'),
    path('scanner/salvar/', views.scanner_salvar, name='scanner_salvar'),

    path('perfil/', views.tela_perfil, name='perfil'),

    path('perfil/editar/', views.editar_perfil, name='editar_perfil'),

    path('perfil/dados/', views.editar_dados_pessoais, name='editar_dados_pessoais'),
    path('perfil/senha/', views.alterar_senha, name='alterar_senha'),
    path('perfil/foto/apagar/', views.apagar_foto_perfil, name='apagar_foto_perfil'),
    path('perfil/exportar/', views.exportar_dados, name='exportar_dados'),
    path('perfil/excluir/', views.excluir_conta, name='excluir_conta'),
    
    path('termosdeuso/', views.tela_termos_de_uso, name='termosdeuso'),
    path('politica-privacidade/', views.tela_politica_privacidade, name='politica_privacidade'),

    path('especialistas/', views.tela_especialistas, name='especialistas'),

    path('produtos/', views.tela_produtos, name='produtos'),
    
    path('produtos/<int:produto_id>/', views.produto_detalhe, name='produto_detalhe'),

    path('artigos/', views.tela_artigos, name='artigos'),

    path('administradores/', views.tela_administradores, name='administradores'),

    path('administradores/artigos/novo/', views.artigo_form, name='artigo_novo'),
    path('administradores/artigos/<int:artigo_id>/editar/', views.artigo_form, name='artigo_editar'),
    path('administradores/artigos/<int:artigo_id>/excluir/', views.artigo_excluir, name='artigo_excluir'),

    path('administradores/especialistas/novo/', views.especialista_form, name='especialista_novo'),
    path('administradores/especialistas/<int:especialista_id>/editar/', views.especialista_form, name='especialista_editar'),
    path('administradores/especialistas/<int:especialista_id>/excluir/', views.especialista_excluir, name='especialista_excluir'),

    path("api/uv/", views.indice_uv, name="indice_uv"),
]