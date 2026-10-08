#!/usr/bin/env bash
# Open-RMF Web Studio 停止容器化開發環境腳本

echo "正在停止 Open-RMF 開發容器服務 (backend, frontend, emqx, mediamtx)..."
docker compose stop backend frontend emqx mediamtx
echo "✅ 所有開發服務已成功停止"
