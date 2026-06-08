# SPEC — 圖書館座位空位監測系統

- **Status:** draft / spec-anchored
- **Owner:** Chung-pei
- **Last updated:** 2026-05-29
- **Schema version:** 1

> 這份檔案是整個系統的「真相來源」。協作者(人或 AI)要先讀懂這份,才有資格動程式。
> 程式碼是這份規格的衍生物,不是反過來。

---

## 1. 命題(可證偽)

一台固定的 ESP32-CAM 週期性把畫面送到 Flask 後端,YOLOv8 偵測人,
靜態 ROI 把偵測結果對應到每個座位,系統即時回報座位空位並記錄歷史。

**可以被推翻的部分:** 單一固定角度相機 + YOLO 人形偵測 + 靜態 ROI,
能在「包包佔位、走道上的人壓到 ROI、兩人共桌」這些干擾下,
做到足夠準(目標:每座位狀態正確率 ≥ 90%)以致於有人願意看這個儀表板。
**如果這條被推翻,產品的價值就不在「偵測」而要重想。**

---

## 2. Elephants(決定整個架構形狀的少數約束)

這三件事先講清楚,後面所有模組都必須與它們一致。

**E1 — 座位 / 占用狀態 schema 是脊椎,不屬於任何單一模組。**
roi 寫它、occupancy 改它、persistence 記它、dashboard 讀它。
它是 source of truth。一旦讓 inference 或 dashboard「就地」改它的定義,
四個模組會各自漂移。→ 獨立成 `shared/seat-schema.contract.md`,任何模組都不准重新定義。

**E2 — 韌體→後端這條鏈不可靠(WiFi、供電、光線)。**
畫面到達是不規律的,有些是壞幀(模糊、過曝)。
所以後端必須把畫面當 best-effort,而 **占用判斷必須做時間平滑(遲滯 hysteresis),
不能逐幀翻轉**。掉一幀或一張壞幀,絕不能讓座位從「有人」翻成「空」。

**E3 —「ROI 內偵測到人」≠「座位被占用」。**
椅子上的包、走道上壓到 ROI 邊緣的路人、共桌——這些才是準確率殺手。
產品的價值住在 occupancy 的判斷邏輯,不是住在 YOLO。

---

## 3. 架構(並行服務,不是直線管線)

```
                          ┌─────────────────────────────┐
                          │  shared/ seat-schema (脊椎)  │
                          │  Seat / Detection / SeatState │
                          └───────▲──────────▲────────────┘
                                  │ reads     │ reads/writes
   ┌──────────┐   frames   ┌──────────┐  ┌──────────┐
   │ firmware │ ─────────▶ │  ingest  │─▶│inference │
   │ ESP32CAM │  (HTTP,    └──────────┘  └────┬─────┘
   └──────────┘   不可靠 E2)                  │ detections
                                              ▼
   ┌──────────┐  ROI 定義          ┌──────────────────┐
   │   roi    │ ─────────────────▶ │    occupancy     │  ← 產品價值在這 (E3)
   │ 標注/儲存│                    │ 遲滯 / 狀態機 E2 │
   └──────────┘                    └────┬────────┬─────┘
                                        │SeatState│SeatState
                                        ▼         ▼
                                 ┌───────────┐ ┌───────────┐
                                 │persistence│ │ dashboard │
                                 │  SQLite   │ │ Chart.js  │
                                 └───────────┘ └───────────┘
```

箭頭是「資料 / 訊息流」,不是函式呼叫。脊椎(seat-schema)是被共享的狀態層,
與運算分開。

---

## 4. 模組地圖

| 模組 | 職責 | 擁有 | 讀取 | 契約 |
|---|---|---|---|---|
| firmware | 擷取畫面、WiFi 重連、送 HTTP | 擷取頻率、重連邏輯 | — | `contracts/ingest.contract.md` (對端) |
| ingest | 接收畫面、丟壞幀、排隊 | 接收端點格式 | — | `contracts/ingest.contract.md` |
| inference | YOLOv8 → detections | 模型版本、信心門檻 | seat-schema(Detection) | (依 seat-schema) |
| roi | 標注 / 儲存座位多邊形 | ROI 編輯流程 | seat-schema(Seat) | (依 seat-schema) |
| **occupancy** | detections + ROI → 占用狀態 | 遲滯演算法、狀態機 | seat-schema、roi | `contracts/occupancy.contract.md` |
| persistence | SQLite 記錄占用事件 | DB schema、migration | seat-schema(SeatState) | (依 seat-schema) |
| dashboard | 即時狀態 + Chart.js 分析 | 前端、API 形狀 | seat-schema(SeatState) | (依 seat-schema) |

---

## 5. 橫切層(這就是純模組化會破的地方)

座位 / 占用 schema 被四個模組共享,**不屬於任何一個**。
它的契約獨立放在 `shared/seat-schema.contract.md`,變更規則寫在那裡:
改它要 bump `schema_version` + 寫 migration + 人類簽核,AI 只能提案不能合併。

---

## 6. 關鍵開放問題(研究閘門 — 解決前不准寫對應模組的程式)

| # | 問題 | 為什麼要命 | 怎麼解決 | 閘門擋住 |
|---|---|---|---|---|
| Q1 | 一台相機的 FOV 能不能在不嚴重遮擋下看到目標座位? | 看不到就什麼都不用做 | 實體架設,跨一天擷取 ~50 幀目視 | roi 工具 |
| Q2 | YOLOv8 在館內光線 / 這個角度的人形偵測準度? | occupancy 信任的是它的輸出 | 對擷取幀手標,量 precision/recall | 信任 occupancy |
| Q3 | 占用的定義:包佔位 / 站立路人怎麼算? | E3,產品準確率的核心 | 用 Q1/Q2 的資料實證地定規則 | occupancy 演算法定版 |

---

## 7. 分階段推進

| 階段 | 目標 | 退出條件(可證偽) |
|---|---|---|
| P0 研究 | 解 Q1–Q3 | 拿到實測數字,做 go/no-go |
| P1 離線建置 | 用 `replay` 模式(餵錄好的幀)把後端做完 | 已知幀序列上行為符合預期、契約測試全綠 |
| P2 單區實機 | 一台相機、一個區域上線 | 實機準確率追平離線結果,連續 N 天穩定 |
| P3 擴張 | 擴到整層樓 | 多相機狀態不漂移、reconciliation 乾淨 |
