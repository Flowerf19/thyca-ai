# Example store

Fixture giống `~/.thyca` (phần nhớ). Mở session giả định **2026-08-17**.

```
example_store/
  SOUL.md                 # chữ — Active full, index ngay
  USER.md                 # chữ — Active full, index ngay
  MEMORY.md               # chữ — Active tail + index ngay + TTL
  memory/
    2026-08-13.md         # đã đóng → có trong memory.sqlite
    2026-08-16.md         # hôm qua → Active tail + đã index
    2026-08-17.md         # hôm nay → chỉ Active, KHÔNG có trong sqlite
  memory.sqlite           # mục lục: source_files + chunks + chunks_fts + chunks_vec
```

## Chữ vs index vs vector

| Thứ | Nơi lưu | Trong example |
|-----|---------|----------------|
| Nhật ký / hồ sơ | `*.md` | có |
| Mục lục file | `source_files` | 5 hàng (không gồm 2026-08-17) |
| Từng leaf | `chunks` (`text_raw` / `text_norm` / `embed_text`) | 11 hàng |
| FTS | `chunks_fts` | đồng bộ từ `text_raw` |
| Vector 640-d | `chunks_vec` (`float[640]`, cosine) | **bảng có, 0 hàng** — GOAL-003 mới insert |
| Hội thoại | `sessions/*.jsonl` | không thuộc store này |

`get` chỉ trả chữ. Vector không bao giờ vào `.md` hay tool result.

Tái tạo sqlite (từ repo root):

```bash
uv run python -c "
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from thyca.memory.archived import ArchivedMemory
root = Path('memories/example_store')
(root / 'memory.sqlite').unlink(missing_ok=True)
m = ArchivedMemory(root, timezone_name='Asia/Ho_Chi_Minh')
m.reindex(datetime(2026, 8, 17, 10, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')))
"
```

`chunks_vec` tạo tay bằng sqlite-vec; code production chưa tạo bảng này.
