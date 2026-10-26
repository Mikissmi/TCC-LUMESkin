# TCC - LUME Skin

Repositório do Trabalho de Conclusão de Curso de Técnico em Desenvolvimento de Sistemas do IFSul – Campus Sapucaia do Sul, desenvolvido pelas alunas Mariana e Caroline para a obtenção do diploma.

## Executar localmente

Com Python 3.12 ou superior instalado, abra o PowerShell na pasta do projeto e execute:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Sem configuração de banco no ambiente, o projeto usa SQLite local (`db.sqlite3`). Para usar PostgreSQL, configure `DATABASE_URL` (ou `POSTGRES_URL`) ou as variáveis `DB_NAME`, `DB_USER`, `DB_PASSWORD` e `DB_HOST`.