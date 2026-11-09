# Redis · API

API de gestão para uma **igreja com vários campus**. Organiza a estrutura da igreja (campus, ministérios, células e equipes), controla quem vê o quê por nível de liderança, coordena a **produção dos cultos em tempo real** e acompanha os novos convertidos pelo Ministério de Reconciliação.

> Front-end: [redis](https://github.com/LucasPires-dev/redis)

## ✨ Funcionalidades

- **Estrutura da igreja**: igreja → campus → ministérios (jovens, crianças, mulheres…) → equipes e células, cada um com sua liderança
- **Controle de acesso por papel**: gestor geral, líder de campus, líder geral de ministério (todos os campus) e líder de ministério local, cada um enxergando só o que lhe cabe
- **Equipes com atividades e pontuação**: tarefas, conclusões e pontos por equipe
- **Produção de eventos**: roteiro do culto (*rundown*) com status por item, demandas, escala de voluntários e checklist, **atualizados em tempo real** para toda a equipe
- **Reconciliação**: respostas de um Google Forms são importadas da planilha conectada, sem duplicar, e viram acompanhamentos atribuídos a um responsável

## 🛠️ Stack

| Camada | Tecnologias |
|---|---|
| Backend | Python · Django 5 · Django REST Framework · SimpleJWT · django-filter · drf-spectacular |
| Tempo real | Django Channels · Daphne · Redis |
| Tarefas assíncronas | Celery · Celery Beat |
| Integrações | Google Sheets API |
| Banco | PostgreSQL |
| Testes | pytest · **112 testes** |
| Infra | Docker · Docker Compose · Docker Swarm · Poetry |

## 🧠 Destaques técnicos

- **Permissões testadas por papel**: cada app tem suas classes de permissão, e os testes cobrem o que cada nível de liderança pode ver e alterar.
- **WebSocket com JWT**: o navegador não envia headers no handshake do WebSocket, então o token vai na query string e é validado com o mesmo `JWTAuthentication` do REST, sem duplicar a lógica.
- **Tempo real enxuto**: o servidor só avisa *o que* mudou (`rundown_item_status`, `demand`…) e o front-end busca de novo pela API REST. O WebSocket nunca vira uma segunda fonte de dados.
- **Importação idempotente**: as linhas da planilha são lidas pelo cabeçalho, e não pela posição da coluna, e deduplicadas por e-mail, telefone e data de envio. A sincronização roda periodicamente pelo Celery Beat.
- **Usuário customizado** (`Person`) como modelo de autenticação.
- **Settings por ambiente** (`development`, `production`, `test`) com `python-decouple` e `DATABASE_URL`.

## 🚀 Como rodar

```bash
cp .env.example .env
poetry install
poetry run python manage.py migrate
poetry run python manage.py seed_initial_church   # dados iniciais
poetry run python manage.py runserver
```

Com Docker (API, worker, beat e Redis):

```bash
docker compose up -d --build
```

Testes:

```bash
poetry run pytest
```

Documentação interativa da API em `http://localhost:8000/api/docs/`.

## 🔌 Recursos

| Módulo | O que expõe |
|---|---|
| `people` | Pessoas, login (JWT), autenticação do WebSocket |
| `churches` | Igreja, campus, ministérios, células, equipes, atividades e lideranças |
| `production` | Eventos, roteiro, demandas, escala, checklist e WebSocket |
| `reconciliation` | Leads da planilha e acompanhamentos |

## 📁 Estrutura

```
apps/
  people/          # usuário (Person), auth, WebSocket
  churches/        # estrutura da igreja e permissões
  production/      # produção de eventos + tempo real (Channels)
  reconciliation/  # Google Sheets + Celery
config/settings/   # base, development, production, test
tests/             # testes por app
```

---

Desenvolvido por [Lucas C. Pires](https://github.com/LucasPires-dev) · Full-stack Python
