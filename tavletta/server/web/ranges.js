// Number ranges for label series: "3000-5685", "00300-00450" (leading
// zeros kept), "INV-0001-INV-0250" (the text around the number kept), an
// optional step. No DOM here: tests/js/ranges.test.mjs runs it in Node.
//
// A range either makes values (numbers without a data file) or picks the
// data rows whose value lies in it. Several ranges of one field: any of
// them; several fields: all of them (row by row, never every combination).
// Numbers are BigInt: an inventory number has as many digits as it likes.

const DASHES = /\s*(?:\.\.|[-‐-―−])\s*/g;
// spaces and apostrophes between digits: 3'000, 3 000, 3’000
const GROUPING = /(?<=\d)[\s'’  ](?=\d)/g;
const LAST_NUMBER = /^(.*?)(\d+)(\D*)$/s;
export const MAX_STEP_DIGITS = 18;

// "3'000" -> "3000"; text around the number stays as it is
export function clean(text) {
  return String(text ?? "").trim().replace(GROUPING, "");
}

// The number in a value and the text around it, from its last digits:
// "INV-00300" -> {prefix: "INV-", digits: "00300", suffix: ""}; null
// without a digit.
export function parseValue(text) {
  const match = LAST_NUMBER.exec(clean(text));
  if (!match) return null;
  return { prefix: match[1], digits: match[2], suffix: match[3] };
}

// "3000-5685" (or with an en dash, "..", spaces) split into from and to;
// null if it is not one range. A dash inside the text around the numbers
// ("INV-1-INV-9") is told apart: the split that gives two values with the
// same text around them counts.
export function splitRange(text) {
  const value = clean(text);
  const found = [];
  for (const match of value.matchAll(DASHES)) {
    const from = value.slice(0, match.index);
    const to = value.slice(match.index + match[0].length);
    const a = parseValue(from);
    const b = parseValue(to);
    if (a && b && a.prefix === b.prefix && a.suffix === b.suffix) found.push([from, to]);
  }
  return found.length === 1 ? found[0] : null;
}

function padded(digits) {
  return digits.length > 1 && digits[0] === "0";
}

// A range checked: {ok: true, count, first, last, value(i), matches(v)} or
// {ok: false, error: code, field: "from" | "to" | "step", params}.
// Codes: from_missing, to_missing, step_invalid, pattern_differs,
// width_differs, to_below_from.
export function checkRange({ from, to, step = "1" }) {
  const a = parseValue(from);
  if (!a) return fail("from_missing", "from");
  const b = parseValue(to);
  if (!b) return fail("to_missing", "to");
  const stepText = clean(step) || "1";
  if (!/^\d+$/.test(stepText) || stepText.length > MAX_STEP_DIGITS || BigInt(stepText) < 1n) {
    return fail("step_invalid", "step");
  }
  if (a.prefix !== b.prefix || a.suffix !== b.suffix) {
    return fail("pattern_differs", "to", { from: clean(from), to: clean(to) });
  }
  const width = padded(a.digits) || padded(b.digits) ? a.digits.length : 0;
  if (width && a.digits.length !== b.digits.length) {
    return fail("width_differs", "to", { from: a.digits.length, to: b.digits.length });
  }
  const low = BigInt(a.digits);
  const high = BigInt(b.digits);
  const by = BigInt(stepText);
  if (high < low) return fail("to_below_from", "to", { from: clean(from) });
  const count = (high - low) / by + 1n;
  const format = (n) => a.prefix + (width ? n.toString().padStart(width, "0") : n.toString()) + a.suffix;
  const value = (i) => format(low + BigInt(i) * by);
  return {
    ok: true,
    count,
    first: format(low),
    last: format(low + (count - 1n) * by),
    value,
    matches(text) {
      const v = parseValue(text);
      if (!v || v.prefix !== a.prefix || v.suffix !== a.suffix) return false;
      if (width && v.digits.length !== width) return false;
      const n = BigInt(v.digits);
      return n >= low && n <= high && (n - low) % by === 0n;
    },
  };
}

function fail(error, field, params = {}) {
  return { ok: false, error, field, params };
}

// The ranges of several fields checked together. fields: [{column, ranges:
// [{from, to, step}]}] (fields without a range are left out). Returns
// {ok: true, fields: [{column, checked: [...], count}]} or the first
// problem {ok: false, column, index, error, field, params}.
export function checkFields(fields) {
  const result = [];
  for (const { column, ranges } of fields) {
    const checked = [];
    for (const [index, range] of ranges.entries()) {
      const one = checkRange(range);
      if (!one.ok) return { ...one, column, index };
      checked.push(one);
    }
    if (checked.length) {
      result.push({ column, checked, count: checked.reduce((sum, r) => sum + r.count, 0n) });
    }
  }
  return { ok: true, fields: result };
}

// How many labels ranges make without a data file: each field makes the
// same number of values, its ranges one after the other; null if they do
// not ({ok: false, error: "counts_differ", params: {counts}}).
export function generatedCount(checked) {
  if (!checked.length) return { ok: true, count: 0n };
  const counts = checked.map((f) => f.count);
  if (counts.some((c) => c !== counts[0])) {
    return { ok: false, error: "counts_differ", params: { counts: checked.map((f) => [f.column, f.count]) } };
  }
  return { ok: true, count: counts[0] };
}

// The value of a field's ranges at position i (0-based) across its ranges.
function valueAt(field, i) {
  let rest = BigInt(i);
  for (const range of field.checked) {
    if (rest < range.count) return range.value(rest);
    rest -= range.count;
  }
  throw new RangeError("beyond the ranges");
}

// The row at position i of generated values, with the fixed values.
// (entries, not assignments: a column may be called "__proto__")
export function generatedRow(checked, i, fixed = {}) {
  const values = new Map(Object.entries(fixed));
  for (const field of checked) values.set(field.column, valueAt(field, i));
  return Object.fromEntries(values);
}

// Rows made of the ranges, from position `start` on (to continue a series),
// at most `limit` of them.
export function generateRows(checked, fixed = {}, start = 0, limit = Infinity) {
  const { count } = generatedCount(checked);
  const end = Math.min(Number(count), start + limit);
  const rows = [];
  for (let i = start; i < end; i++) rows.push(generatedRow(checked, i, fixed));
  return rows;
}

// The indexes of the data rows whose values lie in the ranges: in any range
// of a field, for every field with ranges.
export function matchingRows(rows, checked) {
  const found = [];
  rows.forEach((row, index) => {
    if (checked.every((f) => f.checked.some((r) => r.matches(Object.hasOwn(row, f.column) ? row[f.column] : "")))) {
      found.push(index);
    }
  });
  return found;
}

// "1-5, 8, 12-" -> the 0-based indexes of rows 1 to 5, 8 and 12 to the
// last (of `total`); null for text that is no such list.
export function parseRowList(text, total) {
  const picked = new Set();
  const parts = clean(text).split(/[,;]\s*|\s+/).filter(Boolean);
  if (!parts.length) return null;
  for (const part of parts) {
    const match = /^(\d*)\s*(?:[-‐-―−]\s*(\d*))?$/.exec(part);
    if (!match || (!match[1] && !match[2])) return null;
    const dash = part.length > match[1].length;
    const first = match[1] ? Number(match[1]) : 1;
    const last = dash ? (match[2] ? Number(match[2]) : total) : first;
    if (first < 1 || last < first) return null;
    for (let n = first; n <= Math.min(last, total); n++) picked.add(n - 1);
  }
  return [...picked].sort((x, y) => x - y);
}

// Contiguous rows as text: [0,1,2,4] -> "1-3, 5"
export function rowListText(indexes) {
  const parts = [];
  let start = null;
  let previous = null;
  for (const i of indexes) {
    if (start === null) {
      start = previous = i;
    } else if (i === previous + 1) {
      previous = i;
    } else {
      parts.push(start === previous ? `${start + 1}` : `${start + 1}-${previous + 1}`);
      start = previous = i;
    }
  }
  if (start !== null) parts.push(start === previous ? `${start + 1}` : `${start + 1}-${previous + 1}`);
  return parts.join(", ");
}

// Whether whole numbers from first to last leave some out when `count`
// labels were printed ("70000" to "70004" with 4 labels); false for
// anything that is not a number
export function leavesOut(first, last, count) {
  const digits = /^\d{1,30}$/;
  if (!digits.test(String(first)) || !digits.test(String(last))) return false;
  const span = BigInt(last) - BigInt(first);
  return (span < 0n ? -span : span) + 1n > BigInt(count);
}
