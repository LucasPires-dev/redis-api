#!/bin/sh
set -e

# app, worker e beat compartilham a mesma imagem/entrypoint. Só quem sobe o
# Daphne roda migrations/collectstatic — se os três rodassem isso ao mesmo
# tempo no primeiro deploy, teria corrida entre processos de migrate.
if [ "$1" = "daphne" ]; then
    echo "Aplicando migrations..."
    python manage.py migrate --noinput

    echo "Coletando arquivos estáticos..."
    python manage.py collectstatic --noinput
fi

exec "$@"
