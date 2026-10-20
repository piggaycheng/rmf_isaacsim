import React, { useState, useEffect, useMemo } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { MapLevel, parseFloorOrder } from '@/types/rmf';
import { Building2, Layers, AlertCircle, ArrowUp, ArrowDown, HelpCircle, Check, Sparkles } from 'lucide-react';

interface LevelModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode?: 'add' | 'edit';
  initialLevel?: MapLevel | null;
  existingLevels: MapLevel[];
  onSave: (data: { name: string; elevation: number }) => void;
}

export const LevelModal: React.FC<LevelModalProps> = ({
  open,
  onOpenChange,
  mode = 'add',
  initialLevel = null,
  existingLevels = [],
  onSave,
}) => {
  const [name, setName] = useState<string>('');
  const [elevationStr, setElevationStr] = useState<string>('0');
  const [isCustomElevation, setIsCustomElevation] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Initialize or reset form state whenever modal opens or mode/initialLevel changes
  useEffect(() => {
    if (!open) return;
    setErrorMsg(null);

    if (mode === 'edit' && initialLevel) {
      setName(initialLevel.name);
      const elev =
        typeof initialLevel.elevation === 'number' && Number.isFinite(initialLevel.elevation)
          ? initialLevel.elevation
          : parseFloorOrder(initialLevel.name) ?? 0;
      setElevationStr(String(elev));
      setIsCustomElevation(true);
    } else {
      // Suggest next floor name (e.g., 2F if 1F exists, 3F if 2F exists)
      const existingFloorNums = existingLevels
        .map((l) => {
          const m = l.name.match(/^(\d+)F$/i);
          return m ? parseInt(m[1], 10) : 0;
        })
        .filter((n) => n > 0);

      const nextFloorNum = existingFloorNums.length > 0 ? Math.max(...existingFloorNums) + 1 : 2;
      const suggestedName = `${nextFloorNum}F`;
      setName(suggestedName);

      const parsed = parseFloorOrder(suggestedName) ?? existingLevels.length;
      setElevationStr(String(parsed));
      setIsCustomElevation(false);
    }
  }, [open, mode, initialLevel, existingLevels]);

  // Handle floor name input changes with automatic elevation suggestion
  const handleNameChange = (val: string) => {
    setName(val);
    setErrorMsg(null);
    if (!isCustomElevation) {
      const parsed = parseFloorOrder(val);
      if (parsed !== null) {
        setElevationStr(String(parsed));
      }
    }
  };

  // Quick preset pills click
  const handleSelectPreset = (presetName: string) => {
    setName(presetName);
    setErrorMsg(null);
    const parsed = parseFloorOrder(presetName);
    if (parsed !== null) {
      setElevationStr(String(parsed));
      setIsCustomElevation(false);
    }
  };

  const handleElevationChange = (val: string) => {
    setElevationStr(val);
    setIsCustomElevation(true);
    setErrorMsg(null);
  };

  const handleApplyRecommendedElevation = () => {
    const parsed = parseFloorOrder(name);
    if (parsed !== null) {
      setElevationStr(String(parsed));
      setIsCustomElevation(false);
    } else {
      setElevationStr(String(existingLevels.length));
    }
  };

  const parsedElevation = useMemo(() => {
    const val = parseFloat(elevationStr);
    return Number.isFinite(val) ? val : 0;
  }, [elevationStr]);

  // Build a preview of all floors sorted from highest elevation down to lowest elevation
  const sortedStackPreview = useMemo(() => {
    const trimmed = name.trim();
    const currentEntry = {
      name: trimmed || (mode === 'add' ? '新樓層 (編輯中)' : initialLevel?.name || '當前樓層'),
      elevation: parsedElevation,
      isTarget: true,
    };

    const others = existingLevels
      .filter((l) => (mode === 'edit' ? l.name !== initialLevel?.name : true))
      .map((l) => ({
        name: l.name,
        elevation:
          typeof l.elevation === 'number' && Number.isFinite(l.elevation)
            ? l.elevation
            : parseFloorOrder(l.name) ?? 0,
        isTarget: false,
      }));

    const combined = [...others, currentEntry];
    // Sort descending by elevation: top floor first, basement bottom
    return combined.sort((a, b) => b.elevation - a.elevation);
  }, [existingLevels, name, parsedElevation, mode, initialLevel]);

  // Form Submission
  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();

    const trimmed = name.trim();
    if (!trimmed) {
      setErrorMsg('請輸入樓層識別名稱');
      return;
    }

    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(trimmed)) {
      setErrorMsg('樓層名稱格式錯誤，僅能使用英數字、_、-（例如 2F、B1）');
      return;
    }

    // Name uniqueness check
    if (mode === 'add') {
      const isDuplicate = existingLevels.some(
        (l) => l.name.toLowerCase() === trimmed.toLowerCase()
      );
      if (isDuplicate) {
        setErrorMsg(`樓層「${trimmed}」已存在，請使用不同名稱`);
        return;
      }
    } else if (mode === 'edit' && initialLevel && trimmed.toLowerCase() !== initialLevel.name.toLowerCase()) {
      const isDuplicate = existingLevels.some(
        (l) => l.name.toLowerCase() === trimmed.toLowerCase()
      );
      if (isDuplicate) {
        setErrorMsg(`樓層「${trimmed}」已存在，無法重複命名`);
        return;
      }
    }

    const elevVal = parseFloat(elevationStr);
    if (!Number.isFinite(elevVal)) {
      setErrorMsg('請輸入有效的樓層高度數值（可以是正數、0 或負數）');
      return;
    }

    onSave({
      name: trimmed,
      elevation: elevVal,
    });

    onOpenChange(false);
  };

  const recommendedOrder = parseFloorOrder(name);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} className="bg-[#0b111e] border-slate-800 text-slate-100 max-w-lg">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2 text-white text-base">
          <Building2 className="w-5 h-5 text-cyan-400" />
          <span>{mode === 'add' ? '新增建築樓層 (Add Floor)' : `編輯樓層定義 - ${initialLevel?.name}`}</span>
        </DialogTitle>
        <DialogDescription className="text-xs text-slate-400">
          設定樓層識別名稱與相對高度層級（Elevation），這將決定 3D 模式中各樓層平面的上下疊放順序。
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4 text-xs">
        {/* Error Alert */}
        {errorMsg && (
          <div className="flex items-start gap-2 bg-rose-950/60 border border-rose-800/80 text-rose-300 p-2.5 rounded-lg text-xs animate-in fade-in">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* 1. Floor Name Input */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="font-semibold text-slate-200 flex items-center gap-1.5">
              <span>樓層名稱 (Name / ID)</span>
              <span className="text-rose-400">*</span>
            </label>
            <span className="text-[11px] text-slate-400">常用範例: 2F, 3F, B1, 夾層</span>
          </div>
          <Input
            type="text"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder="例如: 2F, 3F, B1, Warehouse"
            className="bg-slate-950 border-slate-700 text-cyan-300 font-mono text-sm focus:border-cyan-500 h-9"
            autoFocus
          />

          {/* Quick presets */}
          {mode === 'add' && (
            <div className="flex items-center gap-1.5 pt-1 flex-wrap">
              <span className="text-[10px] text-slate-500">快速填入:</span>
              {['1F', '2F', '3F', '4F', 'B1', 'B2', 'RF'].map((preset) => {
                const isUsed = existingLevels.some(
                  (l) => l.name.toLowerCase() === preset.toLowerCase()
                );
                return (
                  <button
                    key={preset}
                    type="button"
                    disabled={isUsed}
                    onClick={() => handleSelectPreset(preset)}
                    className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
                      isUsed
                        ? 'bg-slate-900/40 text-slate-600 border border-slate-800/50 cursor-not-allowed line-through'
                        : name === preset
                        ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 font-bold'
                        : 'bg-slate-900 text-slate-400 border border-slate-800 hover:border-slate-600 hover:text-slate-200'
                    }`}
                  >
                    {preset}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* 2. Elevation Input & Helpers */}
        <div className="space-y-1.5 bg-slate-900/60 border border-slate-800 p-3 rounded-lg">
          <div className="flex items-center justify-between">
            <label className="font-semibold text-slate-200 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-cyan-400" />
              <span>樓層高度層級 (Elevation)</span>
            </label>
            {recommendedOrder !== null && (
              <button
                type="button"
                onClick={handleApplyRecommendedElevation}
                className="text-[11px] text-cyan-400 hover:text-cyan-300 underline flex items-center gap-1"
                title="根據樓層名稱自動設定建議值"
              >
                <Sparkles className="w-3 h-3" />
                <span>名稱建議值: {recommendedOrder}</span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Input
              type="number"
              step="any"
              value={elevationStr}
              onChange={(e) => handleElevationChange(e.target.value)}
              className="bg-slate-950 border-slate-700 text-amber-300 font-mono text-sm focus:border-amber-400 h-9 w-32"
            />
            <div className="text-[11px] text-slate-400 leading-tight">
              <div>
                3D 空間估計高度:{' '}
                <span className="font-mono text-cyan-300 font-semibold">
                  {(parsedElevation * 24).toFixed(1)} m
                </span>{' '}
                <span className="text-slate-500">(預設間距 24m)</span>
              </div>
              <div className="text-slate-500 text-[10px]">
                數值愈大在 3D 模式中位於愈上層（地上層 $\ge 0$、地下層 $\le -1$）。
              </div>
            </div>
          </div>
        </div>

        {/* 3. Visual 3D Stack Order Preview */}
        <div className="space-y-1 bg-slate-950/70 border border-slate-800/80 p-2.5 rounded-lg">
          <div className="flex items-center justify-between text-[11px] text-slate-400 px-1 mb-1">
            <span className="flex items-center gap-1 font-semibold text-slate-300">
              <Building2 className="w-3 h-3 text-cyan-400" />
              <span>3D 樓層上下立體排序預覽</span>
            </span>
            <span className="text-[10px] text-slate-500">頂層 (上方) $\rightarrow$ 底層 (下方)</span>
          </div>

          <div className="flex flex-col gap-1 max-h-36 overflow-y-auto pr-1">
            {sortedStackPreview.map((item, idx) => {
              const isHighlight = item.isTarget;
              return (
                <div
                  key={`${item.name}-${idx}`}
                  className={`flex items-center justify-between px-2.5 py-1.5 rounded text-xs transition-all ${
                    isHighlight
                      ? 'bg-cyan-950/90 border border-cyan-500/80 text-cyan-200 shadow-sm shadow-cyan-950 ring-1 ring-cyan-500/30'
                      : 'bg-slate-900/60 border border-slate-800/60 text-slate-400'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-slate-500">#{idx + 1}</span>
                    <span className="font-bold">{item.name}</span>
                    {isHighlight && (
                      <span className="text-[9px] px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300 font-medium">
                        {mode === 'add' ? '新增目標' : '編輯中'}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 font-mono text-[11px]">
                    <span className={isHighlight ? 'text-amber-300' : 'text-slate-400'}>
                      Elevation: {item.elevation}
                    </span>
                    <span className="text-slate-600">|</span>
                    <span className={item.elevation >= 0 ? 'text-emerald-400' : 'text-indigo-400'}>
                      {item.elevation >= 0 ? `+${(item.elevation * 24).toFixed(0)}m` : `${(item.elevation * 24).toFixed(0)}m`}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <DialogFooter className="mt-4 gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="border-slate-700 text-slate-300 hover:bg-slate-800 text-xs h-8"
          >
            取消
          </Button>
          <Button
            type="submit"
            size="sm"
            className="bg-cyan-600 hover:bg-cyan-500 text-white font-semibold text-xs h-8 px-4"
          >
            <Check className="w-3.5 h-3.5 mr-1" />
            <span>{mode === 'add' ? '確認新增樓層' : '儲存樓層設定'}</span>
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
};
