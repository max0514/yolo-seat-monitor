# CONTRACT(橫切層 / 脊椎)— seat-schema

## 白話摘要(給不讀程式的協作者)
這是全系統共享的資料定義,是真相來源。座位長什麼樣、一筆偵測長什麼樣、
一個座位的占用狀態長什麼樣——都只在這裡定義一次。roi 寫座位、occupancy 算狀態、
persistence 記錄、dashboard 顯示,全部讀同一套定義。**沒有任何模組可以自己改它。**

## 為什麼它要獨立出來
這是純模組化會破的地方(spec E1)。座位 schema 被四個模組共享,
不屬於其中任何一個。把它隔離在這裡、用嚴格流程治理,才能讓「模組化」這件事誠實。

## 型別定義(schema_version = 1)
```python
from dataclasses import dataclass
from enum import Enum

class Status(str, Enum):
    EMPTY = "empty"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"      # 壞幀 / 暫時看不到時用,不可省略

@dataclass(frozen=True)
class Seat:
    id: str                 # 穩定唯一 ID,例如 "F2-A07"
    label: str              # 給人看的名字
    zone: str               # 區域,供 dashboard 分組
    roi_polygon: list[tuple[float, float]]   # 影像座標多邊形(歸一化 0~1)

@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]  # x1,y1,x2,y2 歸一化
    confidence: float
    cls: str                # 目前只用 "person"
    frame_ts: float         # 該幀的時間戳(秒)

@dataclass(frozen=True)
class SeatState:
    seat_id: str
    status: Status
    since_ts: float         # 進入目前 status 的時間
    last_update_ts: float   # 最後一次被評估的時間
    confidence: float       # 這次判斷的信心
```

## 不變量(Invariants)
- `seat_id` 必須對應到某個已存在的 `Seat.id`。
- `status = UNKNOWN` 是合法狀態,代表「這輪沒有可信判斷」,不可被當成 EMPTY 寫進歷史。
- `since_ts <= last_update_ts`。

## 變更流程(AI 只能提案,不能合併)
改這份契約 = 改全系統脊椎,必須:
1. 改本檔的型別定義
2. `schema_version` +1
3. 寫一支 append-only 的 SQLite migration
4. 人類簽核

migration 永遠 append-only,不准就地改舊欄位語意。
