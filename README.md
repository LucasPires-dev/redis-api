# Redis API

API da plataforma **Redis**, um sistema de gestão para igrejas com múltiplos
campus. O backend centraliza pessoas, ministérios, células, lideranças,
reconciliação de novos convertidos e produção de eventos.

> **Nota:** Redis é o nome do produto. O banco de dados principal da aplicação é
> PostgreSQL; Redis é usado como infraestrutura para comunicação em tempo real e
> processamento assíncrono.

## Visão geral

- API REST construída com Django e Django REST Framework.
- Autenticação JWT e permissões organizadas por papel e escopo de atuação.
- Atualizações em tempo real com Django Channels e WebSockets.
- Tarefas agendadas e assíncronas com Celery.
- Documentação interativa da API com OpenAPI e Swagger.
- Integração opcional com Google Sheets para sincronização de novos convertidos.

## Tecnologias

| Área | Tecnologias |
| --- | --- |
| Linguagem e framework | Python 3.11+, Django 5, Django REST Framework |
| Banco de dados | PostgreSQL |
| Autenticação e documentação | Simple JWT, DRF Spectacular |
| Tempo real e tarefas | Django Channels, Daphne, Celery, Redis |
| Integrações | Google Sheets API |
| Testes | pytest, pytest-django |
| Containers | Docker e Docker Compose |

## Pré-requisitos

Para executar a API diretamente no Windows ou Linux:

- Python 3.11 ou superior.
- Poetry 2.x.
- PostgreSQL em execução.
- Git.

Redis é necessário para usar WebSockets e tarefas assíncronas. A API REST pode
ser desenvolvida sem iniciar o worker do Celery. Para executar toda a stack com
Docker Compose, também é necessário Docker com suporte a containers Linux.

## Instalação e configuração local

### 1. Instale as dependências

Na raiz deste repositório:

```powershell
poetry install --with dev
```

### 2. Configure as variáveis de ambiente

Crie o arquivo local a partir do exemplo. No PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edite `.env` e configure, no mínimo:

```dotenv
DJANGO_SETTINGS_MODULE=config.settings.development
SECRET_KEY=uma-chave-aleatoria-para-desenvolvimento
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://postgres:SUA_SENHA@127.0.0.1:5432/redis_db
```

Gere uma chave local com:

```powershell
poetry run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Crie um banco chamado `redis_db` no PostgreSQL, por exemplo pelo pgAdmin, e
substitua `SUA_SENHA` pela senha do usuário do banco. Se a senha contiver
caracteres especiais, use a codificação URL correspondente no `DATABASE_URL`.
Não use os valores de exemplo nem compartilhe ou versione o arquivo `.env`.

Para habilitar as chamadas do frontend local, confirme que
`CORS_ALLOWED_ORIGINS` inclui a origem em que o Vite iniciou (normalmente
`http://localhost:8080`; o exemplo inclui portas próximas).

### 3. Prepare o banco e inicie a API

```powershell
poetry run python manage.py migrate
poetry run python manage.py runserver 127.0.0.1:8000
```

A API ficará disponível em `http://127.0.0.1:8000`. A documentação interativa
fica em `http://127.0.0.1:8000/api/docs/`; o esquema OpenAPI está em
`http://127.0.0.1:8000/api/schema/`.

Opcionalmente, crie um usuário administrador:

```powershell
poetry run python manage.py createsuperuser
```

O painel administrativo fica em `http://127.0.0.1:8000/admin/`.

## Redis, WebSockets e tarefas em segundo plano

As configurações padrão esperam Redis em `127.0.0.1:6379`:

- `REDIS_URL` usa o banco lógico `0` para Django Channels.
- `CELERY_BROKER_URL` usa o banco lógico `1` para Celery.

Defina essas variáveis no `.env` se o serviço estiver em outro endereço. Com
Redis disponível e as credenciais de Google Sheets configuradas, os processos
de worker e agendamento podem ser iniciados em terminais separados:

```powershell
poetry run celery -A config worker -l info
poetry run celery -A config beat -l info
```

O sincronizador de reconciliação usa `GOOGLE_SHEETS_CREDENTIALS_JSON` e
`GOOGLE_SHEETS_SPREADSHEET_ID`. Sem essas credenciais, deixe o Beat desligado
para evitar executar a sincronização sem configuração.

## Executar com Docker Compose

Configure `.env` conforme a seção anterior e, na raiz deste repositório, execute:

```powershell
docker compose run --rm app python manage.py migrate
docker compose up --build
```

O Compose inicia API, Redis, worker e agendador. A API é publicada na porta
`8090` e Redis na porta `6379`. No ambiente Compose, os containers usam o nome
do serviço `redis` para se conectar entre si.

## Testes e qualidade

Os testes usam SQLite em memória e uma camada de canais em memória, sem exigir
uma instância de Redis:

```powershell
poetry run pytest
```

Verificações de estilo disponíveis:

```powershell
poetry run black --check .
poetry run isort --check-only .
poetry run flake8 .
```

## Organização do código

```text
apps/
├── churches/       # Igrejas, campus, ministérios, células e lideranças
├── people/         # Pessoas, autenticação e identidade de acesso
├── production/     # Eventos, demandas, escala e produção em tempo real
└── reconciliation/ # Acompanhamento e sincronização de novos convertidos
config/             # Configurações Django, ASGI e rotas do projeto
tests/              # Testes de API, permissões, tarefas e WebSockets
```

## Segurança e configuração

- `.env` contém credenciais locais e não deve ser commitado.
- `DEBUG=True`, chaves de exemplo e configurações permissivas são somente para
  desenvolvimento.
- Para produção, use configurações de produção, segredos fortes e origens CORS
  explicitamente permitidas.
