#!/usr/bin/env bash
echo "start update.sh"
mkdir -p data/siyuan
chmod 777 data/siyuan

echo "Stopping containers..."
docker compose down
echo "Pulling latest images..."
docker compose pull
echo "Removing old images..."
docker image prune -f
echo "Starting containers..."
docker compose up -d

echo "end update.sh"
