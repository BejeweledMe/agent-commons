const typed = "[0-9A-HJKMNP-TV-Z]{26}";
const thread = `thread\\.${typed}`, message = `message\\.${typed}`;
const draft = "draft\\.[0-9a-f]{32}", attachment = "attachment\\.[0-9a-f]{32}";
export function conversationPath(path: string, method: "GET" | "POST", rawFile: File | null = null): string {
  if (!path.startsWith("/") || path.startsWith("//") || /[\\#\u0000-\u001f]/.test(path)) throw Error("conversation_path");
  const url = new URL(path, "http://conversation.invalid");
  if (url.origin !== "http://conversation.invalid" || url.pathname !== path.split("?")[0] || decodeURIComponent(url.pathname) !== url.pathname || url.pathname.includes("..")) throw Error("conversation_path");
  const pairs = [...url.searchParams], keys = new Set(pairs.map(([key]) => key));
  if (keys.size !== pairs.length) throw Error("conversation_query");
  const pathname = url.pathname;
  if (method === "GET") {
    if (rawFile) throw Error("conversation_method");
    if (pathname === "/conversations") {
      if (pairs.some(([key]) => key !== "scope_kind" && key !== "scope_id")) throw Error("conversation_query");
      const kind = url.searchParams.get("scope_kind") ?? "project", id = url.searchParams.get("scope_id");
      if (kind === "project" ? id !== null : !["task", "agent"].includes(kind) || !new RegExp(`^${kind}\\.${typed}$`).test(id ?? "")) throw Error("conversation_scope");
    } else if (new RegExp(`^/conversations/${thread}/messages$`).test(pathname)) {
      if (pairs.length > 1 || pairs.some(([key, value]) => key === "tail" ? value !== "true" : !["after", "before"].includes(key) || !new RegExp(`^${message}$`).test(value))) throw Error("conversation_query");
    } else if (!new RegExp(`^/conversations/${thread}/drafts/${draft}$`).test(pathname) || pairs.length) throw Error("conversation_path");
  } else if (new RegExp(`^/conversations/${thread}/drafts/${draft}/attachments$`).test(pathname)) {
    if (!rawFile || pairs.length !== 1 || url.searchParams.get("filename") !== rawFile.name) throw Error("conversation_upload");
  } else if (rawFile || pairs.length || !(pathname === "/conversations" || new RegExp(`^/conversations/${thread}/(?:messages|drafts)$`).test(pathname) || new RegExp(`^/conversations/${thread}/drafts/${draft}/attachments/${attachment}/remove$`).test(pathname))) throw Error("conversation_path");
  return path;
}
export function attachmentPath(threadId: string, messageId: string, attachmentId: string): string {
  if (!new RegExp(`^${thread}$`).test(threadId) || !new RegExp(`^${message}$`).test(messageId) || !new RegExp(`^${attachment}$`).test(attachmentId)) throw Error("conversation_attachment");
  return `/conversations/${threadId}/messages/${messageId}/attachments/${attachmentId}`;
}
export async function boundedAttachmentBlob(response: Response): Promise<Blob> {
  const type = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() ?? "";
  if (!["image/png", "image/jpeg", "application/octet-stream"].includes(type)) throw Error("conversation_attachment_type");
  const length = response.headers.get("Content-Length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > 15_000_000)) throw Error("conversation_attachment_size");
  if (!response.body) throw Error("conversation_attachment_body");
  const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = [];
  let total = 0;
  try {
    while (true) { const { value, done } = await reader.read(); if (done) break; total += value.byteLength; if (total > 15_000_000) throw Error("conversation_attachment_size"); chunks.push(new Uint8Array(value)); }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  if (total < 1 || (length !== null && total !== Number(length))) throw Error("conversation_attachment_size");
  return new Blob(chunks, { type });
}
