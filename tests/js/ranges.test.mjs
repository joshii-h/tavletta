// The web page's number ranges (tavletta/server/web/ranges.js), with Node's
// own test runner: node --test tests/js/ (tests/test_webui_js.py runs it).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  checkFields,
  checkRange,
  clean,
  generatedCount,
  generatedRow,
  generateRows,
  matchingRows,
  parseRowList,
  parseValue,
  rowListText,
  splitRange,
} from "../../tavletta/server/web/ranges.js";

test("a plain range counts and names its first and last value", () => {
  const range = checkRange({ from: "3000", to: "5685" });
  assert.equal(range.ok, true);
  assert.equal(range.count, 2686n);
  assert.equal(range.first, "3000");
  assert.equal(range.last, "5685");
  assert.equal(range.value(1203), "4203");
});

test("leading zeros keep the width", () => {
  const range = checkRange({ from: "00300", to: "00450" });
  assert.equal(range.count, 151n);
  assert.equal(range.first, "00300");
  assert.equal(range.value(1), "00301");
  assert.equal(range.last, "00450");
  assert.equal(checkRange({ from: "0098", to: "0102" }).value(2), "0100");
});

test("a width without leading zeros may grow", () => {
  const range = checkRange({ from: "998", to: "1002" });
  assert.equal(range.count, 5n);
  assert.equal(range.last, "1002");
});

test("leading zeros need the same width", () => {
  const range = checkRange({ from: "00300", to: "4500" });
  assert.deepEqual([range.ok, range.error, range.field], [false, "width_differs", "to"]);
  assert.deepEqual(range.params, { from: 5, to: 4 });
});

test("a step", () => {
  const range = checkRange({ from: "10", to: "20", step: "3" });
  assert.equal(range.count, 4n);
  assert.equal(range.last, "19");
  assert.equal(checkRange({ from: "1", to: "9", step: "0" }).error, "step_invalid");
  assert.equal(checkRange({ from: "1", to: "9", step: "x" }).error, "step_invalid");
  assert.equal(checkRange({ from: "1", to: "9", step: "-2" }).error, "step_invalid");
  assert.equal(checkRange({ from: "1", to: "9", step: "" }).count, 9n);
});

test("text around the number", () => {
  const range = checkRange({ from: "INV-0001", to: "INV-0250" });
  assert.equal(range.count, 250n);
  assert.equal(range.value(9), "INV-0010");
  assert.equal(checkRange({ from: "A1", to: "B9" }).error, "pattern_differs");
  assert.equal(checkRange({ from: "1kg", to: "9kg" }).value(1), "2kg");
});

test("problems", () => {
  assert.equal(checkRange({ from: "", to: "5" }).error, "from_missing");
  assert.equal(checkRange({ from: "abc", to: "5" }).error, "from_missing");
  assert.equal(checkRange({ from: "5", to: "" }).error, "to_missing");
  const below = checkRange({ from: "5685", to: "3000" });
  assert.deepEqual([below.error, below.params.from], ["to_below_from", "5685"]);
});

test("numbers as people write them", () => {
  assert.equal(clean(" 3'000 "), "3000");
  assert.equal(clean("3 000"), "3000");
  assert.equal(clean("3’000"), "3000");
  assert.equal(clean("3 000"), "3000");
  assert.equal(checkRange({ from: "3'000", to: "5'685" }).count, 2686n);
  assert.deepEqual(parseValue("Room 2 Box 0300"), { prefix: "Room 2 Box ", digits: "0300", suffix: "" });
  assert.equal(parseValue("none"), null);
});

test("large numbers stay exact", () => {
  const range = checkRange({ from: "900719925474099100", to: "900719925474099200" });
  assert.equal(range.count, 101n);
  assert.equal(range.last, "900719925474099200");
});

test("a range typed or pasted in one", () => {
  assert.deepEqual(splitRange("3000-5685"), ["3000", "5685"]);
  assert.deepEqual(splitRange("3000 – 5685"), ["3000", "5685"]);
  assert.deepEqual(splitRange("00300..00450"), ["00300", "00450"]);
  assert.deepEqual(splitRange("3'000 - 5'685"), ["3000", "5685"]);
  assert.deepEqual(splitRange("INV-0001-INV-0250"), ["INV-0001", "INV-0250"]);
  assert.equal(splitRange("3000"), null);
  assert.equal(splitRange("A-1-B-2"), null);
});

