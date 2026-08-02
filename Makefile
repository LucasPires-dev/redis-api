IMAGE ?= redis-api
TAG ?= latest
STACK ?= redis-api

.PHONY: build deploy update down logs logs-worker logs-redis ps status restart shell migrate

build:
	docker build -t $(IMAGE):$(TAG) .

deploy: build
	docker stack deploy -c swarm-stack.yml $(STACK)

update:
	docker service update --image $(IMAGE):$(TAG) --force $(STACK)_app
	docker service update --image $(IMAGE):$(TAG) --force $(STACK)_worker
	docker service update --image $(IMAGE):$(TAG) --force $(STACK)_beat

down:
	docker stack rm $(STACK)

logs:
	docker service logs -f $(STACK)_app

logs-worker:
	docker service logs -f $(STACK)_worker

logs-redis:
	docker service logs -f $(STACK)_redis

ps:
	docker stack ps $(STACK)

status:
	docker service ls | grep $(STACK)

restart:
	docker service update --force $(STACK)_app

shell:
	docker exec -it $$(docker ps -q -f name=$(STACK)_app) python manage.py shell

migrate:
	docker exec -it $$(docker ps -q -f name=$(STACK)_app) python manage.py migrate
