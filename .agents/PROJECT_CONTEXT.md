# Bối cảnh dự án

Mục tiêu của file này là mô tả trạng thái thật tại HEAD. Mã nguồn, test và `FEATURE_MAP.md` là nguồn sự thật. Kế hoạch và quyết định cũ có thể đã lỗi thời.

## Runtime hiện tại

- Phiên bản: `0.88.0.dev4`. Đồng bộ ở `pyproject.toml`, `thyca/__init__.py`, `uv.lock`, `README.md` và `.agents/`.
- Điểm vào CLI nằm ở `thyca/app/cli.py`. Máy chủ WebUI vòng lặp nội bộ (loopback) nằm ở `thyca/serve/`.
- WebUI là một SPA gốc duy nhất. File vào là `thyca/webui/ui/chat/index.html` tại `/`.
- Sáu đường workspace cũ vẫn mở đúng màn. Đường Trace cũ được chuẩn hóa vào màn Sử dụng (Usage).
- Factory và workspace của từng trang nằm dưới `thyca/webui/ui/<page>/`. Router và mã vỏ dùng chung nằm dưới `thyca/webui/ui/share/`.
- Trạng thái trang, bộ lọc và fragment nằm trong lịch sử trình duyệt. URL nhìn thấy vẫn là `/`.
- Điều hướng trong app phải giữ route và bản nháp đã cache. Điều hướng chỉ bật stylesheet của trang đang mở cộng style markdown dùng chung.
- Không dùng iframe. Không tạo điểm vào app thứ hai.
- Avatar bot là huy chương nhân vật tròn (medallion): mã ở `thyca/webui/ui/share/avatars/avatars.js`, sidebar xếp thẻ bot lưới 2 cột (`thyca/webui/ui/share/sidebar.css`).
- Lượt gốc và lượt con dùng chung `BotRuntime` qua `ChatApp` và `WorkspaceTurnService`.
- CLI đi qua `ChatApp.turn`. Không còn bộ thực thi lượt gốc riêng. Không còn `thyca/app/run_turn.py`.
- CLI chọn hội thoại bằng `--bot <bot_id>` (DM) hoặc `--group <group_id>` (nhóm). Hai cờ này loại trừ nhau.
- Cờ cũ `--session` và `--continue` đã bị gỡ.

## API hội thoại

- DM dùng `/api/bots/<bot_id>/dm/...`.
- Nhóm dùng `/api/groups/<group_id>/...`.
- Danh sách hội thoại dùng `/api/conversations`.
- Mọi yêu cầu tới `/api/sessions*` nhận `404`. Đây là cắt hẳn (hard-cut), không phải chuyển hướng.

## Lưu trữ

### Hội thoại công khai

- Nơi lưu duy nhất là SQLite tại `<root>/conversations.db`.
- Mã nằm ở `thyca/conversations/store/`. Hai cửa ghi là `AdminStore` và `AgentStore`.
- Schema hiện tại là v2 (`SCHEMA_VERSION = 2` trong `thyca/conversations/store/database.py`).
- Cơ sở dữ liệu chạy ở chế độ WAL. Gốc có marker và khóa riêng để chống mở nhầm gốc.
- Không còn lưu hội thoại công khai bằng JSONL. Không có đường chuyển dữ liệu cũ (migration).
- Hàm `require_no_legacy_conversations` trong `thyca/conversations/store/runtime.py` chặn khởi động khi còn `sessions/*.jsonl`. `ChatApp` gọi hàm này lúc dựng app.
- Bốn class điều phối trong `thyca/conversations/runtime/` là `ConversationRuntime`, `ConversationManager`, `ConversationStore`, `ConversationCompactor` (đã cắt sạch, không còn alias `Session*`).

### Lịch sử thực thi canonical

