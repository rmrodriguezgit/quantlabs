#!/usr/bin/env bash
set -euo pipefail
cp -n .env.example .env || true
mkdir -p storage/artifacts storage/sessions notebooks models
docker compose build
docker compose up -d
