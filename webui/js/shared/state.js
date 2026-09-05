export const state = {
  activeMode: "chat",
  activePageIndex: 0,
  pageOrderNewest: true,
  listPage: 0,
  activeSessionId: null,
  chatLive: false,
  // Snapshot provider chat đang dùng — settings so sánh để reset
  // về phiên mới khi model/baseUrl đổi (TASK-041).
  lastChatModel: "",
  lastChatBaseUrl: "",
};