- `WorkspaceStore` quản lý binding bot/cửa sổ và con trỏ.
- Mỗi bot có một `WorkspaceTimeline` canonical duy nhất trong `thyca/bots/timeline.py`.
- File trên đĩa là `bots/<bot>/workspace/timeline.jsonl`.
- Hội thoại công khai là mô hình đọc tổng hợp (aggregate read model). Nó không phải bản ghi thực thi.
- Không còn `room_sessions.json`, timeline bóng (shadow), hay API thăng/giáng cấp (promotion/demotion), chuyển đổi (cutover) và khôi phục (rollback).
- Lịch sử chat giữ tin nhắn của user và câu trả lời tổng hợp của assistant.
- Delta suy nghĩ trực tiếp được stream trong lúc lượt chạy.
- Chi tiết Trace đối chiếu tham chiếu bot/span đáng tin cậy tới sự kiện `message` canonical gốc trong đúng cửa sổ phòng. Chi tiết này loại bản sao `view_reset`. Nhờ vậy chi tiết thực thi còn nguyên sau khi nén (compaction) mà không bị trùng.
- Nén thủ công xử lý từng thành viên phòng độc lập. Việc này chỉ chạy khi bot đó đã có ngữ cảnh canonical.
- Kết quả nén báo từng thành viên dưới khóa `per_bot`. Việc này không tạo binding còn thiếu.

### Thứ tự chọn model hiệu lực

- Ưu tiên cao nhất là giá trị ghi đè tường minh cho thao tác đó.
- Tiếp theo là model trên thẻ bot nếu khác mặc định.
- Cuối cùng là mặc định toàn cục đã cấu hình.
- Đồng hồ ngữ cảnh là số ước tính. Nó bỏ qua các đầu vào chưa biết lúc đọc: văn bản user tiếp theo, lô hộp thư (mailbox) sắp tới và kết quả truy hồi theo truy vấn.

### Cài đặt model theo bot

- File `bots/<id>/settings.json` sở hữu `{"model": {"id", "thinking"}}` và tùy chọn `"limits": {"loopMax", "hotTailKB", "contextTokens", "softTimeoutS"}` (trần ngữ cảnh `32000–1000000`, mặc định `272000`).
- File `TWIN.md` giữ danh tính, persona và chính sách. Ghi canonical không còn khóa `model` trong `TWIN.md`.
- Lúc đọc, hệ thống nạp thẻ từ settings nếu có. Nếu không có, hệ thống quay về model thẻ cũ mà không ghi chuyển đổi.
- Settings hỏng làm bot bị đánh dấu không hợp lệ.
- Trang Hồ sơ từng bot (`?bot=<id>`, `#bot-settings-form` kèm mục `<details id="bot-advanced">` "Nâng cao") là UI cấu hình model, mức suy luận và giới hạn chạy theo bot duy nhất ngoài admin tools; hộp thoại Cài đặt cũ đã bị gỡ và `/ui/settings` định tuyến về Hồ sơ.
- Không có `?bot` thì trang Hồ sơ hiện các file canonical dùng chung. Mỗi bot (kể cả Thyca) có tab riêng.
- Thứ tự lúc chạy lượt: ghi đè model/mức suy luận cho lượt đó, rồi cài đặt đã lưu của bot, rồi mặc định toàn cục. Mức suy luận được kiểm tra theo tập hợp của chính model hiệu lực.
- Bên nhận A2A tự resolve thẻ của mình. Chỉ mức suy luận ghi đè tường minh mới được chuyển tiếp.

## Bố cục gói

- Layout backend là `thyca/{agents,app,bots,configs,conversations,core,llms,memories,platform,seeds,serve,skills,tools,webui}/`.
- Cấm file lẻ ở tầng cao nhất, trừ `__init__.py` và `__main__.py`. Cấm shim.
- `core/` và `platform/` là lá (leaf): chỉ dùng thư viện chuẩn, không import `thyca.*` khác.
- `serve/` không import runtime `app/` ở mức module. Chỉ dùng `TYPE_CHECKING` và import lười (lazy).
- MCP client nằm ở package `thyca/tools/mcp/` (`process`, `catalog`, `manager`). Không còn `thyca/tools/mcp.py`.
- Mọi ranh giới trên được kiểm tra tự động trong `scripts/checks/check-tier1.sh`.

