# LUME Skin

Trabalho de Conclusão de Curso do Técnico em Desenvolvimento de Sistemas, IFSul – Câmpus Sapucaia do Sul.

Autoras: Mariana Garibaldi  e Caroline Berghahn 
Orientação: Mateus Raeder e Stefanie Merker
Site publicado: https://lumeskin-eight.vercel.app/

## Sobre o projeto

O LUME Skin é um site que monta uma rotina de skincare para a pessoa. Ela responde um questionário sobre a pele e os hábitos e, se quiser, tira uma foto do rosto para uma análise por inteligência artificial. Com isso o sistema mostra uma rotina de manhã e outra de noite, sugere produtos, calcula uma pontuação de saúde da pele e mostra o índice UV da cidade.

A ideia nasceu de um projeto que fizemos no segundo ano do curso e que refizemos para o TCC. O artigo entregue à banca explica a pesquisa, os requisitos e a avaliação com usuários. Este repositório é o código do protótipo.

## O que o sistema faz

- Cadastro, login e logout de usuários.
- Questionário dermatológico com 11 perguntas (tipo de pele, objetivo, alergias, sensibilidade, gravidez, reação ao sol, sono, exercício, protetor solar, preferência de produto).
- Análise facial opcional pela API da YouCam, só depois de o usuário aceitar o termo de consentimento. A foto não fica guardada, só o resultado e o link da imagem de mapeamento.
- Dashboard com a rotina da manhã e da noite, onde dá para marcar cada passo como feito.
- Catálogo de produtos com filtro por categoria e página de detalhe.
- Artigos científicos e contatos de especialistas.
- Índice UV da região, pela API Open-Meteo.
- Perfil do usuário: editar dados e respostas, trocar senha, apagar a imagem do scan, exportar os dados em JSON e excluir a conta.
- Painel do administrador (`/administradores/`) para cadastrar, editar e excluir artigos e especialistas.

## Tecnologias

Python 3.12+ e Django 6, HTML, CSS e JavaScript puro no front, SQLite no computador local e PostgreSQL quando publicado, WhiteNoise para os arquivos estáticos e Vercel para a hospedagem. Serviços externos: YouCam API (análise de pele) e Open-Meteo (índice UV).

## Como rodar no seu computador

Precisa do Python 3.12 ou mais novo. No PowerShell, dentro da pasta do projeto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py import_produtos_csv core/data/00_produtos_skincare.csv
python manage.py import_tratamento_csv core/data/tratamento_skincare.csv
python manage.py runserver
```

Depois é só abrir http://127.0.0.1:8000.

Os dois comandos `import_*` carregam os 145 produtos e as 1.120 regras de tratamento. Sem eles a página de produtos e a rotina ficam vazias. Dá para rodar de novo sem duplicar nada.

## Arquivo .env

O `.env.example` mostra todas as variáveis. As que importam:

| Variável | Para que serve |
| --- | --- |
| `SECRET_KEY` | chave secreta do Django |
| `DEBUG` | `True` para desenvolver, `False` quando publicado |
| `YOUCAM_API_KEY` | chave da YouCam. Sem ela o scanner não funciona |
| `ADMIN_LOGIN_EMAIL` e `ADMIN_LOGIN_SENHA` | login do painel do administrador |
| `DATABASE_URL` | endereço do PostgreSQL. Sem isso o projeto usa o SQLite local |

O login do administrador não é um cadastro: é um e-mail e uma senha definidos nessas variáveis, usados na mesma tela de login dos usuários.

O scanner tem um limite de 3 análises por hora por usuário, porque cada análise gasta créditos da YouCam.

## Banco de dados

O banco é criado pelo próprio Django com `python manage.py migrate`. Para quem quiser só ler a estrutura, a pasta `banco_de_dados/` tem os scripts SQL (PostgreSQL):

- `01_estrutura.sql`: as 7 tabelas, com comentários
- `02_dados_produtos.sql`: os produtos
- `03_dados_regras_tratamento.sql`: as regras de tratamento

## Organização do código

```
configuracao/     configurações do Django e rotas principais
core/
  models.py       tabelas do banco
  views.py        telas e lógica das páginas
  recomendacao.py monta a rotina e escolhe os produtos
  youcam_utils.py leitura dos resultados da YouCam
  templates/      páginas HTML
  static/         CSS e imagens
  data/           CSVs de produtos e de regras de tratamento
  management/commands/   comandos que importam os CSVs
  tests.py        testes
```

## Publicação

O site fica na Vercel. O `build_files.sh` roda o `collectstatic` e o `migrate`. As variáveis do `.env` são cadastradas em *Environment Variables* no painel da Vercel.

## Aviso

O LUME Skin é um apoio ao cuidado com a pele e não substitui a avaliação de um dermatologista.