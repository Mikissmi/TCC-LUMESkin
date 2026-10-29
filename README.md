# TCC - LUME Skin

Repositório do Trabalho de Conclusão de Curso de Técnico em Desenvolvimento de Sistemas do IFSul – Campus Sapucaia do Sul, desenvolvido pelas alunas Mariana e Caroline para a obtenção do diploma.

O LUME Skin é um aplicativo web (Django) que monta uma rotina de skincare personalizada a partir de um questionário e, opcionalmente, de uma análise facial por inteligência artificial (YouCam). Ele também recomenda produtos, lista artigos científicos e especialistas, e mostra o índice UV da região.

## Executar localmente

Com Python 3.12 ou superior instalado, abra o PowerShell na pasta do projeto e execute:

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

Os dois comandos `import_*` carregam o catálogo de produtos e as regras de tratamento; sem eles a página de produtos e a rotina ficam vazias. Rodar de novo apenas atualiza os dados, sem duplicar.

## Configuração (arquivo `.env`)

O arquivo `.env.example` lista todas as variáveis. As principais:

| Variável | Para que serve |
| --- | --- |
| `SECRET_KEY` | Chave secreta do Django (obrigatória em produção). |
| `DEBUG` | `True` em desenvolvimento, `False` em produção. |
| `YOUCAM_API_KEY` | Chave da API YouCam; sem ela o scanner de pele não funciona. |
| `ADMIN_LOGIN_EMAIL` / `ADMIN_LOGIN_SENHA` | Login do painel `/administradores/` (artigos e especialistas). |
| `DATABASE_URL` ou `DB_*` | PostgreSQL. Sem configuração, usa SQLite local (`db.sqlite3`). |

## Testes

```powershell
python manage.py test
```

## Publicação (Vercel)

O `build_files.sh` roda `collectstatic` e `migrate`. As variáveis acima devem ser cadastradas em *Environment Variables* no painel do Vercel.

## Aviso

A rotina e as recomendações são apenas apoio e não substituem a avaliação de um dermatologista.