test("several fields make rows side by side, not every combination", () => {
  const checked = checkFields([
    { column: "Number", ranges: [{ from: "3000", to: "3002" }] },
    { column: "Box", ranges: [{ from: "B01", to: "B03" }] },
    { column: "Room", ranges: [] },
  ]);
  assert.equal(checked.ok, true);
  assert.equal(generatedCount(checked.fields).count, 3n);
  assert.deepEqual(generatedRow(checked.fields, 2, { Room: "A" }), { Room: "A", Number: "3002", Box: "B03" });
  assert.deepEqual(generateRows(checked.fields, {}, 1), [
    { Number: "3001", Box: "B02" },
    { Number: "3002", Box: "B03" },
  ]);
  const differ = checkFields([
    { column: "Number", ranges: [{ from: "1", to: "3" }] },
    { column: "Box", ranges: [{ from: "1", to: "2" }] },
  ]);
  assert.equal(generatedCount(differ.fields).error, "counts_differ");
});

test("several ranges of one field follow each other", () => {
  const checked = checkFields([
    { column: "N", ranges: [{ from: "1", to: "2" }, { from: "10", to: "11" }] },
  ]);
  assert.equal(generatedCount(checked.fields).count, 4n);
  assert.deepEqual(generateRows(checked.fields).map((r) => r.N), ["1", "2", "10", "11"]);
  assert.deepEqual(generateRows(checked.fields, {}, 0, 2).length, 2);
});

test("a problem names its field and range", () => {
  const checked = checkFields([
    { column: "N", ranges: [{ from: "1", to: "2" }, { from: "9", to: "3" }] },
  ]);
  assert.deepEqual([checked.ok, checked.column, checked.index, checked.error], [false, "N", 1, "to_below_from"]);
});

test("ranges pick data rows", () => {
  const rows = [
    { N: "00299", R: "A" },
    { N: "00300", R: "A" },
    { N: "300", R: "A" },
    { N: "00450", R: "B" },
    { N: "00451", R: "A" },
    { N: "x", R: "A" },
  ];
  const one = checkFields([{ column: "N", ranges: [{ from: "00300", to: "00450" }] }]);
  assert.deepEqual(matchingRows(rows, one.fields), [1, 3]);
  const both = checkFields([
    { column: "N", ranges: [{ from: "00300", to: "00450" }] },
    { column: "R", ranges: [{ from: "A", to: "A" }] },
  ]);
  assert.equal(both.ok, false); // "A" has no number
  // without leading zeros in the range a value is compared as a number
  const stepped = checkFields([{ column: "N", ranges: [{ from: "298", to: "301", step: "2" }] }]);
  assert.deepEqual(matchingRows(rows, stepped.fields), [1, 2]);
});

test("a list of rows", () => {
  assert.deepEqual(parseRowList("1-3, 5", 10), [0, 1, 2, 4]);
  assert.deepEqual(parseRowList("8-", 10), [7, 8, 9]);
  assert.deepEqual(parseRowList("-2", 10), [0, 1]);
  assert.deepEqual(parseRowList("3;1", 10), [0, 2]);
  assert.deepEqual(parseRowList("9-20", 10), [8, 9]);
  assert.equal(parseRowList("", 10), null);
  assert.equal(parseRowList("5-2", 10), null);
  assert.equal(parseRowList("a", 10), null);
  assert.equal(rowListText([0, 1, 2, 4, 6, 7]), "1-3, 5, 7-8");
  assert.equal(rowListText([]), "");
});

test("a column may be called __proto__", () => {
  const checked = checkFields([{ column: "__proto__", ranges: [{ from: "1", to: "2" }] }]);
  const row = generatedRow(checked.fields, 1, { constructor: "x" });
  assert.deepEqual(Object.keys(row), ["constructor", "__proto__"]);
  assert.equal(JSON.stringify(row), '{"constructor":"x","__proto__":"2"}');
  assert.deepEqual(matchingRows([JSON.parse('{"__proto__": "2"}'), {}], checked.fields), [0]);
});

test("numbers left out between the first and the last printed", async () => {
  const { leavesOut } = await import("../../tavletta/server/web/ranges.js");
  assert.equal(leavesOut("70000", "70004", 4), true);
  assert.equal(leavesOut("70000", "70004", 5), false);
  assert.equal(leavesOut("00300", "00301", 2), false);
  assert.equal(leavesOut("7", "7", 1), false);
  assert.equal(leavesOut("B-07-1", "B-07-4", 2), false);
  assert.equal(leavesOut("", "5", 1), false);
});
