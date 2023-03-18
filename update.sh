#!/usr/bin/env bash

docker compose down
git pull
docker compose pull
docker image prune -f
docker compose up -d