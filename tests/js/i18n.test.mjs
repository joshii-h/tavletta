// Times and numbers of the web page (tavletta/server/web/i18n.js): a
// 24-hour clock in every language, numbers in the page language's locale.
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatTime, localeFor } from "../../tavletta/server/web/i18n.js";

// 15:49 local time, whatever the time zone the tests run in
const AFTERNOON = new Date(2026, 10, 24, 15, 49).toISOString();
const MORNING = new Date(2026, 10, 24, 3, 7).toISOString();
const MIDNIGHT = new Date(2026, 10, 24, 0, 5).toISOString();

test("Swiss German gives a 24-hour clock", () => {
  assert.equal(formatTime(AFTERNOON, localeFor("de", ["de-CH", "de"])), "15:49");
});

test("English gives a 24-hour clock too, also when the browser says en-US", () => {
  assert.equal(formatTime(AFTERNOON, localeFor("en", [])), "15:49");
  assert.equal(formatTime(AFTERNOON, localeFor("en", ["en-US", "en", "de-CH"])), "15:49");
  assert.equal(formatTime(AFTERNOON, "en-US"), "15:49");
  assert.equal(formatTime(MORNING, "en"), "03:07");
  assert.equal(formatTime(MIDNIGHT, "en-US"), "00:05");
});

test("no AM or PM in any language of the app", () => {
  const languages = ["ar", "de", "es", "et", "fi", "fr", "hi", "hu", "id", "it", "ja", "ko", "ms", "nl", "pl", "pt", "pt_BR", "ru", "sv", "th", "tr", "uk", "vi", "zh_CN", "zh_HK", "zh_TW"];
  for (const code of ["en", ...languages]) {
    const text = formatTime(AFTERNOON, localeFor(code, []));
    const digits = text.replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x660));
    assert.match(digits, /15\D49/, `${code}: ${text}`);
    assert.doesNotMatch(text, /AM|PM|午|오|ص|م|अ/i, `${code}: ${text}`);
  }
});

test("no time for nothing or for what is not a time", () => {
  assert.equal(formatTime("", "en"), "");
  assert.equal(formatTime(null, "en"), "");
  assert.equal(formatTime("not a time", "en"), "");
});

test("German numbers are Swiss, as the app writes them", () => {
  assert.equal(localeFor("de", ["de-DE"]), "de-CH");
  assert.match(new Intl.NumberFormat(localeFor("de", [])).format(2686), /^2['’]686$/);
});

test("the browser's region of the page's language, else the catalog's", () => {
  assert.equal(localeFor("en", ["en-GB", "en"]), "en-GB");
  assert.equal(localeFor("en", ["de-CH", "en-US"]), "en-US");
  assert.equal(localeFor("fr", ["fr-ch"]), "fr-CH");
  assert.equal(localeFor("en", ["de-CH"]), "en");
  assert.equal(localeFor("pt_BR", ["pt-PT"]), "pt-BR");
  assert.equal(localeFor("zh_TW", []), "zh-TW");
  assert.equal(localeFor("en", ["en-x-%%"]), "en");
});
