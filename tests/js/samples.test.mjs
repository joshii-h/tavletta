// Sample values for a template's data fields and the series' assignment
// helpers (tavletta/server/web/samples.js), with Node's own test runner.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  autoAssign,
  columnText,
  elementsOf,
  numberLike,
  overflowing,
  sameName,
  sampleRow,
  sampleValue,
  withSamples,
} from "../../tavletta/server/web/samples.js";

// an asset label: a company line, the inventory number as text and as QR code
const ASSET = {
  columns: ["Inventory number"],
  elements: [
    { index: 1, kind: "text", text: "Asset" },
    { index: 2, kind: "text", text: "{Inventory number}" },
    { index: 3, kind: "code", data: "{Inventory number}", symbology: "qr" },
  ],
};

test("number-like fields", () => {
  for (const name of ["Inventory number", "Inventarnummer", "Asset ID", "AssetID", "Nr.", "Kundennr", "Serial No", "Counter", "Numéro", "Code"]) {
    assert.equal(numberLike(name), true, name);
  }
  for (const name of ["Name", "Shelf", "Description", "Valid", "Casino", "Notes", "Ort"]) {
    assert.equal(numberLike(name), false, name);
  }
});

test("a number-like field gets digits, the others their name, shortened", () => {
  assert.equal(sampleValue(ASSET, "Inventory number"), "123456");
  assert.equal(sampleValue(ASSET, "Inventory number", 0.5), "123");
  const label = { columns: ["Name", "Description of the item"], elements: [{ index: 1, kind: "text", text: "{Name} {Description of the item}" }] };
  assert.deepEqual(sampleRow(label), { Name: "Name", "Description of the item": "Description" });
  assert.equal(sampleValue(label, "Description of the item", 0.25), "Des");
  assert.equal(sampleValue(label, "Name", 0.25), "Nam");
});

test("a sample never keeps the placeholder's braces and is shorter than it", () => {
  for (const name of ["Inventory number", "Name", "A", "Description of the item", "Ünïcødé"]) {
    const label = { columns: [name], elements: [{ index: 1, kind: "text", text: `{${name}}` }] };
    const value = sampleValue(label, name);
    assert.doesNotMatch(value, /[{}]/);
    assert.ok([...value].length < [...`{${name}}`].length, name);
  }
});

test("codes that take only digits get as many as they need", () => {
  const code = (symbology) => ({ columns: ["Article"], elements: [{ index: 1, kind: "code", data: "{Article}", symbology }] });
  assert.equal(sampleValue(code("ean13"), "Article"), "123456789012");
  assert.equal(sampleValue(code("ean8"), "Article"), "1234567");
  assert.equal(sampleValue(code("upca"), "Article"), "12345678901");
  assert.equal(sampleValue(code("upce"), "Article"), "0123456");
  assert.equal(sampleValue(code("itf14"), "Article"), "1234567890123");
  assert.equal(sampleValue(code("ean13"), "Article", 0.25), "123456789012"); // never shortened
  assert.equal(sampleValue(code("code39"), "Article"), "ARTICLE");
  assert.equal(sampleValue({ columns: ["Größe"], elements: [{ index: 1, kind: "code", data: "{Größe}", symbology: "code128" }] }, "Größe"), "Groe");
});

test("the elements a field fills", () => {
  assert.deepEqual(elementsOf(ASSET, "column", "Inventory number"), [1, 2]);
  const label = { elements: [{ index: 1, kind: "text", text: "{=date:%d.%m.}" }, { index: 2, kind: "text", text: "x", name: "Title" }] };
  assert.deepEqual(elementsOf(label, "variable", "date"), [0]);
  assert.deepEqual(elementsOf(label, "element", "Title"), [1]);
});

// geometry of a 40 × 18.6 mm label: the QR code at 23.5–37.5 mm
function geometry(textRight) {
  return {
    width_mm: 40,
    height_mm: 18.6,
    elements: [
      { index: 0, kind: "text", box: [2.5, 3.4, 14.7, 7.1] },
      { index: 1, kind: "text", box: [2.6, 14.7, textRight, 18.58] },
      { index: 2, kind: "code", box: [23.5, 3.75, 37.5, 17.75] },
    ],
  };
}

test("a filled text running into the code beside it or off the label overflows", () => {
  assert.equal(overflowing(ASSET, geometry(38.08), ["Inventory number"]), true); // "{Inventory number}" as it is
  assert.equal(overflowing(ASSET, geometry(15.9), ["Inventory number"]), false);
  assert.equal(overflowing(ASSET, geometry(41), ["Inventory number"]), true);
  assert.equal(overflowing(ASSET, geometry(38.08), []), false); // not a sample: not checked
  assert.equal(overflowing(ASSET, null, ["Inventory number"]), false);
});

test("shorter samples while they overflow, at most three renders", async () => {
  const rows = [];
  const render = async (row) => {
    rows.push(row);
    return { geometry: geometry(row["Inventory number"].length > 3 ? 38 : 15) };
  };
  await withSamples(render, ASSET);
  assert.deepEqual(rows.map((r) => r["Inventory number"]), ["123456", "123"]);
  rows.length = 0;
  const always = async (row) => (rows.push(row), { geometry: geometry(39) });
  await withSamples(always, ASSET);
  assert.equal(rows.length, 3);
});

test("given values stay, only the others are samples", async () => {
  const label = { columns: ["Code", "Name"], elements: [{ index: 1, kind: "text", text: "{Code} {Name}" }] };
  const rows = [];
  await withSamples(async (row) => (rows.push(row), { geometry: null }), label, { Code: "B-07-1" });
  assert.deepEqual(rows, [{ Code: "B-07-1", Name: "Name" }]);
  rows.length = 0;
  const gone = await withSamples(async (row) => (rows.push(row), null), label);
  assert.equal(gone, null);
  assert.equal(rows.length, 1);
});

test("names compared without case and spaces", () => {
  assert.equal(sameName("Inventory number", " inventory  Number "), true);
  assert.equal(sameName("Inventory number", "inventorynumber"), true);
  assert.equal(sameName("Inventory number", "Inventory_number"), false);
});

test("columns of the same name are assigned automatically", () => {
  const { mapping, automatic } = autoAssign(["Inventory number", "Room", "__proto__"], ["room", "INVENTORY NUMBER", "Other", "__proto__"]);
  assert.deepEqual(mapping["Inventory number"], { column: "INVENTORY NUMBER" });
  assert.deepEqual(mapping.Room, { column: "room" });
  assert.deepEqual(mapping["__proto__"], { column: "__proto__" }); // a name from a file, not the prototype
  assert.equal(Object.getPrototypeOf(mapping), null);
  assert.deepEqual(Object.keys(automatic), ["Inventory number", "Room", "__proto__"]);
  const lone = autoAssign(["Code"], ["Values"]);
  assert.deepEqual(lone.mapping.Code, { column: "Values" });
  const none = autoAssign(["Code", "Name"], ["A", "B"]);
  assert.deepEqual([none.mapping.Code, Object.keys(none.automatic)], [{}, []]);
});

test("a column's option names its first different values", () => {
  const rows = ["300021", "300022", "300022", "", "300023", "300024"].map((A) => ({ A }));
  assert.equal(columnText("A", rows), "A — 300021, 300022, 300023, …");
  assert.equal(columnText("A", rows.slice(0, 2)), "A — 300021, 300022");
  assert.equal(columnText("B", rows), "B");
  assert.equal(columnText("A", [{ A: "x".repeat(30) }]), `A — ${"x".repeat(19)}…`);
  assert.equal(columnText("__proto__", [{}]), "__proto__");
});
