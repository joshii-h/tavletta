// The page's texts from the app's gettext catalogs (/ui/messages.json):
// the function t takes an English text and its values, {name} in the text,
// and gives the translation with the values filled in. Only a plain string
// as its first argument is found by xgettext and the server
// (tests/test_server_webui.py checks it).

let messages = {};
export let lang = "en";
export let languages = ["en"];
export let dir = "ltr";
const STORE = "tavletta.lang"; // the language chosen here, for this browser only

export function t(text, params) {
  const found = messages[text] ?? text;
  if (!params) return found;
  return found.replace(/\{(\w+)\}/g, (all, key) => (key in params ? String(params[key]) : all));
}

export function locale() {
  // Swiss numbers for German (2'686), as the app writes them
  return lang === "de" ? "de-CH" : lang.replace("_", "-");
}

export function num(value) {
  return new Intl.NumberFormat(locale()).format(value);
}

export function mm(value) {
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(value);
}

export function time(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
}

export function languageName(code) {
  try {
    const tag = code.replace("_", "-");
    return new Intl.DisplayNames([tag], { type: "language" }).of(tag) ?? code;
  } catch {
    return code;
  }
}

function stored() {
  try {
    return localStorage.getItem(STORE);
  } catch {
    return null;
  }
}

export function chooseLanguage(code) {
  try {
    localStorage.setItem(STORE, code);
  } catch {
    // private window: chosen for this visit only
  }
  return loadMessages(code);
}

export async function loadMessages(asked = stored()) {
  const query = asked ? `?lang=${encodeURIComponent(asked)}` : "";
  try {
    const response = await fetch(`/ui/messages.json${query}`, { credentials: "omit" });
    if (response.ok) {
      const body = await response.json();
      messages = body.messages ?? {};
      lang = body.language ?? "en";
      languages = body.languages ?? ["en"];
      dir = body.dir === "rtl" ? "rtl" : "ltr";
    }
  } catch {
    // offline: English, the texts themselves
  }
  document.documentElement.lang = lang.replace("_", "-");
  document.documentElement.dir = dir; // the server's, from the app's list of right-to-left languages
}
