export const icons = {
  chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14v8H8l-3 3V7Z" /><path d="M9 11h6M9 14h4" /></svg>',
  memories: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5h7.5L19 9v10.5H7zM14.5 4.5V9H19" /><path d="M9.5 13h7M9.5 16.5h5" /></svg>',
  trace: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2" /><circle cx="12" cy="7" r="2" /><circle cx="18" cy="12" r="2" /><circle cx="12" cy="17" r="2" /><path d="M8 11.2 10.2 8.4M13.8 8.4 16 11.2M16 12.8 13.8 15.6M10.2 15.6 8 12.8" /></svg>'
};

export const personaPages = [
  {
    title: "SOUL.md",
    date: "Inject cả file",
    tag: "canonical",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>SOUL.md</h2><p>Hồ sơ ổn định của trợ lý. Nhét cả file mỗi lượt để prefix system ít đổi.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div><blockquote class="quote-note"><p>Thyca là harness trợ lý cá nhân. Không phải coding agent.</p><cite>SOUL.md</cite></blockquote></div>`
  },
  {
    title: "USER.md",
    date: "Inject cả file",
    tag: "canonical",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>USER.md</h2><p>Hồ sơ người dùng. Cùng rule với SOUL: cả file, không cắt.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div></div>`
  },
  {
    title: "IDENTITY.md",
    date: "Inject cả file",
    tag: "canonical",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>IDENTITY.md</h2><p>Danh tính trợ lý. Cùng rule với SOUL: cả file, không cắt, không vào inventory leaf.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div></div>`
  }
];

export const modes = {
  chat: {
    label: "Chat",
    listLabel: "Phiên gần đây",
    kicker: "ses_7f3a · gpt-4o-mini",
    note: "Thyca trả lời như trợ lý. Năng lực đến từ tool, không từ khung chat.",
    chips: ["Nhớ điều này", "Tìm trong memory", "Giải thích lượt vừa rồi"],
    pages: [
      {
        title: "Linux là target",
        date: "Hôm nay · 14:06",
        tag: "ses_7f3a",
        tone: "chat",
        active: true
      },
      {
        title: "Cấu hình provider",
        date: "Hôm qua · 21:12",
        tag: "ses_2c91",
        tone: "chat",
        body: `<div class="entry-list"><article class="entry entry-user"><div class="entry-copy"><p>API key lấy từ đâu? Đừng bảo commit vào repo.</p></div></article><article class="entry entry-thyca"><time>thyca</time><div class="entry-copy"><p><code>provider.apiKey</code> trong <code>~/.thyca/config.json</code> thắng nếu có. Trống thì đọc <code>apiKeyEnv</code>.</p><p class="soft-line">Không hiện key trong <code>repr</code>. Không commit <code>~/.thyca</code>.</p></div></article></div>`
      },
      {
        title: "Loop max",
        date: "19 tháng 8",
        tag: "ses_9ab0",
        tone: "chat",
        body: `<div class="entry-list"><article class="entry entry-user"><div class="entry-copy"><p>Sau 10 vòng tool thì sao?</p></div></article><article class="entry entry-thyca"><time>thyca</time><div class="entry-copy"><p><code>Observe.loop_limit</code> append “loop limit reached” rồi dừng. Không planner, không subagent.</p></div></article></div>`
      }
    ],
    body: `
      <div class="entry-list">
        <article class="entry entry-user">
          <div class="entry-copy"><p>Linux là target chính. Đừng viết API chỉ chạy trên Windows.</p></div>
        </article>
        <article class="entry entry-thyca">
          <time>thyca</time>
          <div class="entry-copy">
            <p>Đã ghi vào daily hôm nay. Lượt sau <code>ActiveMemory.refresh</code> sẽ thấy.</p>
            <p class="soft-line">Writer duy nhất cho memory files là <code>memory_remember</code>. Không đụng session JSONL.</p>
          </div>
        </article>
        <div class="tool-strip"><span class="tool-pill">memory_remember</span><span>daily</span></div>
        <article class="entry entry-user">
          <div class="entry-copy"><p>Tìm lại: L2 hybrid lấy gì trước?</p></div>
        </article>
        <article class="entry entry-thyca">
          <time>thyca</time>
          <div class="entry-copy">
            <p>Lexical trước: FTS5 + trigram. Semantic/RRF chỉ khi agent gọi lại với <code>semantic=true</code>.</p>
            <p class="soft-line">Embedding runtime đã gỡ. Đừng reintroduce như đã implement.</p>
          </div>
        </article>
        <div class="tool-strip"><span class="tool-pill">memory_search</span><span>lexical</span></div>
      </div>`
  },
  memories: {
    label: "Memories",
    listLabel: "File canonical",
    kicker: "~/.thyca · markdown là nguồn sự thật",
    note: "Get = đọc đủ. Search = đã hiện trong kết quả. Hot không đếm.",
    chips: [],
    pages: [
      {
        title: "Tổng quan",
        date: "leaf",
        tag: "stats",
        tone: "memories",
        kicker: "leaf · get và search",
        body: `<div class="book-reading"><div class="stat-row"><div><strong>0</strong><span>tổng</span></div></div><div class="suggest-inline"><h3>Theo ngày</h3><p class="suggest-empty">Chưa có daily.</p></div></div>`
      },
      {
        title: "SOUL.md",
        date: "Inject cả file",
        tag: "canonical",
        tone: "memories",
        body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>SOUL.md</h2><p>Hồ sơ ổn định của trợ lý. Nhét cả file mỗi lượt để prefix system ít đổi.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div><blockquote class="quote-note"><p>Thyca là harness trợ lý cá nhân. Không phải coding agent.</p><cite>SOUL.md</cite></blockquote></div>`
      },
      {
        title: "USER.md",
        date: "Inject cả file",
        tag: "canonical",
        tone: "memories",
        body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>USER.md</h2><p>Hồ sơ người dùng. Cùng rule với SOUL: cả file, không cắt.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div></div>`
      }
    ],
    body: `
      <div class="book-reading">
        <div class="book-meta">
          <span class="book-author">Canonical · prompt</span>
          <h2>SOUL.md</h2>
          <p>Hồ sơ ổn định của trợ lý. Nhét cả file mỗi lượt.</p>
        </div>
        <blockquote class="quote-note">
          <p>Linux là target chính. Đừng viết API chỉ chạy trên Windows.</p>
          <cite>SOUL.md</cite>
        </blockquote>
      </div>`
  },
  trace: {
    label: "Trace",
    listLabel: "Lượt gần đây",
    kicker: "AgentLoop · round 2 / 10",
    note: "assemble → think → act → observe. Một Stage, không planner.",
    chips: [],
    pages: [
      {
        title: "think → act",
        date: "ses_7f3a · 14:06",
        tag: "round 2",
        tone: "trace",
        active: true
      },
      {
        title: "text, dừng",
        date: "ses_7f3a · 14:06",
        tag: "round 3",
        tone: "trace",
        body: `<div class="music-page"><div class="album-note"><span class="track-kicker">Observe.assistant</span><h2>text, dừng</h2><p>Không còn tool_calls. In text, append session JSONL.</p><div class="track-rule"><span></span></div><div class="track-time"><span>think</span><span>done</span></div></div><ol class="phase-list"><li class="is-done">assemble</li><li class="is-done">think</li><li>act</li><li class="is-current">observe</li></ol><div class="music-note"><p>Hết vòng tool. Session lưu. Không compact vì còn dưới ngưỡng.</p></div><button class="player-button" id="player-button" type="button" aria-pressed="false"><span class="player-symbol" aria-hidden="true">▶</span><span id="player-label">Phát lại lượt</span></button></div>`
      }
    ],
    body: `
      <div class="music-page">
        <div class="album-note">
          <span class="track-kicker">Act.act · gather</span>
          <h2>think → act</h2>
          <p><code>memory_search</code> lexical, không fallback semantic.</p>
          <div class="track-rule"><span></span></div>
          <div class="track-time"><span>assemble</span><span>act</span></div>
        </div>
        <ol class="phase-list">
          <li class="is-done">assemble</li>
          <li class="is-done">think</li>
          <li class="is-current">act</li>
          <li>observe</li>
        </ol>
        <div class="music-note">
          <p>Tool chạy thẳng, không cửa xác nhận. Kết quả giữ đúng thứ tự <code>tool_call_id</code>.</p>
        </div>
        <button class="player-button" id="player-button" type="button" aria-pressed="false"><span class="player-symbol" aria-hidden="true">▶</span><span id="player-label">Phát lại lượt</span></button>
      </div>`
  }
};

