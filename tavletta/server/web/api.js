// The REST API, with the token of this tab: kept in sessionStorage only
// (gone with the tab, never in localStorage, a cookie or the address).
// Every request sends X-Tavletta-Request, which a page of another site
// cannot send without asking first (the server answers no such question).

import { lang, t } from "./i18n.js";

const KEY = "tavletta.token";

export const token = {
  get() {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  set(value) {
    try {
      sessionStorage.setItem(KEY, value);
    } catch {
      // storage blocked: the token lasts until the page is left
    }
    memory = value;
  },
  clear() {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      // nothing kept
    }
    memory = null;
  },
};
let memory = null;

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// Called with every 401: the page asks for a token again.
let onUnauthorized = () => {};
export function whenUnauthorized(fn) {
  onUnauthorized = fn;
}

// method, path under /api/v1, a body (sent as JSON; a string as it is)
export async function api(method, path, body, { signal, binary = false } = {}) {
  const headers = { "X-Tavletta-Request": "1", "Accept-Language": lang.replace("_", "-") };
  const current = token.get() ?? memory;
  if (current) headers.Authorization = `Bearer ${current}`;
  const init = { method, headers, signal, credentials: "omit", cache: "no-store", redirect: "error" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api/v1${path}`, init);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "offline", t("No connection to the server. Your input is kept."));
  }
  if (response.status === 204) return null;
  if (response.ok && binary) return response.blob();
  let data = null;
  try {
    data = await response.json();
  } catch {
    // no JSON: a proxy's error page
  }
  if (!response.ok) {
    const problem = data?.error ?? {};
    const error = new ApiError(response.status, problem.code ?? "http_" + response.status, problem.message ?? response.statusText);
    if (response.status === 401) onUnauthorized(error);
    throw error;
  }
  return data;
}

// a path segment: a label name may contain slashes for subfolders
export function labelPath(name) {
  return name.split("/").map(encodeURIComponent).join("/");
}
