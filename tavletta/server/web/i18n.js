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

// The locale numbers and times are written in: the page's language (its
// catalog: "en", "de", "pt_BR"), with the region of the first of the
// browser's languages (navigator.languages) that is the same language
// ("en-GB" for "en"), else the catalog's own region. German always Swiss
// (2'686), as the app writes it. Browsers do not tell the system's regional
// formats, only this list.
export function localeFor(code, browser = []) {
  if (code === "de") return "de-CH";
  const tag = code.replace("_", "-");
  if (tag.includes("-")) return tag;
  const same = browser.find((b) => typeof b === "string" && b.toLowerCase().split("-")[0] === tag);
  try {
    return same ? Intl.getCanonicalLocales(same)[0] : tag;
  } catch {
    return tag; // not a valid tag
  }
}

function browserLanguages() {
  return globalThis.navigator?.languages ?? [];
}

export function locale() {
  return localeFor(lang, browserLanguages());
}

export function num(value) {
  return new Intl.NumberFormat(locale()).format(value);
}

export function mm(value) {
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(value);
}

// A time of day on a 24-hour clock in every language ("15:49", never
// "03:49 PM"): the browser tells neither the system's clock setting nor its
// region (an English browser says en-US wherever it is), and 15:49 cannot
// be misread anywhere. Digits and separators follow the locale.
export function formatTime(iso, tag) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(tag, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

export function time(iso) {
  return formatTime(iso, locale());
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
