# Cấu hình Thyca — đọc trước khi sửa

Dành cho user và agent được nhờ cấu hình Thyca. Đọc hết mục 1–3 trước khi
đụng vào config; các mục còn lại tra cứu khi cần.

## 1. Hiểu đúng 3 khái niệm (quan trọng nhất)

Thyca gọi AI qua **provider** (máy chủ API), không gọi model "trơ trụi":

- **Provider** = địa chỉ máy chủ (`baseUrl`) + chìa khóa riêng (`apiKey`).
  Mỗi provider có key riêng, không dùng chung.
- **Model** luôn thuộc về đúng 1 provider. Chọn model nào trong khung chat
  thì Thyca tự dùng endpoint + key của provider đó. Không bao giờ có chuyện
  "đổi model nhưng giữ nguyên provider".
- **Mặc định**: `defaultModel` là model chat dùng khi bạn không chọn gì;
  `defaultProvider` là provider dự phòng cho model chưa gán.

Lỗi kinh điển: thêm model của provider B nhưng quên gán provider → Thyca gọi
model đó lên máy chủ A → provider trả `model_not_found`. Cách tránh: mỗi lần
thêm model, gán đúng provider cho nó (mục 2).

## 2. Cấu hình bằng WebUI (nên dùng cách này)

Mở Thyca (`thyca --serve`, http://127.0.0.1:8765) → Cài đặt → **Provider**:

1. **Thêm provider**: bấm Thêm, đặt tên ngắn (chữ, số, `_`, `-`), nhập endpoint
   và dán API key của đúng provider đó.
2. **Kiểm tra & tải model**: bấm nút này để Thyca thử key và liệt kê model
   provider có. Key sai/key thiếu sẽ báo ngay tại đây.
3. **Thêm model**: chọn model từ danh sách vừa tải (hoặc nhập đúng ID),
   bấm Thêm. Model tự thuộc về provider đang sửa.
4. **Đặt mặc định**: chọn model chat mặc định (và provider mặc định nếu có
   nhiều provider).
5. **Lưu cấu hình**: sau khi lưu, Thyca **tự test API** bằng một câu hỏi nhỏ
   và báo `OK (model · ms)` hoặc lỗi cụ thể. Thấy OK mới yên tâm dùng.
6. Muốn kiểm tra lại bất cứ lúc nào: nút **Test API** của từng provider.

Xóa provider còn model sẽ bị chặn — chuyển model sang provider khác trước.
Đổi tên provider thì model đi theo, không phải gán lại.

## 3. Sửa config bằng tay (khi không mở được WebUI)

- Hai file đi cặp: `~/.thyca/config.json` (cấu hình, không secret) và
  `~/.thyca/auth.json` (chỉ chứa key). Cả hai mode 0600, thư mục 0700.
  Không commit hai file này, không copy key sang file khác.
- Đọc config hiện tại trước, chỉ đổi đúng chỗ cần đổi.
- Khung `config.json` (giá trị ví dụ, không có key):

```json
{
  "providers": {
    "ten-provider": {
      "baseUrl": "https://dia-chi-api/v1",
      "reasoningEffort": "high"
    }
  },
  "defaultProvider": "ten-provider",
  "defaultModel": "ten-model",
  "models": {
    "ten-model": { "provider": "ten-provider" }
  }
}
```

- Khung `auth.json`:

```json
{
  "providers": {
    "ten-provider": { "apiKey": "dán-key-của-provider-này" }
  }
}
```

- `baseUrl` phải bắt đầu `http://` hoặc `https://`, không kèm `/models` hay
  `/chat/completions` (Thyca tự nối).
- Mỗi model trong `models` nên ghi rõ `"provider"`. Bỏ trống nghĩa là dùng
  provider mặc định.
- Xong thì chạy `thyca --version`: không báo lỗi tức file đúng định dạng.
  Rồi mở WebUI bấm **Test API** để chắc gọi được thật.

## 4. API key nằm ở `auth.json` (ưu tiên từ trên xuống)

1. `auth.json`: WebUI lưu key vào đây khi bạn dán key và bấm Lưu. Sửa tay thì
   mở đúng file này — đừng ghi key vào `config.json` (lần lưu sau sẽ bị gỡ).
2. Biến môi trường: khi provider không có key trong `auth.json`, Thyca đọc
   biến có tên trong `apiKeyEnv` (mặc định `THYCA_TOKEN`). Cách này chỉ là
   dự phòng — bình thường không cần đụng tới bash.

`apiKeyEnv` chỉ là *tên biến*, không phải key. WebUI giấu field này; nếu vẫn
muốn dùng env cho nhiều provider thì mỗi provider đặt một tên riêng
(ví dụ `META_KEY`, `OPENROUTER_KEY`), đừng để trùng nhau. Đừng dùng tên
`OPENAI_API_KEY` để tránh tool khác quét nhầm key của Thyca.

Không bao giờ in key ra màn hình, log, hay chat. Trang Provider không bao giờ
trả key về trình duyệt (ô key luôn trống sau khi tải).

## 5. Kiểu API của provider (Chat completions / Responses)

Mỗi provider có 1 field `api` chọn cách Thyca nói chuyện với nó:

- `openai_chat` (mặc định) — endpoint `/chat/completions`, chuẩn phổ thông,
  dùng được với hầu hết provider (OpenAI, OpenRouter, local, commandcode…).
- `openai_responses` — endpoint `/responses`: cần khi provider chỉ trả thinking
  qua API này (ví dụ meta.ai). Không có nó thì panel "đang suy nghĩ" của model
  meta chỉ có đồng hồ, không có chữ (provider vẫn tính tiền reasoning).

Đổi ở WebUI (dropdown "Kiểu API" trong phần Nhà cung cấp) hoặc sửa tay
`providers["<id>"].api`. Đổi model sang provider khác thì API tự đi theo
provider đó. Chú ý: mức effort `low` có thể không trả summary thinking —
dùng `high` trở lên để thấy chữ.

## 6. Mức suy luận (thinking) của từng model
Mỗi model có bộ mức suy luận riêng (model này `low/high/max`, model khác có
thêm `minimal/medium`). Khai báo trong `reasoningEfforts` của model; mức đang
dùng (`reasoningEffort`) phải nằm trong bộ đó, khung chat sẽ tự hiện đúng các
mức khi bạn chọn model.

Tra bộ mức trong tài liệu/API reference chính thức của model — không đoán mò.
Model không hỗ trợ `reasoning_effort` (ví dụ gpt-4o) thì Thyca tự thử lại
không kèm tham số này, không cần xóa tay.

## 7. Các mục khác (đụng tới thì đọc)

- **Giới hạn chung** (`limits`): `loopMax` 1–200 (số vòng agent mỗi lượt),
  `hotTailKB` 1–64 (nhớ nóng đưa vào prompt), `contextTokens` 1000–2000000
  (trần ngữ cảnh). Từng model có thể khai riêng đè lên.
- **Giá token** (`models[...].input/cache/output`, USD/1M token): chỉ để tính
  chi phí hiển thị. Điền thì điền đủ cả 3, bỏ trống thì Thyca dùng bảng giá
  có sẵn. Đừng bịa giá — tra trang pricing của provider.
- **Múi giờ** (`timeline.timezone`, ví dụ `Asia/Ho_Chi_Minh`): sai là lệch
  toàn bộ memory và nhật ký. Config mới tự lấy theo giờ máy.
- **MCP servers**: công cụ ngoài chạy qua stdio
  (`{command, args, env}`), tên chỉ gồm chữ/số/`_`/`-`. Lỗi MCP hiện ở log
  khởi động, không chặn chat.

## 8. Lỗi thường gặp và xem log ở đâu

Phân biệt 2 nút: **Kiểm tra** chỉ thử key + lấy danh sách model;
**Test API** gọi thử một câu thật — Test OK mới chắc chắn dùng được.

| Thấy gì | Nghĩa là gì | Làm gì |
|---|---|---|
| `model '...' không có trên provider (HTTP 404)` | Model không thuộc provider đang gọi | Gán model đúng provider (mục 1–2) |
| `API key bị từ chối (HTTP 401/403)` | Sai key hoặc key của provider khác | Dán lại đúng key của provider đó rồi Test |
| `không kết nối được provider` / quá thời gian | Sai endpoint, mất mạng, hoặc máy chủ chậm | Kiểm tra `baseUrl`, thử lại |
| `chưa có API key` | Provider chưa có key ở cả 3 cách (mục 4) | Điền key rồi lưu lại |

Xem chi tiết lỗi ở 3 nơi (message ở cả 3 là một, không chứa key):

1. Khung chat: dòng đỏ dưới câu trả lời hiện đúng message của provider.
2. Trang Trace: lượt lỗi có dòng message đỏ dưới tên model.
3. File `~/.thyca/serve.log`: mỗi lượt lỗi một dòng `turn failed ...`,
   mỗi lần test một dòng `provider test ...`.

## 9. Quy tắc an toàn (nhớ kỹ)

1. Không in/dán API key ra chat, log, commit, hay file khác ngoài `auth.json`.
2. Giữ mode 0600 cho `config.json` và `auth.json`, 0700 cho `~/.thyca`.
3. Sửa xong luôn Test API trước khi báo "xong".
