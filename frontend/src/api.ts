export type ApiResult = Record<string, any>;

let sessionToken = "";

export function setSessionToken(value: string) {
  sessionToken = value;
}

async function request(path: string, init: RequestInit = {}): Promise<ApiResult> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (init.method && init.method !== "GET") headers.set("X-Youbu-Token", sessionToken);
  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

export const getBootstrap = () => request("/api/bootstrap");
export const getSignals = (workspaceId: string, maxPoints = 12000) =>
  request("/api/workspaces/" + workspaceId + "/signals?max_points=" + maxPoints);
export const openWorkspace = (trialRecordId: string, recoverDraft = true) =>
  request("/api/workspaces", {
    method: "POST",
    body: JSON.stringify({ trial_record_id: trialRecordId, recover_draft: recoverDraft }),
  });
export const closeWorkspace = (workspaceId: string) =>
  request("/api/workspaces/" + workspaceId, { method: "DELETE" });
export const command = (workspaceId: string, action: string, payload: ApiResult = {}) =>
  request("/api/workspaces/" + workspaceId + "/commands", {
    method: "POST",
    body: JSON.stringify({ action, payload }),
  });
export const recognize = (workspaceId: string) =>
  request("/api/workspaces/" + workspaceId + "/recognize", { method: "POST" });
export const commit = (workspaceId: string) =>
  request("/api/workspaces/" + workspaceId + "/commit", { method: "POST" });
export const registerTrial = (value: ApiResult) =>
  request("/api/trials", { method: "POST", body: JSON.stringify(value) });
export const updateSchema = (schemaValue: ApiResult, expectedHash: string) =>
  request("/api/schema/draft", {
    method: "PUT",
    body: JSON.stringify({ schema_value: schemaValue, expected_hash: expectedHash }),
  });
export const publishSchema = (expectedHash: string) =>
  request("/api/schema/publish", {
    method: "POST",
    body: JSON.stringify({ expected_hash: expectedHash }),
  });
