# Example store

Fixture giống `~/.thyca` (phần nhớ). Session giả định **2026-08-17**.

```
example_store/
  SOUL.md                 # canonical — Active full, index ngay
  USER.md                 # canonical — Active full, index ngay
  MEMORY.md               # canonical — Active tail + index ngay + TTL
  memory/
    2026-08-13.md         # đã đóng → có trong memory.sqlite
    2026-08-16.md         # hôm qua → Active tail + đã index
    2026-08-17.md         # hôm nay → chỉ Active, không vào sqlite
  memory.sqlite           # mục lục derived: source_files + chunks + chunks_fts (lexical; vector đã gỡ 580ae03)
```

Markdown là sự thật. SQLite chỉ là mục lục. Search đọc sqlite; `get(path)` đọc `.md` thô.

## Một file daily → chunk

`memory/2026-08-13.md` (rút gọn comment):

```md
# 2026-08-13

## 08:00 — ăn sáng bún bò <!-- thyca {id,imp,exp} -->
- Ăn bún bò Huế ở quán X, 45k, khá ngon
- Nói chuyện với Luna về đồ án

## 19:30 — thịt quay Q1 <!-- thyca {id,imp,exp} -->
- 19:30 ăn thịt quay với bạn ở Q1, quán đông
- 20:30 bàn mai thử đồ nướng
```

`Chunker` không đụng I/O. Nó đọc chữ, tách:

| Lớp | Trong file | Thành gì |
|-----|------------|----------|
| Timeline | `# 2026-08-13` + tên file | partition `timeline_day`; không phải chunk |
| Session | `## HH:mm — title` + comment JSON | `session_id = ngày#id`; heading metadata; comment không vào FTS |
| Leaf | mỗi bullet / đoạn / fence | 1 hàng `chunks` |

Bốn bullet → bốn hàng. Heading không lặp vào body FTS.

| `chunk_id` | `session_id` | `text_raw` (FTS + snippet) | `text_norm` (trigram) |
|------------|--------------|----------------------------|------------------------|
| `2026-08-13#a1b2c3d4#1` | `2026-08-13#a1b2c3d4` | `- Ăn bún bò Huế ở quán X, 45k, khá ngon` | `- an bun bo hue o quan x, 45k, kha ngon` |
| `2026-08-13#a1b2c3d4#2` | `2026-08-13#a1b2c3d4` | `- Nói chuyện với Luna về đồ án` | `- noi chuyen voi luna ve đo an` |
| `2026-08-13#e5f6a7b8#1` | `2026-08-13#e5f6a7b8` | `- 19:30 ăn thịt quay với bạn ở Q1, quán đông` | `- 19:30 an thit quay voi ban o q1, quan đong` |
| `2026-08-13#e5f6a7b8#2` | `2026-08-13#e5f6a7b8` | `- 20:30 bàn mai thử đồ nướng` | `- 20:30 ban mai thu đo nuong` |

```text
.md  ──Chunker──►  Chunk
                     ├─ text_raw   → chunks + trigger chunks_fts
                     └─ text_norm  → rapidfuzz khi FTS < 3 hit
                          │
                          ▼
                   memory.sqlite
                          │
          search "ca phe" / "thit quya"  →  Hit.snippet = text_raw
```

Query `ca phe` khớp FTS không dấu trên `text_raw` (snippet vẫn `cà phê`). Query `thit quya` lệch FTS → trigram trên `text_norm`.

Không có lớp vector trên đường này.

## Cả store (mốc 2026-08-17)

| Thứ | Nơi | Example |
|-----|-----|---------|
| Nhật ký / hồ sơ | `*.md` | có |
| File đã index | `source_files` | 5 hàng — không gồm `2026-08-17.md` |
| Leaf | `chunks` | 11 hàng (`text_raw` + `text_norm`) |
| FTS | `chunks_fts` | đồng bộ từ `text_raw` |
| Hôm nay | chỉ Active prompt | `UNIQUE_TODAY_TOKEN` không search được |
| Hội thoại | `sessions/*.jsonl` | không thuộc store này |

Canonical không có `## HH:mm` thì cả file là một session (`canonical#soul`, `canonical#user`, `memory#id`). Leaf ngắn `<20` ký tự gộp với leaf kế — `USER.md` thành 2 hàng, không phải 3.

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
