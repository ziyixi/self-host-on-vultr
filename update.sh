#!/usr/bin/env bash

docker compose down
docker compose pull
docker image prune -f
docker compose up -d