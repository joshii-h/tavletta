// A template's data fields and values for them where no data give any: the
// previews of the template cards and of the fields not assigned yet in a
// series show sample values, never the placeholders themselves ("{Name}"
// is longer than most values and runs off the label). Number-like fields
// ("Inventory number", "Asset ID", "Nr.") get digits, codes that take only
// digits as many as they need, the others the field's name, shortened.
// Without the DOM: tests/js/samples.test.mjs tests it with Node.

// The fields of a label and the elements each fills: a column {Name}, a
// variable {=name}, or the element's name (indexes from 0, as the geometry).
export function elementsOf(label, kind, name) {
  return label.elements
    .filter((e) => {
      const content = `${e.text ?? ""}${e.data ?? ""}`;
      if (kind === "column") return content.includes(`{${name}}`) || content.includes(`{${name}:`);
      if (kind === "variable") return new RegExp(`\\{=${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[}:]`).test(content);
      return e.name === name;
    })
    .map((e) => e.index - 1); // the API counts from 1, the geometry from 0
}

// codes that take only digits, and how many without the check digit
// (tavletta/codes.py DIGITS)
const DIGIT_CODES = { ean13: 12, ean8: 7, upca: 11, upce: 7, itf14: 13 };
// codes that take ASCII only (codes.py CHARSETS); Code 39 capitals too
const ASCII_CODES = new Set(["code128", "code93", "code39", "gs1_128", "gs1_datamatrix"]);
const NUMBER_WORDS = new Set([
  "no", "nr", "nb", "num", "id", "nummer", "number", "numero", "número", "numéro",
  "counter", "count", "zähler", "code", "serial", "sn", "sku", "ean", "gtin", "isbn", "barcode",
]);
const NUMBER_ENDINGS = ["nummer", "number", "numero", "número", "numéro", "nr"];
const DIGITS = "1234567890123"; // as many as the longest code needs
const NUMBER_LENGTH = 6;
const NAME_LENGTH = 12;
export const SCALES = [1, 0.5, 0.25]; // the samples' length, shorter while they do not fit

// "Inventory number", "Inventarnummer", "AssetID", "Nr." are number-like
export function numberLike(name) {
  const words = String(name)
    .replace(/(\p{Ll})(\p{Lu})/gu, "$1 $2")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
  return words.some((w) => NUMBER_WORDS.has(w) || NUMBER_ENDINGS.some((end) => w.length > end.length && w.endsWith(end)));
}

function codesOf(label, column) {
  return label.elements.filter((e) => e.kind === "code" && typeof e.data === "string" && e.data.includes(`{${column}`));
}

// One sample value for a column; scale (from SCALES) shortens it.
export function sampleValue(label, column, scale = 1) {
  const codes = codesOf(label, column);
  const digitCode = codes.find((e) => e.symbology in DIGIT_CODES && e.data.trim() === `{${column}}`);
  if (digitCode) {
    const length = DIGIT_CODES[digitCode.symbology];
    return digitCode.symbology === "upce" ? `0${DIGITS.slice(0, length - 1)}` : DIGITS.slice(0, length);
  }
  if (numberLike(column)) return DIGITS.slice(0, Math.max(1, Math.round(NUMBER_LENGTH * scale)));
  let text = String(column).trim() || "A";
  if (codes.some((e) => ASCII_CODES.has(e.symbology))) {
    text = text.normalize("NFD").replace(/[^\x20-\x7e]/g, "") || "A";
    if (codes.some((e) => e.symbology === "code39")) text = text.toUpperCase().replace(/[^A-Z0-9 .-]/g, "") || "A";
  }
  const chars = [...text];
  return chars.slice(0, Math.max(2, Math.round(NAME_LENGTH * scale))).join("").trim();
}

// Sample values for the columns named (by default all of the label's).
export function sampleRow(label, scale = 1, columns = label.columns) {
  return Object.fromEntries(columns.map((column) => [column, sampleValue(label, column, scale)]));
}

const SLACK = 0.2; // mm a box may reach beyond another or the label: rounding, line spacing

function overlap(a, b) {
  return Math.min(a[2], b[2]) - Math.max(a[0], b[0]) > SLACK && Math.min(a[3], b[3]) - Math.max(a[1], b[1]) > SLACK;
}

// Whether an element the columns fill runs off the label or into a text or
// code beside it, by the boxes of a preview's geometry.
export function overflowing(label, geometry, columns) {
  if (!geometry) return false;
  const filled = new Set(columns.flatMap((column) => elementsOf(label, "column", column)));
  const { width_mm: width, height_mm: height, elements } = geometry;
  for (const element of elements) {
    if (!filled.has(element.index)) continue;
    const [left, top, right, bottom] = element.box;
    if (left < -SLACK || top < -SLACK || right > width + SLACK || bottom > height + SLACK) return true;
    const hit = elements.some((other) => other.index !== element.index && (other.kind === "text" || other.kind === "code") && overlap(element.box, other.box));
    if (hit) return true;
  }
  return false;
}

// A preview with sample values for the columns not in `given`: render(row)
// gives the preview (or null when it is not wanted any more); shorter
// samples while they run off the label or into another element.
export async function withSamples(render, label, given = {}) {
  const sampled = label.columns.filter((column) => !Object.hasOwn(given, column));
  let preview = null;
  for (const scale of SCALES) {
    preview = await render({ ...given, ...sampleRow(label, scale, sampled) });
    if (!preview || !sampled.length || !overflowing(label, preview.geometry, sampled)) break;
  }
  return preview;
}

// --- a series: data columns for the template's fields ------------------------------

// names compared without case and spaces: "Inventory number" = "inventorynumber"
export function sameName(a, b) {
  const key = (s) => String(s).normalize("NFKC").toLowerCase().replace(/\s+/g, "");
  return key(a) === key(b);
}

// The data column of each field read automatically: the one of the same
// name, or the only column for the only field. {mapping, automatic}
export function autoAssign(fields, columns) {
  const mapping = Object.create(null);
  const automatic = Object.create(null); // field -> column
  for (const field of fields) {
    const same = columns.find((c) => sameName(c, field));
    const column = same ?? (columns.length === 1 && fields.length === 1 ? columns[0] : undefined);
    mapping[field] = column !== undefined ? { column } : {};
    if (column !== undefined) automatic[field] = column;
  }
  return { mapping, automatic };
}

const SHOWN = 3; // values of a column named in its option
const SHOWN_LENGTH = 20;
const LOOKED_AT = 50; // rows looked at for them

// "A — 300021, 300022, 300023, …": a column with its first different values
export function columnText(column, rows) {
  const values = [];
  let more = false;
  for (const row of rows.slice(0, LOOKED_AT)) {
    const value = Object.hasOwn(row, column) ? String(row[column]).trim() : "";
    if (!value || values.includes(value)) continue;
    if (values.length === SHOWN) {
      more = true;
      break;
    }
    values.push(value);
  }
  if (rows.length > LOOKED_AT && values.length === SHOWN) more = true;
  if (!values.length) return column;
  const short = values.map((v) => ([...v].length > SHOWN_LENGTH ? `${[...v].slice(0, SHOWN_LENGTH - 1).join("")}…` : v));
  return `${column} — ${short.join(", ")}${more ? ", …" : ""}`;
}
