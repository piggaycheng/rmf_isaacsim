#!/usr/bin/env bash
# Open-RMF Web Studio 前後端分離開發模式啟動腳本

echo "=========================================================="
echo "  🚀 啟動 Open-RMF 前後端分離開發環境"
echo "  - 後端 (FastAPI + ROS 2)：http://localhost:8000"
echo "  - 前端 (Vite 熱重載 HMR)：http://localhost:5173"
echo "=========================================================="

# 1. 確保 Docker 後端正在運行
if ! docker ps | grep -q "open_rmf_web"; then
  echo "正在啟動後端 Docker 容器 (open_rmf_web)..."
  docker compose up -d web
else
  echo "✅ 後端 Docker 容器已在運行中"
fi

# 2. 啟動前端開發伺服器
echo "正在啟動前端 Vite 開發伺服器..."
cd "$(dirname "$0")/frontend"
npm run dev
