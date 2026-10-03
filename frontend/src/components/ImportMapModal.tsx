import React, { useState } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { SlamMap } from '@/types/rmf';
import { FileUp, Image as ImageIcon, FileText, CheckCircle2, AlertCircle } from 'lucide-react';

interface ImportMapModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMapLoaded: (slamMap: SlamMap) => void;
}

export const ImportMapModal: React.FC<ImportMapModalProps> = ({
  open,
  onOpenChange,
  onMapLoaded,
}) => {
  const [pgmFile, setPgmFile] = useState<File | null>(null);
  const [yamlFile, setYamlFile] = useState<File | null>(null);
  const [resolution, setResolution] = useState<number>(0.05);
  const [originX, setOriginX] = useState<number>(-10.0);
  const [originY, setOriginY] = useState<number>(-10.0);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // When YAML file is selected, parse resolution & origin client-side for immediate feedback
  const handleYamlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setYamlFile(file);

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      if (!text) return;

      // Extract resolution
      const resMatch = text.match(/resolution:\s*([\d.]+)/i);
      if (resMatch && resMatch[1]) {
        setResolution(parseFloat(resMatch[1]));
      }

      // Extract origin
      const originMatch = text.match(/origin:\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)/i);
      if (originMatch && originMatch[1] && originMatch[2]) {
        setOriginX(parseFloat(originMatch[1]));
        setOriginY(parseFloat(originMatch[2]));
      }
    };
    reader.readAsText(file);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pgmFile) {
      setErrorMsg('請至少選取一個 .pgm 或 .png 地圖檔案！');
      return;
    }

    setIsUploading(true);
    setErrorMsg(null);

    const formData = new FormData();
    formData.append('pgm_file', pgmFile);
    if (yamlFile) {
      formData.append('yaml_file', yamlFile);
    }

    try {
      const response = await fetch('http://localhost:8000/api/map/upload-slam', {
        method: 'POST',
        body: formData,
      });

      const res = await response.json();
      if (res.status === 'success' && res.map) {
        onMapLoaded(res.map);
        onOpenChange(false);
      } else {
        setErrorMsg(res.message || '地圖轉換失敗，請確認檔案格式是否正確。');
      }
    } catch (err: any) {
      setErrorMsg(`連線後端伺服器失敗: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  // Generate a mock demo occupancy grid map (test without real file)
  const handleLoadDemoSlamMap = () => {
    const canvas = document.createElement('canvas');
    canvas.width = 400;
    canvas.height = 300;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Free space (light gray / white)
    ctx.fillStyle = '#e2e8f0';
    ctx.fillRect(0, 0, 400, 300);

    // Walls / Obstacles (black lines)
    ctx.strokeStyle = '#0f172a';
    ctx.lineWidth = 4;
    ctx.strokeRect(20, 20, 360, 260); // perimeter walls

    // Inner rooms and obstacles
    ctx.beginPath();
    ctx.moveTo(150, 20);
    ctx.lineTo(150, 180);
    ctx.moveTo(250, 120);
    ctx.lineTo(250, 280);
    ctx.stroke();

    // Workcells / pillars
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(60, 60, 30, 30);
    ctx.fillRect(300, 60, 30, 30);
    ctx.fillRect(60, 200, 30, 30);

    const demoDataUrl = canvas.toDataURL('image/png');

    const demoSlamMap: SlamMap = {
      name: 'demo_warehouse_slam.pgm',
      image_url: demoDataUrl,
      resolution: 0.05,
      origin: [0.0, -2.0, 0.0],
      width: 400,
      height: 300,
      real_width_m: 400 * 0.05, // 20.0 meters
      real_height_m: 300 * 0.05, // 15.0 meters
      opacity: 0.85,
    };

    onMapLoaded(demoSlamMap);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle className="flex items-center space-x-2 text-xl font-bold">
          <FileUp className="w-5 h-5 text-primary" />
          <span>載入 ROS SLAM 地圖 (.pgm + map.yaml)</span>
        </DialogTitle>
        <DialogDescription>
          上傳 ROS 2 產生的雷達建圖檔（PGM 與 YAML），系統將自動轉換並根據真實世界座標尺度對齊畫布。
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4">
        {errorMsg && (
          <div className="flex items-center space-x-2 text-xs bg-rose-500/10 border border-rose-500/30 text-rose-400 p-2.5 rounded-lg">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* 1. PGM File Picker */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1 block">
            1. 地圖影像檔 (.pgm 或 .png) <span className="text-rose-400">*</span>
          </label>
          <div className="flex items-center space-x-2">
            <div className="relative flex-1">
              <input
                type="file"
                accept=".pgm,.png,.jpg,.jpeg"
                onChange={(e) => setPgmFile(e.target.files?.[0] || null)}
                className="w-full text-xs text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-primary file:text-primary-foreground hover:file:bg-primary/90 bg-slate-900 border border-slate-700 rounded-md p-1.5 cursor-pointer"
              />
            </div>
          </div>
          {pgmFile && (
            <p className="text-[11px] text-emerald-400 mt-1 flex items-center">
              <CheckCircle2 className="w-3 h-3 mr-1" /> 已選擇: {pgmFile.name} ({(pgmFile.size / 1024).toFixed(1)} KB)
            </p>
          )}
        </div>

        {/* 2. YAML File Picker */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1 block">
            2. 地圖設定檔 (map.yaml - 可選)
          </label>
          <input
            type="file"
            accept=".yaml,.yml"
            onChange={handleYamlChange}
            className="w-full text-xs text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-secondary file:text-secondary-foreground hover:file:bg-secondary/80 bg-slate-900 border border-slate-700 rounded-md p-1.5 cursor-pointer"
          />
          {yamlFile && (
            <p className="text-[11px] text-emerald-400 mt-1 flex items-center">
              <CheckCircle2 className="w-3 h-3 mr-1" /> 已自動解析 YAML: {yamlFile.name}
            </p>
          )}
        </div>

        {/* 3. Parameters (Resolution & Origin) */}
        <div className="bg-slate-900/60 border border-slate-800 p-3 rounded-lg space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-300">地圖座標校準參數</span>
            <span className="text-[10px] text-slate-500">(可依 map.yaml 自動帶入)</span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-slate-400 block mb-0.5">解析度 (m/pixel)</label>
              <Input
                type="number"
                step="0.001"
                value={resolution}
                onChange={(e) => setResolution(parseFloat(e.target.value) || 0.05)}
                className="h-7 text-xs bg-slate-950 border-slate-700"
              />
            </div>
            <div>
              <label className="text-[10px] text-slate-400 block mb-0.5">原點 Origin X (m)</label>
              <Input
                type="number"
                step="0.1"
                value={originX}
                onChange={(e) => setOriginX(parseFloat(e.target.value) || 0)}
                className="h-7 text-xs bg-slate-950 border-slate-700"
              />
            </div>
            <div>
              <label className="text-[10px] text-slate-400 block mb-0.5">原點 Origin Y (m)</label>
              <Input
                type="number"
                step="0.1"
                value={originY}
                onChange={(e) => setOriginY(parseFloat(e.target.value) || 0)}
                className="h-7 text-xs bg-slate-950 border-slate-700"
              />
            </div>
          </div>
        </div>

        <DialogFooter className="flex items-center justify-between sm:justify-between w-full">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleLoadDemoSlamMap}
            className="text-xs text-cyan-400 hover:text-cyan-300"
          >
            <ImageIcon className="w-3.5 h-3.5 mr-1" />
            快速載入範例 SLAM 地圖
          </Button>

          <div className="flex items-center space-x-2">
            <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" size="sm" disabled={isUploading || !pgmFile}>
              {isUploading ? '轉換並載入中...' : '確認載入地圖'}
            </Button>
          </div>
        </DialogFooter>
      </form>
    </Dialog>
  );
};