## MCP runtime

- Boot: `ChatApp` chỉ `schedule` connect song song; không chờ MCP xong mới mở app.
- Mỗi turn: `_sync_mcp` gọi `sync` (= `schedule` + `settle`), rồi upsert tool specs vào registry.
- Server từng `list_tools` thành công giữ `last_tools` in-memory: reconnect lỗi → stub/`is_error`; đang connect → handler chờ rồi forward.
- Cold fail (chưa có catalog) mới vào `failed_servers()` / `<mcp_status>` trong system prompt. Stub đã cover thì không double-signal.
- `START_TIMEOUT` mặc định 10s mỗi server; `merge_env` giữ biến chứng chỉ TLS thường gặp. Không tự respawn process chết giữa phiên.

## Dữ liệu và an toàn

- User đã cho phép đặt lại (reset) và đã xóa toàn bộ dữ liệu `~/.thyca`. Không kiểm tra, không truy cập và không tạo lại `~/.thyca` trong quá trình phát triển hay chạy test.
- `ensure_default(target)` truyền `target.parent` cho `write_config_guide` nên các root cô lập và test không chạm vào `~/.thyca`.
- Không thêm ví dụ API key vào tài liệu hướng dẫn dự án.
- Giữ nguyên các thay đổi không liên quan trong working tree và artifact untracked của user.
- Bộ nhớ L2 dùng 1 file ngày `memory/YYYY-MM-DD.md` với 3 sổ hành vi tách tại `thyca/memories/ledgers.py` (`Task`, `Habit`, `Other`); SQLite derived index ở `thyca/memories/schema.py` (`SCHEMA_VERSION = "10"`, FTS5 unigram + bigram + trigram, không vector). Lá bộ nhớ dùng `memory_id` / `latest_memory_id`.
- Serve chỉ chạy loopback. Không để lộ secret, đường dẫn nội bộ hay stack trace qua API.
- Markdown của bộ nhớ vẫn là nguồn sự thật của bộ nhớ. SQLite của bộ nhớ chỉ là mục lục suy ra (derived index).

## Việc còn mở (nói rõ, không giấu)

- Chưa có bên phát hành vòng đời/đặt lại ngoài luồng khởi động cục bộ của `ChatApp`.
- Chưa chốt chính sách giữ/xóa dữ liệu cũ (retention policy) cho SQLite hội thoại.
- Chưa đóng nợ cách ly private qua `bash` và daily prefix vô chủ (`docs/TECH_DEBT.md` mục 10).
- Chưa thiết lập kiểm chứng chất lượng nhà cung cấp thật tự động trong CI (`tests/test_live_memory_agent.py` là opt-in qua `THYCA_LIVE=1`).

## Kiểm chứng và review

- **Tier 1:** `bash scripts/checks/check-tier1.sh` (chạy `ruff check .`, `node --check`, kiểm tra ranh giới + đồng bộ version, `scripts/checks/check-no-weakened-tests.sh`, `scripts/checks/check-comment-ban.sh`, `git diff --check`) và chạy tự động trong `.github/workflows/ci.yml`.
- **Tier 2:** `uv run pytest -q` + kiểm chứng runtime thật (CLI / loopback HTTP / browser CDP khi có Chrome/Chromium qua `chromium_binary()`).
- **Verification Artifacts:** xuất bằng chứng chạy thật vào `.agents/artifacts/manifest.md` (gitignored) và kiểm tra bằng `bash scripts/checks/check-artifacts.sh`.
- Một test tập trung hoặc provider giả không chứng minh trạng thái toàn suite, hành vi trình duyệt thật hay chất lượng nhà cung cấp thật.
