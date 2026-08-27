export const icons = {
  chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14v8H8l-3 3V7Z" /><path d="M9 11h6M9 14h4" /></svg>',
  memories: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6.6C10.3 5.1 8 4.6 4.5 4.6v13.8c3.5 0 5.8.5 7.5 2 1.7-1.5 4-2 7.5-2V4.6c-3.5 0-5.8.5-7.5 2Z" /><path d="M12 6.6v13.8" /></svg>',
  trace: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2" /><circle cx="12" cy="7" r="2" /><circle cx="18" cy="12" r="2" /><circle cx="12" cy="17" r="2" /><path d="M8 11.2 10.2 8.4M13.8 8.4 16 11.2M16 12.8 13.8 15.6M10.2 15.6 8 12.8" /></svg>'
};

export const personaPages = [
  {
    title: "SOUL.md",
    date: "Inject cả file",
    tag: "",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>SOUL.md</h2><p>Hồ sơ ổn định của trợ lý. Nhét cả file mỗi lượt để prefix system ít đổi.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div><blockquote class="quote-note"><p>Thyca là harness trợ lý cá nhân. Không phải coding agent.</p><cite>SOUL.md</cite></blockquote></div>`
  },
  {
    title: "USER.md",
    date: "Inject cả file",
    tag: "",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>USER.md</h2><p>Hồ sơ người dùng. Cùng rule với SOUL: cả file, không cắt.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div></div>`
  },
  {
    title: "IDENTITY.md",
    date: "Inject cả file",
    tag: "",
    tone: "memories",
    body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>IDENTITY.md</h2><p>Danh tính trợ lý. Cùng rule với SOUL: cả file, không cắt, không vào inventory leaf.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div></div>`
  }
];

export const modes = {
  chat: {
    label: "Chat",
    listLabel: "Phiên gần đây",
    kicker: "ses_7f3a · gpt-4o-mini",
    note: "",
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
        <div class="tool-strip"><span class="tool-kicker">Tools used:</span> memory_remember</div>
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
        <div class="tool-strip"><span class="tool-kicker">Tools used:</span> memory_search</div>
      </div>`
  },
  memories: {
    label: "Memories",
    listLabel: "File canonical",
    kicker: "~/.thyca · markdown là nguồn sự thật",
    note: "",
    chips: [],
    pages: [
      {
        title: "Tổng quan",
        hideTitle: true,
        date: "leaf",
        tag: "",
        tone: "memories",
        kicker: "leaf · get và search",
        body: `<div class="book-reading"><div class="stat-row"><div><strong>0</strong><span>tổng</span></div></div><div class="suggest-inline"><h3>Theo ngày</h3><p class="suggest-empty">Chưa có daily.</p></div></div>`
      },
      {
        title: "SOUL.md",
        date: "Inject cả file",
        tag: "",
        tone: "memories",
        body: `<div class="book-reading"><div class="book-meta"><span class="book-author">Canonical · ActiveMemory</span><h2>SOUL.md</h2><p>Hồ sơ ổn định của trợ lý. Nhét cả file mỗi lượt để prefix system ít đổi.</p><div class="progress-label"><span>Inject</span><strong>full</strong></div><div class="reading-progress reading-progress-complete"><span></span></div></div><blockquote class="quote-note"><p>Thyca là harness trợ lý cá nhân. Không phải coding agent.</p><cite>SOUL.md</cite></blockquote></div>`
      },
      {
        title: "USER.md",
        date: "Inject cả file",
        tag: "",
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
    listLabel: "Phiên gần đây",
    kicker: "Trace · AgentLoop",
    note: "",
    chips: [],
    pages: [
      {
        title: "Tổng quan",
        hideTitle: true,
        date: "mock",
        tag: "",
        tone: "trace",
        kicker: "Trace · AgentLoop",
        body: `<div class="music-page"><div class="album-note"><span class="track-kicker">Trace · AgentLoop</span><h2>Tổng quan</h2><p>model · cache · in/out là derived. Mở bằng server để xem số thật từ JSONL.</p><div class="track-rule"><span style="width:100%"></span></div><div class="track-time"><span>assemble</span><span>observe</span></div></div></div>`
      },
      {
        title: "Ghi daily buổi tối",
        date: "20:41 26 thg 8",
        tag: "muse-spark",
        tone: "trace",
        body: `<div class="music-page"><div class="album-note"><span class="track-kicker">lượt 1 · ok</span><h2>Ghi daily buổi tối</h2><p>input 1 240 → output 318 · $0,0075 · 1,4 s</p><div class="track-rule"><span style="width:100%"></span></div><div class="track-time"><span>AgentLoop</span><span>1,4 s</span></div></div><ol class="phase-list trace-timeline"><li class="is-done"><span class="phase-name">think #1</span><div class="track-rule"><span style="width:48%"></span></div></li><li class="is-done"><span class="phase-name">act</span><div class="track-rule"><span style="width:14%"></span></div></li><li class="is-done"><span class="phase-name">observe</span><div class="track-rule"><span style="width:0%"></span></div></li></ol><div class="music-note"><p>Model · cache · in/out là derived — đọc từ meta, không đếm tay.</p></div></div>`
      }
    ],
    body: `
      <div class="music-page">
        <div class="album-note">
          <span class="track-kicker">Trace · AgentLoop</span>
          <h2>Tổng quan</h2>
          <p>model · cache · in/out là derived. Mở bằng server để xem số thật từ JSONL.</p>
          <div class="track-rule"><span></span></div>
          <div class="track-time"><span>assemble</span><span>observe</span></div>
        </div>
      </div>`
  }
};

