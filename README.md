# Thyca

Trợ lý cá nhân đa công việc, giai đoạn **trước beta (pre-beta)**. Thyca học dần từ những gì bạn chia sẻ và cách bạn làm việc — trò chuyện, viết code, đọc tài liệu, tra web, chạy lệnh trên máy bạn — và **ghi nhớ** để lần sau không phải nhắc lại.

**Phiên bản hiện tại: 0.87.0.dev0**

Thyca là một **đội bot**: Thyca là bot quản lý, bạn tạo thêm bot cho từng vai trò. Mỗi bot có persona và bộ nhớ riêng, cùng đọc `SOUL.md` + `USER.md` chung về bạn.

## Màn hình

6 màn trong một SPA tại `http://127.0.0.1:19502/`, chuyển bằng thanh nav góc phải; bấm bot trên sidebar để lọc màn theo bot đó. Mọi màn dùng chung URL gốc, không tải lại ứng dụng khi chuyển trang; các liên kết trang cũ vẫn mở đúng màn và giữ bộ lọc/định danh liên quan:

![Nhật ký](docs/images/webui-memories.png)

| Màn | Làm gì |
|---|---|
| **Trò chuyện** | Chat chính. Xem suy nghĩ model đang chạy, đổi model/mức suy nghĩ trên ô soạn tin, dừng/thử lại lượt. Màn duy nhất có mục Hội thoại chung (nhiều bot). |
| **Nhật ký** | Bộ nhớ các bot ghi. Thẻ tổng quan, lọc loại (Ghi nhớ/Thói quen/Task) + mức dùng, tìm kiếm, phân trang. |
| **Hồ sơ** | Không bot nào mở: 3 file chung sửa trực tiếp (`USER.md`/`SOUL.md`/`IDENTITY.md`). Chọn bot ở thanh đội để mở hồ sơ riêng: persona (chỉ đọc) + model & mức suy luận của đúng bot đó. |
| **Provider** | Một trình chỉnh sửa kết nối + model dùng chung: URL/API key, tải danh sách, chọn hoặc nhập model, rồi lưu rõ ràng. Tải danh sách chỉ đọc `/models`, không thử gọi model hay lưu cấu hình. Không chỉnh theo từng bot ở đây. |
| **Sử dụng** | Ba góc nhìn Sử dụng, Chi phí, Trace trong cùng dashboard; lọc bot và kỳ rồi mở chi tiết reasoning/tool đầy đủ. Lượt chưa gắn bot chỉ có trong Tổng quan. |
| **Cài đặt** | Mặc định chung (giới hạn/nhịp làm việc của đội). |

![Hồ sơ](docs/images/webui-profile.png)
![Sử dụng](docs/images/webui-dashboard.png)
![Chi tiết lượt trong màn Sử dụng (ảnh tham khảo cũ)](docs/images/webui-trace.png)

## Bộ nhớ

Markdown dưới `~/.thyca` là nguồn sự thật, SQLite chỉ là index:

- Note đáng nhớ ghi vào `memory/YYYY-MM-DD.md` — file hôm nay inject thẳng vào ngữ cảnh mỗi lượt.
- Qua ngày mới đưa vào index (`memory_search`, FTS5 + trigram) — tìm bằng từ khóa.
- `SOUL.md` + `USER.md` dùng chung mọi bot; `IDENTITY.md` chỉ của Thyca; mỗi bot có `TWIN.md` + `USER.md` riêng (`bots/<id>/`) cho thói quen bot tự suy luận.
- Model và mức suy luận riêng của từng bot nằm ở `bots/<id>/settings.json` (chọn bot ở thanh đội trên trang Hồ sơ, mục model của bot đó). `TWIN.md` chỉ giữ căn tính/persona/quy tắc — file canonical không còn key `model`.

## Cài đặt

Cần Linux, Python 3.14+, `uv`, API key OpenAI-compatible:

```bash
curl -LsSf https://raw.githubusercontent.com/Flowerf19/thyca-ai/main/install.sh | sh
thyca --serve        # SPA: http://127.0.0.1:19502/
```

Lần đầu, nếu model hiệu lực chưa có API key, Thyca đưa bạn tới Provider và khóa các thao tác ở màn khác cho tới khi máy chủ xác nhận sẵn sàng. Điền tên kết nối + Base URL + API key → **Tải model** (chỉ lấy danh sách, không gọi model) → chọn hoặc nhập model → **Lưu và dùng**. Cấu hình được ghi trên máy; key đã lưu không gửi lại giao diện. Key trong biến môi trường cũng được máy chủ tính khi xác định trạng thái sẵn sàng, kể cả model chưa có trong danh mục.

Nâng cấp: `uv tool upgrade thyca-ai` hoặc chạy lại `install.sh`.

**Chat**: khung soạn mặc định để trống (theo cấu hình bot: model trong Hồ sơ khi chọn bot, provider suy từ model, mức suy luận đã lưu hoặc của model/provider); chỉ giá trị bạn chọn mới ghi đè lượt đó — không đổi gì đã lưu. Gõ `@tên-bot` để bot khác trả lời vẫn áp cấu hình của bot đó.

**CLI**: `thyca -p "câu hỏi"`, REPL chat liên tục, `--continue` nối phiên, `--model` đổi model.

**Từ mã nguồn**: `uv sync` → `uv run thyca --serve --daemon` → `uv run pytest -q`.

## Tài liệu & góp ý

`.agents/README.md` cho agent contributor, `.agents/plans/` kế hoạch, `.agents/decisions/` quyết định kiến trúc. Góp ý tại [GitHub Discussions](https://github.com/Flowerf19/thyca-ai/discussions).

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Nguyễn Hoà (Flowerf19).
