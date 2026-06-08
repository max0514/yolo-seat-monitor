# CONTRACT(模組)— occupancy

## 白話摘要(給不讀程式的協作者)
這塊拿「YOLO 偵測到的人」加上「每個座位的 ROI」,判斷每個座位現在是不是有人。
關鍵不是看單一畫面,而是看一段時間:要連續看到才算「有人」,要連續看不到一陣子才算「空」。
這樣掉一幀、一張糊掉的畫面,都不會讓座位狀態亂跳。**產品準不準,主要看這塊。**

## 公開介面(這是契約,改它要人類審)
```python
class OccupancyEngine:
    def update(
        self,
        detections: list[Detection],   # 來自 inference,本幀
        roi_set: list[Seat],           # 來自 roi
        now: float,                    # 本幀時間戳(秒)
    ) -> list[SeatState]:
        """純函式 + 內部計數器。回傳本輪每個座位的狀態。"""
```
型別全部來自 `shared/seat-schema.contract.md`,本模組不得自定義。

## 相依(只准讀這些)
- `shared/seat-schema`:Detection、Seat、SeatState、Status
- roi 模組提供的 `roi_set`

**明確禁止:** 不准直接碰 DB、不准直接呼叫 YOLO、不准做任何網路 I/O。

## 核心演算法
1. **對應:** 對每個座位,判定哪些 detection 落在它的 ROI 內
   (預設:detection bbox 中心點落在多邊形內;之後可換 IoU)。
2. **遲滯 / 去抖(E2 的落實):**
   - 座位翻成 `OCCUPIED`:需連續 `k_occ` 幀為正。
   - 座位翻成 `EMPTY`:需連續 `k_emp` 幀為負,**且**距上次離開 OCCUPIED ≥ `T_empty` 秒。
   - 起始參數:`k_occ = 2`、`k_emp = 4`、`T_empty = 30s`。
   - **理由:** 誤判成「空」比慢一點報「空」糟得多(學生放包包暫時離開)。
3. **壞幀 / 無資料:** 該輪沒有可信輸入時,維持原狀態並標 `confidence` 低;
   連續 UNKNOWN 超過 `T_unknown` 才把狀態設為 `UNKNOWN`,不可直接設 EMPTY。

## 狀態機(每座位一份)
```
EMPTY ──(連續 k_occ 正)──▶ OCCUPIED
OCCUPIED ──(連續 k_emp 負 且 ≥ T_empty)──▶ EMPTY
任一狀態 ──(連續壞幀 ≥ T_unknown)──▶ UNKNOWN ──(下次有可信判斷)──▶ 回對應狀態
```

## 保證(Invariants)
- 給定相同的輸入序列,輸出完全可重現(deterministic)——這是 `replay` 模式能用的前提。
- 純運算,無副作用、無 I/O。
- 單幀的單一正 / 負偵測,不足以翻轉任何已穩定的狀態。

## AI 未經人類審不得更改
- 上面的 `update` 簽名
- 遲滯語意(k_occ / k_emp / T_empty 的「先正後負、非對稱」這個設計)
- 任何來自 seat-schema 的型別

## 契約測試
本契約的可執行形式在 `tests/test_occupancy_contract.py`。
改本模組後測試必須全綠;測試斷言不可為了通過而放寬。
