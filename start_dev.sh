#!/usr/bin/env bash
# Open-RMF Web Studio 容器化開發模式啟動腳本

echo "=========================================================="
echo "  🚀 啟動 Open-RMF 容器化開發環境"
echo "  - 後端 (FastAPI + ROS 2)：http://localhost:8088"
echo "  - 前端 (Vite 容器熱重載)：http://localhost:5173"
echo "  - MQTT (EMQX Broker)：http://localhost:18083"
echo "  - 影像串流 (MediaMTX)：http://localhost:8889"
echo "=========================================================="

# 1. 確保所有 Docker 服務 (後端、EMQX、MediaMTX、前端) 正在運行
echo "正在啟動/檢查 Docker 開發服務 (backend, emqx, mediamtx, frontend)..."
docker compose up -d backend emqx mediamtx frontend
echo "✅ Docker 服務啟動完成"

# 2. 即時監看前端 Vite 輸出日誌
echo "正在追蹤前端 Vite 輸出日誌 (按 Ctrl+C 可結束日誌追蹤，容器將持續在背景運行)..."
docker compose logs -f frontend
