# CONSTITUTION — 本專案的不可違反規則

> 這份檔案就是給 AI agent(Claude Code 等)的憲法。可以直接當 `CLAUDE.md` 的核心內容。
> AI 在這個 repo 動任何程式之前,必須遵守以下規則。違反任一條 = 該變更無效,需人類介入。

## 技術棧(已釘版,不准擅自更換或新增)
- 韌體:ESP32-CAM(AI Thinker board)+ Arduino framework
- 後端:Python 3.11 + Flask
- 推論:Ultralytics YOLOv8(模型權重版本記在 inference 模組的 config)
- 儲存:SQLite
- 前端分析:Chart.js
- 測試:pytest

## 三條紅線(Cardinal Rules)
1. **脊椎不可就地改。** `shared/seat-schema.contract.md` 定義的型別(Seat / Detection /
   SeatState)是全系統共享的真相來源。任何模組都不准重新定義它。要改 → 走橫切層變更流程。
2. **模組隔離。** 在模組 X 內的變更,不准改到模組 Y 的「對外契約」。
   若非改不可,那就是一次契約變更 → 停下來,標記出來,等人類審。
3. **契約測試必須綠。** 任何模組變更後,該模組的契約測試必須全部通過才算完成。
   不准為了讓測試過而改測試的斷言。

## 慣例
- 設定值放在 `config`(env 或設定檔),不准寫死在程式裡;可不重啟重載。
- 錯誤處理:對外邊界(HTTP、相機、DB)一律 try/except + 結構化 log,不准吞掉例外。
- log 與 monitoring 分開:log 是事後重建用,即時狀態走 dashboard。
- 命名沿用 schema 契約裡的名字,不准同義詞自創(seat 不要又叫 desk 又叫 spot)。

## 模式(Modes)
- inference / occupancy 管線必須支援 `replay` 模式:餵預錄的幀序列,
  不需要實體相機就能跑與測試。live 與 replay 走同一條程式路徑。

## AI 未經人類明確同意,不得做以下事(只能「提案」)
- 改 `shared/seat-schema.contract.md` 或任何 schema 版本
- 改任一模組的對外公開契約(函式簽名、端點格式、回傳結構)
- 寫 / 改 SQLite migration
- 新增任何外部相依套件
- 動韌體的擷取頻率、WiFi 重連邏輯(這是 E2,牽一髮動全身)
