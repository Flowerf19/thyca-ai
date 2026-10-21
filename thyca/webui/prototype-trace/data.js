/* Thyca Trace prototype — mock data (static, file:// friendly).
 * Turn totals (promptTokens / cachedTokens / completionTokens / costUsd /
 * totalTokens / endedAt) are DERIVED from botBreakdown by __protoFinalize()
 * at the bottom, so breakdown sums always equal turn totals.
 * Cost rule: a turn is priced only when EVERY breakdown part has a numeric
 * costUsd; otherwise the turn cost is null (unpriced, never $0).
 */
(function () {
  "use strict";

  window.PROTO_BOTS = {
    thyca: { id: "thyca", name: "thyca", role: "Quản lý đội bot", color: "#a65a42", glyph: "quill" },
    coder: { id: "coder", name: "thyca-coder", role: "Coding assistant", color: "#3e5a5c", glyph: "code" },
    designer: { id: "designer", name: "designer", role: "Designer", color: "#2f5d46", glyph: "brush" },
    writer: { id: "writer", name: "writer", role: "Chấp bút", color: "#b8863c", glyph: "nib" },
    research: { id: "research", name: "research", role: "Tra cứu", color: "#6b4a33", glyph: "lens" }
  };

  window.PROTO_SESSIONS = [
    {
      id: "s-hopdoi", title: "Họp đội cuối tuần", kind: "group",
      bots: ["thyca", "coder", "designer", "writer", "research"],
      turns: [
        {
          index: 1, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-17T08:02:11+07:00", latencyMs: 18400, requests: 2,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 1240, cachedTokens: 320, completionTokens: 410, costUsd: 0.000372 },
            { botId: "research", requests: 1, promptTokens: 980, cachedTokens: 210, completionTokens: 260, costUsd: 0.000288 }
          ],
          user: "Tóm tắt giúp anh tiến độ tuần này của cả đội.",
          assistant: "Đã gom xong: 5 đầu việc hoàn thành, 2 việc dở dang. coder xong khung landing, designer chốt bảng màu, writer gửi bản thảo bản tin.",
          steps: [
            { type: "input", name: "Nhận câu hỏi", detail: "Câu hỏi tiếng Việt, 1 câu, không tệp đính kèm.", latencyMs: 120 },
            { type: "thinking", name: "Lập dàn ý", detail: "Chia theo từng bot: việc xong, việc dở, việc tuần sau.", latencyMs: 2100 },
            { type: "tool", name: "research · tra cứu log", detail: "Đọc 12 lượt gần nhất của 5 bot trong 7 ngày.", latencyMs: 9400 },
            { type: "output", name: "Soạn tóm tắt", detail: "5 ý chính, mỗi ý một dòng, kèm tên bot phụ trách.", latencyMs: 6780 }
          ]
        },
        {
          index: 2, model: "gpt-4o", status: "completed",
          startedAt: "2026-10-17T08:09:44+07:00", latencyMs: 42300, requests: 3,
          botBreakdown: [
            { botId: "coder", requests: 2, promptTokens: 3120, cachedTokens: 1180, completionTokens: 940, costUsd: 0.00846 },
            { botId: "designer", requests: 1, promptTokens: 1450, cachedTokens: 400, completionTokens: 380, costUsd: 0.00342 }
          ],
          user: "Xem giúp khung landing mới, có chỗ nào vỡ trên di động không?",
          assistant: "Phát hiện 2 điểm vỡ ở 360px: cụm nút CTA tràn và ảnh hero méo tỉ lệ. Đã gửi kèm đoạn CSS sửa cho coder.",
          steps: [
            { type: "input", name: "Nhận yêu cầu soát", detail: "Kèm đường dẫn xem trước của trang landing.", latencyMs: 150 },
            { type: "tool", name: "coder · đọc mã nguồn", detail: "Đọc 3 tệp: layout, hero, nút CTA (≈ 900 dòng).", latencyMs: 16800 },
            { type: "tool", name: "designer · đối chiếu mẫu", detail: "So với bản mẫu Figma: sai 2 khoảng cách, 1 cỡ chữ.", latencyMs: 12400 },
            { type: "thinking", name: "Chốt danh sách lỗi", detail: "2 lỗi vỡ layout, 3 góp ý nhỏ về khoảng trắng.", latencyMs: 3100 },
            { type: "output", name: "Báo cáo soát", detail: "Liệt kê lỗi theo mức độ, kèm gợi ý sửa từng chỗ.", latencyMs: 9850 }
          ]
        },
        {
          index: 3, model: "claude-sonnet-4-5", status: "completed",
          startedAt: "2026-10-17T08:21:02+07:00", latencyMs: 31700, requests: 2,
          botBreakdown: [
            { botId: "writer", requests: 1, promptTokens: 2210, cachedTokens: 640, completionTokens: 1120, costUsd: 0.009744 },
            { botId: "research", requests: 1, promptTokens: 1730, cachedTokens: 520, completionTokens: 410, costUsd: 0.004824 }
          ],
          user: "Viết đoạn mở đầu bản tin tuần 42, giọng vui nhưng gọn.",
          assistant: "Tuần 42 khép lại với bản landing mới lên sóng và 40 lượt góp ý từ người dùng thử. Mời cả đội đọc 3 điểm nhấn dưới đây.",
          steps: [
            { type: "input", name: "Nhận đề bài", detail: "Chủ đề: tổng kết tuần 42. Giọng: vui, gọn.", latencyMs: 110 },
            { type: "tool", name: "research · gom số liệu", detail: "Số lượt dùng thử, tỉ lệ hoàn thành, 3 trích dẫn.", latencyMs: 11200 },
            { type: "thinking", name: "Chọn góc mở", detail: "Mở bằng con số nổi bật nhất: 40 lượt góp ý.", latencyMs: 2800 },
            { type: "output", name: "Viết đoạn mở", detail: "3 câu, 62 chữ, đọc thử trong 20 giây.", latencyMs: 17590 }
          ]
        },
        {
          index: 4, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-17T08:35:37+07:00", latencyMs: 52100, requests: 5,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 1890, cachedTokens: 700, completionTokens: 520, costUsd: 0.000512 },
            { botId: "coder", requests: 1, promptTokens: 1640, cachedTokens: 590, completionTokens: 480, costUsd: 0.000451 },
            { botId: "designer", requests: 1, promptTokens: 1320, cachedTokens: 430, completionTokens: 390, costUsd: 0.000368 },
            { botId: "writer", requests: 1, promptTokens: 1180, cachedTokens: 350, completionTokens: 440, costUsd: 0.000382 },
            { botId: "research", requests: 1, promptTokens: 1510, cachedTokens: 560, completionTokens: 370, costUsd: 0.000378 }
          ],
          user: "Mỗi bot một câu: tuần sau ưu tiên việc gì?",
          assistant: "thyca: chốt lịch release. coder: sửa 2 lỗi vỡ layout. designer: hệ màu cho trang giá. writer: bản tin tuần 43. research: đối chiếu phản hồi người dùng.",
          steps: [
            { type: "input", name: "Nhận câu hỏi vòng tròn", detail: "Yêu cầu đúng 1 câu trả lời cho mỗi bot.", latencyMs: 130 },
            { type: "thinking", name: "Chia lượt hỏi", detail: "Hỏi lần lượt 5 bot, mỗi bot tối đa 60 chữ.", latencyMs: 3400 },
            { type: "tool", name: "cả đội · thu thập", detail: "5 lượt gọi song song, lượt chậm nhất 9,4 giây.", latencyMs: 9400 },
            { type: "output", name: "Ghép 5 câu trả lời", detail: "Giữ nguyên chữ của từng bot, thêm tên mở đầu.", latencyMs: 39170 }
          ]
        },
        {
          index: 5, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-17T08:47:20+07:00", latencyMs: 12900, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 860, cachedTokens: 410, completionTokens: 230, costUsd: 0.000196 }
          ],
          user: "Chốt lại 3 việc phải xong trước thứ Hai.",
          assistant: "1) Sửa 2 lỗi vỡ layout trang landing. 2) Duyệt bản tin tuần 42. 3) Chốt lịch release 1.4.2.",
          steps: [
            { type: "input", name: "Nhận yêu cầu chốt", detail: "Chỉ lấy việc có người nhận và hạn cụ thể.", latencyMs: 100 },
            { type: "thinking", name: "Lọc 3 việc", detail: "Bỏ việc chưa có người nhận, giữ việc chặn release.", latencyMs: 1900 },
            { type: "output", name: "Danh sách chốt", detail: "3 gạch đầu dòng, mỗi dòng có tên người nhận.", latencyMs: 10900 }
          ]
        }
      ]
    },
    {
      id: "s-landing", title: "Trang landing đợt khuyến mãi", kind: "group",
      bots: ["thyca", "coder", "designer"],
      turns: [
        {
          index: 1, model: "gpt-4o", status: "completed",
          startedAt: "2026-10-16T14:11:05+07:00", latencyMs: 38600, requests: 2,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 1320, cachedTokens: 380, completionTokens: 540, costUsd: 0.00756 },
            { botId: "designer", requests: 1, promptTokens: 2080, cachedTokens: 610, completionTokens: 720, costUsd: 0.01008 }
          ],
          user: "Phác nhanh cấu trúc trang landing, 5 khối, nhấn vào nút đăng ký.",
          assistant: "Cấu trúc 5 khối: hero, lợi ích, bảng giá mini, chứng thực, CTA cuối trang. Nút đăng ký lặp lại 3 lần với cùng một màu nhấn.",
          steps: [
            { type: "input", name: "Nhận đề bài", detail: "5 khối, mục tiêu: tăng lượt bấm đăng ký.", latencyMs: 140 },
            { type: "thinking", name: "Sắp khối", detail: "Hero trước, CTA lặp lại sau mỗi 2 khối.", latencyMs: 3600 },
            { type: "tool", name: "designer · tham khảo mẫu", detail: "Xem 4 mẫu landing cùng ngành, ghi lại điểm chung.", latencyMs: 14900 },
            { type: "output", name: "Dàn ý 5 khối", detail: "Mỗi khối: tiêu đề, nội dung, gợi ý hình.", latencyMs: 19960 }
          ]
        },
        {
          index: 2, model: "gpt-4o", status: "completed",
          startedAt: "2026-10-16T14:26:51+07:00", latencyMs: 61400, requests: 3,
          botBreakdown: [
            { botId: "coder", requests: 2, promptTokens: 4210, cachedTokens: 1560, completionTokens: 1380, costUsd: 0.01248 },
            { botId: "designer", requests: 1, promptTokens: 990, cachedTokens: 220, completionTokens: 310, costUsd: 0.0027 }
          ],
          user: "Dựng bản HTML đầu tiên theo dàn ý, dùng màu giấy của Thyca.",
          assistant: "Đã dựng xong 5 khối với nền giấy #FBF4E5, chữ Fraunces cho tiêu đề. Còn 2 chỗ khoảng cách chưa đều, sẽ tinh chỉnh ở lượt sau.",
          steps: [
            { type: "input", name: "Nhận dàn ý đã duyệt", detail: "Dàn ý 5 khối từ lượt 1, kèm bảng màu Thyca.", latencyMs: 120 },
            { type: "tool", name: "coder · dựng khung", detail: "Viết 1 tệp HTML + 1 tệp CSS, khoảng 600 dòng.", latencyMs: 38200 },
            { type: "tool", name: "designer · kiểm màu", detail: "Đối chiếu 6 mã màu với token thương hiệu.", latencyMs: 9800 },
            { type: "output", name: "Bàn giao bản 1", detail: "Đường dẫn xem trước + 2 điểm cần tinh chỉnh.", latencyMs: 13280 }
          ]
        },
        {
          index: 3, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-16T15:02:19+07:00", latencyMs: 15800, requests: 1,
          botBreakdown: [
            { botId: "designer", requests: 1, promptTokens: 1120, cachedTokens: 480, completionTokens: 350, costUsd: 0.00032 }
          ],
          user: "Nút đăng ký nhìn chìm quá, làm nó nổi hơn mà vẫn hợp tông giấy.",
          assistant: "Đổi nút sang nền đất nung #a65a42, chữ kem, thêm viền mờ. Vẫn cùng họ màu nên không lạc tông.",
          steps: [
            { type: "input", name: "Nhận góp ý", detail: "Vấn đề: nút CTA chìm trên nền giấy.", latencyMs: 90 },
            { type: "thinking", name: "Chọn cách nổi", detail: "Tăng tương phản bằng màu nhấn, giữ bo tròn.", latencyMs: 1700 },
            { type: "output", name: "Gợi ý chỉnh nút", detail: "3 thay đổi CSS cụ thể, kèm mã màu.", latencyMs: 14010 }
          ]
        },
        {
          index: 4, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-16T15:19:33+07:00", latencyMs: 9600, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 740, cachedTokens: 290, completionTokens: 180, costUsd: 0.000168 }
          ],
          user: "Tóm tắt trạng thái landing để báo cáo.",
          assistant: "Landing xong bản 1, nút CTA đã nổi hơn, còn 2 khoảng cách chờ tinh chỉnh. Dự kiến duyệt cuối ngày mai.",
          steps: [
            { type: "input", name: "Nhận yêu cầu báo cáo", detail: "Tóm tắt trong 3 câu cho buổi họp.", latencyMs: 80 },
            { type: "thinking", name: "Gom trạng thái", detail: "Đọc lại 3 lượt trước, rút 3 ý chính.", latencyMs: 1500 },
            { type: "output", name: "Bản tóm tắt", detail: "3 câu: đã xong, còn dở, hạn tiếp theo.", latencyMs: 8020 }
          ]
        }
      ]
    },
    {
      id: "s-bantin", title: "Bản tin tuần 42", kind: "group",
      bots: ["thyca", "writer", "research", "designer"],
      turns: [
        {
          index: 1, model: "claude-sonnet-4-5", status: "completed",
          startedAt: "2026-10-15T09:44:27+07:00", latencyMs: 27400, requests: 2,
          botBreakdown: [
            { botId: "research", requests: 1, promptTokens: 2340, cachedTokens: 720, completionTokens: 560, costUsd: 0.00672 },
            { botId: "writer", requests: 1, promptTokens: 1290, cachedTokens: 310, completionTokens: 690, costUsd: 0.005184 }
          ],
          user: "Gom chất liệu cho bản tin tuần 42: số liệu + 3 câu chuyện.",
          assistant: "Đã gom: 40 lượt dùng thử, tỉ lệ hoàn thành 68%, 3 câu chuyện từ nhóm kiểm thử ở Hà Nội, Đà Nẵng, Cần Thơ.",
          steps: [
            { type: "input", name: "Nhận đề bài", detail: "Bản tin tuần 42, cần số liệu và câu chuyện.", latencyMs: 130 },
            { type: "tool", name: "research · tra cứu", detail: "Quét nhật ký 7 ngày, rút 5 con số đáng chú ý.", latencyMs: 14200 },
            { type: "thinking", name: "Chọn 3 câu chuyện", detail: "Ưu tiên chuyện có số liệu đi kèm.", latencyMs: 2600 },
            { type: "output", name: "Tập chất liệu", detail: "5 số liệu + 3 câu chuyện, ghi nguồn từng mục.", latencyMs: 10470 }
          ]
        },
        {
          index: 2, model: "claude-sonnet-4-5", status: "completed",
          startedAt: "2026-10-15T10:05:13+07:00", latencyMs: 44800, requests: 2,
          botBreakdown: [
            { botId: "writer", requests: 2, promptTokens: 3180, cachedTokens: 940, completionTokens: 1640, costUsd: 0.012672 }
          ],
          user: "Viết trọn bài, khoảng 400 chữ, giọng ấm.",
          assistant: "Bài 412 chữ: mở bằng con số 40 lượt góp ý, thân bài kể 3 câu chuyện, kết bằng lịch tuần 43. Giọng ấm, câu ngắn.",
          steps: [
            { type: "input", name: "Nhận tập chất liệu", detail: "Dùng toàn bộ chất liệu lượt 1.", latencyMs: 100 },
            { type: "thinking", name: "Dàn bài", detail: "Mở - 3 chuyện - kết, mỗi phần ~100 chữ.", latencyMs: 3900 },
            { type: "output", name: "Bản thảo 1", detail: "412 chữ, đọc thử hết 2 phút.", latencyMs: 40800 }
          ]
        },
        {
          index: 3, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-15T10:33:58+07:00", latencyMs: 14200, requests: 1,
          botBreakdown: [
            { botId: "designer", requests: 1, promptTokens: 980, cachedTokens: 350, completionTokens: 290, costUsd: 0.000263 }
          ],
          user: "Gợi ý 1 ảnh bìa cho bài, tông giấy, có chữ số 42.",
          assistant: "Ảnh bìa: nền giấy vân nhẹ, số 42 bằng chữ Fraunces cỡ lớn màu đất nung, dải lá khô mờ ở góc dưới.",
          steps: [
            { type: "input", name: "Nhận yêu cầu ảnh bìa", detail: "Tông giấy, có số 42.", latencyMs: 90 },
            { type: "thinking", name: "Chọn bố cục", detail: "Số lớn làm chủ đạo, chữ nhỏ phụ trợ.", latencyMs: 1600 },
            { type: "output", name: "Mô tả ảnh bìa", detail: "Đủ chi tiết để họa sĩ vẽ lại trong 1 buổi.", latencyMs: 12510 }
          ]
        },
        {
          index: 4, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-15T10:48:41+07:00", latencyMs: 8700, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 690, cachedTokens: 260, completionTokens: 170, costUsd: 0.000157 }
          ],
          user: "Duyệt bản tin để gửi?",
          assistant: "Duyệt gửi: bài đủ số liệu, ảnh bìa hợp tông. Nhớ kiểm tra lại tên 3 cộng tác viên trước khi bấm gửi.",
          steps: [
            { type: "input", name: "Nhận yêu cầu duyệt", detail: "Kiểm tra lần cuối trước khi gửi.", latencyMs: 80 },
            { type: "thinking", name: "Đối chiếu checklist", detail: "Số liệu, tên người, ảnh bìa, lịch gửi.", latencyMs: 1400 },
            { type: "output", name: "Kết luận duyệt", detail: "Đồng ý gửi + 1 lưu ý về tên cộng tác viên.", latencyMs: 7220 }
          ]
        }
      ]
    },
    {
      id: "s-sang14", title: "Sáng 14 thg 10", kind: "group",
      bots: ["designer", "thyca", "coder"],
      turns: [
        {
          index: 1, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-14T08:15:29+07:00", latencyMs: 11300, requests: 1,
          botBreakdown: [
            { botId: "designer", requests: 1, promptTokens: 1040, cachedTokens: 390, completionTokens: 330, costUsd: 0.0003 }
          ],
          user: "Bảng màu trang giá hiện tại có đủ tương phản chữ không?",
          assistant: "Chữ nhỏ màu nâu nhạt trên nền kem đạt tương phản vừa đủ, nhưng nút phụ thì hơi mờ. Nên đậm thêm một nấc.",
          steps: [
            { type: "input", name: "Nhận câu hỏi tương phản", detail: "Phạm vi: trang giá, chữ và nút.", latencyMs: 100 },
            { type: "tool", name: "designer · đo tương phản", detail: "Đo 6 cặp màu chữ-nền, 1 cặp dưới chuẩn.", latencyMs: 6200 },
            { type: "output", name: "Kết luận", detail: "5/6 cặp đạt, 1 cặp cần đậm thêm.", latencyMs: 5000 }
          ]
        },
        {
          index: 2, model: "gpt-4o", status: "completed",
          startedAt: "2026-10-14T08:31:47+07:00", latencyMs: 29500, requests: 2,
          botBreakdown: [
            { botId: "coder", requests: 1, promptTokens: 2760, cachedTokens: 990, completionTokens: 810, costUsd: 0.00702 },
            { botId: "thyca", requests: 1, promptTokens: 810, cachedTokens: 240, completionTokens: 260, costUsd: 0.00252 }
          ],
          user: "Sửa giúp cặp màu chưa đạt, rồi báo lại.",
          assistant: "Đã sửa: nút phụ đậm thêm một nấc (#8A5A40), đo lại đạt chuẩn. Không đụng tới 5 cặp còn lại.",
          steps: [
            { type: "input", name: "Nhận yêu cầu sửa", detail: "Chỉ sửa cặp chưa đạt, giữ nguyên phần còn lại.", latencyMs: 110 },
            { type: "tool", name: "coder · sửa CSS", detail: "Đổi 1 mã màu, chạy lại kiểm tra 6 cặp.", latencyMs: 18400 },
            { type: "thinking", name: "Xác nhận phạm vi", detail: "Đảm bảo không đổi màu nào khác.", latencyMs: 2100 },
            { type: "output", name: "Báo cáo sửa", detail: "Mã màu mới + kết quả đo lại.", latencyMs: 8890 }
          ]
        },
        {
          index: 3, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-14T08:52:03+07:00", latencyMs: 7200, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 580, cachedTokens: 220, completionTokens: 150, costUsd: 0.000132 }
          ],
          user: "Ghi chú lại việc này cho biên bản.",
          assistant: "Đã ghi: trang giá sửa 1 cặp màu nút phụ, người sửa coder, xong sáng 14 thg 10.",
          steps: [
            { type: "input", name: "Nhận yêu cầu ghi chú", detail: "Ghi ngắn cho biên bản họp.", latencyMs: 70 },
            { type: "output", name: "Dòng biên bản", detail: "1 câu, đủ việc - người - thời gian.", latencyMs: 7130 }
          ]
        }
      ]
    },
    {
      id: "s-release", title: "Soát release 1.4.2", kind: "group",
      bots: ["thyca", "coder"],
      turns: [
        {
          index: 1, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-12T16:20:36+07:00", latencyMs: 13400, requests: 1,
          botBreakdown: [
            { botId: "coder", requests: 1, promptTokens: 1890, cachedTokens: 720, completionTokens: 460, costUsd: 0.000456 }
          ],
          user: "Liệt kê các commit từ 1.4.1 tới nay.",
          assistant: "7 commit: 4 sửa lỗi, 2 tính năng nhỏ, 1 cập nhật văn bản. Không có thay đổi phá vỡ tương thích.",
          steps: [
            { type: "input", name: "Nhận yêu cầu liệt kê", detail: "Phạm vi: từ thẻ 1.4.1 tới HEAD.", latencyMs: 120 },
            { type: "tool", name: "coder · đọc git log", detail: "7 commit, đọc tóm tắt từng commit.", latencyMs: 8100 },
            { type: "output", name: "Danh sách commit", detail: "Nhóm theo loại: sửa lỗi, tính năng, văn bản.", latencyMs: 5180 }
          ]
        },
        {
          index: 2, model: "gpt-4o", status: "failed",
          error: "Lượt dừng: công cụ `bash` trả lỗi (exit 1) khi chạy bộ kiểm thử — 2 test đỏ ở phân trang nhật ký.",
          startedAt: "2026-10-12T16:41:58+07:00", latencyMs: 58700, requests: 3,
          botBreakdown: [
            { botId: "coder", requests: 2, promptTokens: 3540, cachedTokens: 1210, completionTokens: 620, costUsd: 0.00624 },
            { botId: "thyca", requests: 1, promptTokens: 920, cachedTokens: 280, completionTokens: 190, costUsd: 0.0024 }
          ],
          user: "Chạy toàn bộ kiểm thử trước khi đóng gói.",
          assistant: "",
          steps: [
            { type: "input", name: "Nhận lệnh chạy test", detail: "Toàn bộ suite, chặn release nếu đỏ.", latencyMs: 130 },
            { type: "tool", name: "coder · chạy pytest", detail: "212 test, 210 qua, 2 đỏ ở phân trang.", latencyMs: 44900 },
            { type: "tool", name: "coder · đọc log lỗi", detail: "Lỗi tràn trang khi nhật ký trống.", latencyMs: 8700, isError: true },
            { type: "thinking", name: "Đánh giá", detail: "Lỗi thật, không phải test giòn — dừng release.", latencyMs: 4970 }
          ]
        },
        {
          index: 3, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-12T17:15:22+07:00", latencyMs: 10200, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 760, cachedTokens: 310, completionTokens: 210, costUsd: 0.00018 }
          ],
          user: "Vậy release 1.4.2 quyết định sao?",
          assistant: "Hoãn release: chờ sửa 2 test đỏ phân trang. coder nhận sửa, hẹn chạy lại trước 10 giờ sáng mai.",
          steps: [
            { type: "input", name: "Nhận câu hỏi quyết định", detail: "Cần kết luận có/không release.", latencyMs: 90 },
            { type: "thinking", name: "Cân nhắc", detail: "2 test đỏ đúng vùng thay đổi — không thể bỏ qua.", latencyMs: 1800 },
            { type: "output", name: "Quyết định hoãn", detail: "Lý do + người nhận + hạn chạy lại.", latencyMs: 8310 }
          ]
        }
      ]
    },
    {
      id: "s-dm-thyca", title: "Hỏi nhanh về lịch họp", kind: "dm",
      bots: ["thyca"],
      turns: [
        {
          index: 1, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-17T07:26:40+07:00", latencyMs: 5400, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 620, cachedTokens: 280, completionTokens: 140, costUsd: 0.000126 }
          ],
          user: "Hôm nay có họp gì?",
          assistant: "9 giờ họp đội cuối tuần, 14 giờ duyệt landing với designer. Hết.",
          steps: [
            { type: "input", name: "Nhận câu hỏi lịch", detail: "Hỏi lịch ngày 17 thg 10.", latencyMs: 60 },
            { type: "tool", name: "thyca · xem lịch", detail: "2 sự kiện hôm nay.", latencyMs: 2100 },
            { type: "output", name: "Trả lời lịch", detail: "2 dòng, giờ + tên cuộc họp.", latencyMs: 3240 }
          ]
        },
        {
          index: 2, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-17T07:28:15+07:00", latencyMs: 4800, requests: 1,
          botBreakdown: [
            { botId: "thyca", requests: 1, promptTokens: 540, cachedTokens: 260, completionTokens: 120, costUsd: 0.000108 }
          ],
          user: "Nhắc anh trước họp 15 phút nhé.",
          assistant: "Đã hẹn nhắc lúc 8 giờ 45. Anh cứ làm việc, tới giờ em gọi.",
          steps: [
            { type: "input", name: "Nhận yêu cầu nhắc", detail: "Nhắc trước họp 9 giờ 15 phút.", latencyMs: 60 },
            { type: "tool", name: "thyca · đặt nhắc", detail: "Hẹn 8 giờ 45 cùng ngày.", latencyMs: 1800 },
            { type: "output", name: "Xác nhận", detail: "1 câu xác nhận giờ nhắc.", latencyMs: 2940 }
          ]
        }
      ]
    },
    {
      id: "s-dm-coder", title: "Sửa lỗi đăng nhập", kind: "dm",
      bots: ["coder"],
      turns: [
        {
          index: 1, model: "gpt-4o", status: "completed",
          startedAt: "2026-10-10T10:03:51+07:00", latencyMs: 34200, requests: 2,
          botBreakdown: [
            { botId: "coder", requests: 2, promptTokens: 2980, cachedTokens: 1040, completionTokens: 890, costUsd: 0.00756 }
          ],
          user: "Người dùng báo không đăng nhập được bằng email công ty.",
          assistant: "Tìm ra rồi: regex kiểm tra email chặn tên miền có dấu gạch ngang. Đã sửa và thêm 3 test mới.",
          steps: [
            { type: "input", name: "Nhận báo lỗi", detail: "Lỗi đăng nhập email công ty, có ví dụ.", latencyMs: 140 },
            { type: "tool", name: "coder · tái hiện lỗi", detail: "Chạy thử với 5 địa chỉ mẫu, 2 lỗi đúng như báo.", latencyMs: 16900 },
            { type: "tool", name: "coder · sửa regex", detail: "Nới regex + thêm 3 test biên.", latencyMs: 12100 },
            { type: "output", name: "Báo cáo sửa", detail: "Nguyên nhân + bản sửa + test mới.", latencyMs: 5060 }
          ]
        },
        {
          index: 2, model: "claude-opus-4-1", status: "completed",
          startedAt: "2026-10-10T10:29:16+07:00", latencyMs: 22800, requests: 1,
          botBreakdown: [
            { botId: "coder", requests: 1, promptTokens: 2140, cachedTokens: 880, completionTokens: 640, costUsd: null }
          ],
          user: "Soát lại toàn bộ chỗ dùng regex email đó.",
          assistant: "Đã soát 11 chỗ: 9 chỗ dùng hàm chung đã sửa, 2 chỗ chép tay cần thay. Liệt kê đủ 2 chỗ bên dưới.",
          steps: [
            { type: "input", name: "Nhận yêu cầu soát", detail: "Soát mọi chỗ dùng regex email.", latencyMs: 120 },
            { type: "tool", name: "coder · tìm kiếm", detail: "Quét mã nguồn, thấy 11 vị trí.", latencyMs: 13400 },
            { type: "output", name: "Kết quả soát", detail: "9 chỗ ổn, 2 chỗ cần thay, có đường dẫn dòng.", latencyMs: 9280 }
          ]
        },
        {
          index: 3, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-10T10:55:02+07:00", latencyMs: 6900, requests: 1,
          botBreakdown: [
            { botId: "coder", requests: 1, promptTokens: 710, cachedTokens: 330, completionTokens: 190, costUsd: 0.000162 }
          ],
          user: "Thay luôn 2 chỗ chép tay đi.",
          assistant: "Đã thay cả 2 chỗ sang hàm chung, chạy lại 3 test mới đều qua.",
          steps: [
            { type: "input", name: "Nhận lệnh thay", detail: "Thay 2 chỗ chép tay.", latencyMs: 70 },
            { type: "tool", name: "coder · thay mã", detail: "Sửa 2 tệp, chạy test xác nhận.", latencyMs: 4300 },
            { type: "output", name: "Xác nhận xong", detail: "1 câu + kết quả test.", latencyMs: 2530 }
          ]
        }
      ]
    },
    {
      id: "s-dm-writer", title: "Bài đăng fanpage", kind: "dm",
      bots: ["writer"],
      turns: [
        {
          index: 1, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-08T20:12:44+07:00", latencyMs: 9800, requests: 1,
          botBreakdown: [
            { botId: "writer", requests: 1, promptTokens: 880, cachedTokens: 300, completionTokens: 420, costUsd: 0.000318 }
          ],
          user: "Viết tus ra mắt tính năng nhắc họp, vui, dưới 100 chữ.",
          assistant: "Họp 9 giờ mà 8 giờ 59 mới nhớ? Từ nay Thyca nhắc trước 15 phút, kèm cả chương trình họp. Bật thử nhé!",
          steps: [
            { type: "input", name: "Nhận đề bài", detail: "Tus ra mắt, vui, <100 chữ.", latencyMs: 80 },
            { type: "thinking", name: "Chọn câu móc", detail: "Mở bằng tình huống ai cũng gặp.", latencyMs: 1500 },
            { type: "output", name: "Bản tus", detail: "68 chữ, có câu kêu gọi ở cuối.", latencyMs: 8220 }
          ]
        },
        {
          index: 2, model: "gpt-4o-mini", status: "completed",
          startedAt: "2026-10-08T20:24:09+07:00", latencyMs: 6100, requests: 1,
          botBreakdown: [
            { botId: "writer", requests: 1, promptTokens: 640, cachedTokens: 290, completionTokens: 180, costUsd: 0.00015 }
          ],
          user: "Thêm bản ngắn 30 chữ để chạy quảng cáo.",
          assistant: "Thyca nhắc họp trước 15 phút — không lo lỡ giờ, không lo quên việc.",
          steps: [
            { type: "input", name: "Nhận yêu cầu bản ngắn", detail: "30 chữ cho quảng cáo.", latencyMs: 60 },
            { type: "output", name: "Bản 30 chữ", detail: "Đếm đúng 27 chữ.", latencyMs: 6040 }
          ]
        }
      ]
    }
  ];

  /* Derive turn totals from breakdowns so sums can never drift. */
  function __protoFinalize() {
    function round6(n) { return Math.round(n * 1e6) / 1e6; }
    window.PROTO_SESSIONS.forEach(function (s) {
      var minStart = null;
      s.turns.forEach(function (t) {
        var p = 0, c = 0, o = 0, r = 0, cost = 0, priced = true;
        t.botBreakdown.forEach(function (b) {
          p += b.promptTokens; c += b.cachedTokens; o += b.completionTokens; r += b.requests;
          if (typeof b.costUsd === "number") { cost += b.costUsd; } else { priced = false; }
        });
        t.promptTokens = p; t.cachedTokens = c; t.completionTokens = o;
        t.totalTokens = p + o; t.requests = r;
        t.costUsd = priced ? round6(cost) : null;
        t.endedAt = new Date(new Date(t.startedAt).getTime() + t.latencyMs).toISOString();
        if (minStart === null || t.startedAt < minStart) { minStart = t.startedAt; }
      });
      s.startedAt = minStart;
      s.turns.sort(function (a, b) { return a.index - b.index; });
    });
  }
  __protoFinalize();
})();
