// A series: data from a file or a pasted list (or numbers made from
// ranges), its columns assigned to the template's fields, the rows chosen
// (all, some, or by ranges of a field: "Inventory number 3000–5685"),
// checked with the first and last label, printed with progress and Cancel,
// continued from where it stopped. What is entered stays while the tab is
// open, also when going back.

import { api } from "./api.js";
import { debounce, h, uid } from "./dom.js";
import { num, t } from "./i18n.js";
import {
  checkFields,
  generatedCount,
  generatedRow,
  generateRows,
  matchingRows,
  parseRowList,
  rowListText,
  splitRange,
} from "./ranges.js";
import { backLink, labelFields, printerPicker, title } from "./print.js";
import {
  CONFIRM_FROM,
  confirm,
  describe,
  errorBanner,
  errorSummary,
  errorText,
  field,
  jobProgress,
  jobResult,
  loading,
  notice,
  previewPane,
  printerChoice,
  session,
  summaryBox,
  warningsBox,
} from "./ui.js";

const batches = new Map(); // label name -> what was entered, for this tab

export function forgetBatches() {
  batches.clear();
}
const PAGE = 50; // rows of the table at once
const SAMPLE = 5; // rows shown after reading

const STEPS = [
  () => t("Data"),
  () => t("Assign"),
  () => t("Choose"),
  () => t("Check"),
  () => t("Print"),
];

function fresh() {
  return {
    step: 0,
    source: "file", // file, paste, numbers
    file: null,
    header: true,
    pasted: "",
    pasteHeader: false,
    data: null, // POST /data
    mapping: {}, // template column -> {column} or {fixed}
    mode: "all", // all, pick, ranges
    picked: new Set(),
    ranges: {}, // template column -> [{from, to, step}]
    fixed: {}, // template column -> value, numbers without a range
    variables: {},
    copies: "1",
    printer: undefined,
    resume: null, // rows still to print after a job stopped
    job: null, // {id, rows}
    ended: null, // the job's end
  };
}

// the range problem in words (ranges.js names it by a code)
function rangeText(problem) {
  const p = problem.params ?? {};
  const texts = {
    from_missing: t("From needs a number."),
    to_missing: t("To needs a number."),
    step_invalid: t("Step must be a whole number from 1."),
    pattern_differs: t("From and To must be the same apart from the number ({from}, {to}).", p),
    width_differs: t("From has {from} digits, To {to}. Enter both with the same number of digits.", p),
    to_below_from: t("To must be greater than or equal to From ({from}).", p),
    counts_differ: t("The ranges make different numbers of labels: {counts}. Make them the same length.", {
      counts: (p.counts ?? []).map(([column, count]) => `${column} ${num(count)}`).join(", "),
    }),
  };
  return texts[problem.error] ?? problem.error;
}

export async function batchView(name, ctx) {
  const area = h("div");
  ctx.mount(t("Series of {name}", { name: title(name) }), backLink(), area);
  const done = loading(area);
  let label;
  let printers;
  try {
    [label, printers] = await Promise.all([describe(name), printerChoice()]);
  } catch (error) {
    done();
    if (ctx.live()) area.append(errorBanner(error, () => batchView(name, ctx)));
    return;
  }
  done();
  if (!ctx.live()) return;
  if (!label.columns.length) {
    area.append(
      notice(
        "info",
        h("p", null, t("This template has no data fields ({placeholder}). For many equal labels, set the copies when printing.", { placeholder: "{Column}" })),
        h("p", null, h("a", { href: `#/print/${encodeURIComponent(name)}` }, t("Print"))),
      ),
    );
    return;
  }
  if (!batches.has(name)) batches.set(name, fresh());
  const st = batches.get(name);
  const steps = h("ol", { class: "steps" });
  const body = h("div", { class: "step" });
  area.append(h("nav", { "aria-label": t("Steps") }, steps), body);
  const disposers = [];
  ctx.onDispose(() => disposers.splice(0).forEach((fn) => fn()));

  // --- the rows to print, from what was entered ---------------------------------

  const columns = label.columns;
  const numbers = () => st.source === "numbers";

  function mapped() {
    if (!st.data) return [];
    return st.data.rows.map((row) => {
      const out = {};
      for (const column of columns) {
        const how = st.mapping[column];
        out[column] = how?.column !== undefined ? (row[how.column] ?? "") : (how?.fixed ?? "");
      }
      return out;
    });
  }

  function rangeFields() {
    return columns.map((column) => ({ column, ranges: (st.ranges[column] ?? []).filter((r) => r.from || r.to) }));
  }

  // {ok, count (rows), row(i), rows() } or {ok: false, text}
  function selection() {
    if (st.resume) {
      const rows = st.resume;
      return { ok: true, count: rows.length, row: (i) => rows[i], rows: () => rows };
    }
    if (numbers()) {
      const checked = checkFields(rangeFields());
      if (!checked.ok) return { ok: false, text: rangeText(checked), problem: checked };
      if (!checked.fields.length) return { ok: false, text: t("Enter a range for at least one field.") };
      const counted = generatedCount(checked.fields);
      if (!counted.ok) return { ok: false, text: rangeText(counted) };
      const fixed = Object.fromEntries(columns.filter((c) => !checked.fields.some((f) => f.column === c)).map((c) => [c, st.fixed[c] ?? ""]));
      const count = Number(counted.count > BigInt(Number.MAX_SAFE_INTEGER) ? Number.MAX_SAFE_INTEGER : counted.count);
      return {
        ok: true,
        count,
        row: (i) => generatedRow(checked.fields, i, fixed),
        rows: () => generateRows(checked.fields, fixed),
        ranged: checked.fields[0]?.column,
      };
    }
    const all = mapped();
    let indexes;
    if (st.mode === "pick") indexes = [...st.picked].sort((a, b) => a - b);
    else if (st.mode === "ranges") {
      const checked = checkFields(rangeFields());
      if (!checked.ok) return { ok: false, text: rangeText(checked), problem: checked };
      if (!checked.fields.length) return { ok: false, text: t("Enter a range for at least one field.") };
      indexes = matchingRows(all, checked.fields);
      return { ok: true, count: indexes.length, row: (i) => all[indexes[i]], rows: () => indexes.map((i) => all[i]), ranged: checked.fields[0].column, indexes };
    } else indexes = all.map((_, i) => i);
    return { ok: true, count: indexes.length, row: (i) => all[indexes[i]], rows: () => indexes.map((i) => all[i]), indexes };
  }

  // what a label is called in captions: its ranged value, else its first field
  function shown(row, sel, index) {
    const column = sel.ranged ?? columns[0];
    return row?.[column] || t("Row {n}", { n: num(index + 1) });
  }

  function copies() {
    const text = String(st.copies).replace(/['’\s]/g, "");
    return /^\d+$/.test(text) ? Number(text) : NaN;
  }

  function limit() {
    return label.on_sheet ? session.me.limits.max_sheet_labels : session.me.limits.max_labels;
  }

  // "2'686 labels (3000–5685)"
  function countText(sel) {
    if (!sel.ok) return sel.text;
    const labels = sel.count * (copies() || 1);
    if (!sel.count) return t("No row selected.");
    const first = shown(sel.row(0), sel, 0);
    const last = shown(sel.row(sel.count - 1), sel, sel.count - 1);
    const span = sel.count > 1 ? `${first}–${last}` : first;
    return t("{count} labels ({span})", { count: num(labels), span });
  }

  // --- the steps -------------------------------------------------------------------

  function go(step) {
    st.step = step;
    draw();
    body.querySelector("h2")?.focus();
  }

  function stepList() {
    steps.replaceChildren(
      ...STEPS.map((name, i) => {
        const skipped = i === 1 && numbers();
        const text = `${i + 1}. ${name()}${skipped ? ` (${t("not needed")})` : ""}`;
        const reachable = i < st.step && !skipped && !st.job;
        return h(
          "li",
          { "aria-current": i === st.step ? "step" : null, class: i < st.step ? "past" : null },
          reachable ? h("button", { type: "button", class: "link", onclick: () => go(i) }, text) : h("span", null, text),
        );
      }),
    );
  }

  function draw() {
    stepList();
    const views = [dataStep, assignStep, chooseStep, checkStep, printStep];
    body.replaceChildren();
    views[st.step]();
  }

  function heading(text) {
    return h("h2", { tabindex: "-1" }, text);
  }

  function nav({ back = true, next, nextText = t("Next") }) {
    return h(
      "div",
      { class: "actions" },
      back && st.step > 0 && h("button", { type: "button", onclick: () => go(st.step === 2 && numbers() ? 0 : st.step - 1) }, t("Back")),
      next && h("button", { type: "submit", class: "primary" }, nextText),
    );
  }

  // 1. data ---------------------------------------------------------------------------

  function dataStep() {
    const summary = summaryBox();
    const name = uid("source");
    const choice = (value, text) => {
      const input = h("input", { type: "radio", name, value, id: uid("src"), checked: st.source === value });
      input.addEventListener("change", () => {
        st.source = value;
        draw();
      });
      return h("div", { class: "check" }, input, h("label", { for: input.id }, text));
    };
    const form = h("form", { novalidate: true }, summary);
    form.append(
      heading(t("Data")),
      h(
        "fieldset",
        { class: "choices" },
        h("legend", null, t("Where do the values come from?")),
        choice("file", t("A file (CSV, XLSX, ODS)")),
        choice("paste", t("A pasted list")),
        choice("numbers", t("No data: make numbers from ranges")),
      ),
    );
    let input = null;
    let one = null;
    const header = h("input", { type: "checkbox", id: uid("header") });
    if (st.source === "file") {
      input = h("input", { type: "file", accept: ".csv,.tsv,.txt,.xlsx,.ods" });
      one = field({ label: t("Data file"), hint: t("At most {size} MB. Every cell is read as the text it shows: leading zeros stay.", { size: num(Math.floor((session.me.limits.max_body * 3) / 4 / 1048576)) }), input });
      header.checked = st.header;
      form.append(one.node, h("div", { class: "check" }, header, h("label", { for: header.id }, t("The first row holds the column names"))));
      if (st.file) form.append(h("p", { class: "hint" }, t("Last file read: {name}", { name: st.file.name })));
    } else if (st.source === "paste") {
      input = h("textarea", { rows: 8, spellcheck: "false" });
      input.value = st.pasted;
      one = field({ label: t("List"), hint: t("One label per line; columns separated by tab, semicolon or comma (as copied from a spreadsheet)."), input });
      header.checked = st.pasteHeader;
      form.append(one.node, h("div", { class: "check" }, header, h("label", { for: header.id }, t("The first row holds the column names"))));
    } else {
      form.append(h("p", null, t("The next steps make the values from ranges, such as 3000–5685 or 00300–00450.")));
    }
    const sample = h("div");
    if (st.data && st.source !== "numbers") sample.append(dataSummary(st.data));
    form.append(sample, nav({ next: true, nextText: st.source === "numbers" ? t("Next") : t("Read and continue") }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (st.source === "numbers") {
        st.data = null;
        st.mode = "ranges";
        go(2);
        return;
      }
      one.clear();
      const button = form.querySelector("button[type=submit]");
      try {
        button.disabled = true;
        await read(input, header.checked, one);
        errorSummary(summary, []);
        go(1);
      } catch (error) {
        const text = error.message;
        one.error(text);
        errorSummary(summary, [{ text, input }]);
      } finally {
        button.disabled = false;
      }
    });
    body.append(form);
  }

  async function read(input, header, one) {
    let bytes;
    let fileName;
    if (st.source === "file") {
      const file = input.files[0] ?? st.file;
      if (!file) throw new Error(t("Choose a data file."));
      fileName = file.name;
      bytes = new Uint8Array(await file.arrayBuffer());
      st.file = file;
      st.header = header;
    } else {
      st.pasted = input.value;
      st.pasteHeader = header;
      if (!input.value.trim()) throw new Error(t("Paste the list first."));
      fileName = t("List");
      bytes = new TextEncoder().encode(input.value);
    }
    if (((bytes.length + 2) / 3) * 4 + 1024 > session.me.limits.max_body) {
      throw new Error(t("The data are too large: at most {size} MB.", { size: num(Math.floor((session.me.limits.max_body * 3) / 4 / 1048576)) }));
    }
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    try {
      const data = await api("POST", "/data", { name: fileName, content: btoa(binary), header: st.source === "file" ? st.header : st.pasteHeader });
      st.data = { ...data, name: fileName };
    } catch (error) {
      throw new Error(errorText(error));
    }
    // a new file: assignments by equal names, nothing picked
    st.mapping = {};
    for (const column of columns) {
      const same = st.data.columns.find((c) => c.toLowerCase() === column.toLowerCase());
      st.mapping[column] = same !== undefined ? { column: same } : st.data.columns.length === 1 && columns.length === 1 ? { column: st.data.columns[0] } : {};
    }
    st.picked = new Set();
    st.resume = null;
    one.clear();
  }

  function dataSummary(data) {
    const kind = { csv: "CSV", xlsx: "XLSX", ods: "ODS" }[data.kind] ?? data.kind;
    const delimiter = { ";": t("semicolon"), ",": t("comma"), "\t": t("tab") }[data.delimiter];
    const facts = [
      t("{name}: {kind}", { name: data.name, kind }),
      delimiter && t("Separated by {delimiter}", { delimiter }),
      data.sheet && t("Sheet: {sheet}", { sheet: data.sheet }),
      t("{rows} rows, {columns} columns", { rows: num(data.rows.length), columns: num(data.columns.length) }),
    ].filter(Boolean);
    return h("div", { class: "panel" }, h("ul", { class: "facts" }, facts.map((f) => h("li", null, f))), table(data.columns, data.rows.slice(0, SAMPLE), t("The first {count} rows", { count: num(Math.min(SAMPLE, data.rows.length)) })));
  }

  function table(names, rows, caption, first = 0) {
    return h(
      "div",
      { class: "table-scroll", tabindex: "0", role: "region", "aria-label": caption },
      h(
        "table",
        null,
        h("caption", null, caption),
        h("thead", null, h("tr", null, h("th", { scope: "col" }, t("Row")), names.map((n) => h("th", { scope: "col" }, n)))),
        h("tbody", null, rows.map((row, i) => h("tr", null, h("th", { scope: "row" }, num(first + i + 1)), names.map((n) => h("td", null, row[n] ?? ""))))),
      ),
    );
  }

  // 2. assign -------------------------------------------------------------------------

  function assignStep() {
    const summary = summaryBox();
    const form = h("form", { novalidate: true }, summary, heading(t("Assign")));
    if (st.data && st.data.sheets.length > 1) {
      const sheet = h("select", null, st.data.sheets.map((s) => h("option", { value: s, selected: s === st.data.sheet }, s)));
      const one = field({ label: t("Sheet"), input: sheet });
      sheet.addEventListener("change", async () => {
        try {
          const data = await api("POST", "/data", { name: st.data.name, content: await base64(st.file), sheet: sheet.value, header: st.header });
          st.data = { ...data, name: st.data.name };
          draw();
        } catch (error) {
          one.error(errorText(error));
        }
      });
      form.append(one.node);
    }
    form.append(h("p", null, t("Choose the data column for each field of the template.")));
    const fields = columns.map((column) => {
      const select = h(
        "select",
        null,
        h("option", { value: "" }, t("– not assigned –")),
        h("option", { value: "\u0000fixed" }, t("The same value for all")),
        st.data.columns.map((c) => h("option", { value: c, selected: st.mapping[column]?.column === c }, c)),
      );
      const fixed = h("input", { type: "text", value: st.mapping[column]?.fixed ?? "", autocomplete: "off" });
      const fixedField = field({ label: t("Value for {name}", { name: column }), input: fixed });
      if ("fixed" in (st.mapping[column] ?? {})) select.value = "\u0000fixed";
      fixedField.node.hidden = select.value !== "\u0000fixed";
      const one = field({ label: column, hint: t("Data field {placeholder} of the template, required", { placeholder: `{${column}}` }), input: select });
      const update = () => {
        fixedField.node.hidden = select.value !== "\u0000fixed";
        st.mapping[column] = select.value === "\u0000fixed" ? { fixed: fixed.value } : select.value ? { column: select.value } : {};
      };
      select.addEventListener("change", update);
      fixed.addEventListener("input", update);
      form.append(one.node, fixedField.node);
      return { column, one, select };
    });
    form.append(nav({ next: true }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const problems = [];
      for (const f of fields) {
        f.one.clear();
        if (!f.select.value) {
          const text = t("{name} has no data column. Choose one, or the same value for all.", { name: f.column });
          f.one.error(text);
          problems.push({ text, input: f.select });
        }
      }
      errorSummary(summary, problems);
      if (!problems.length) go(2);
    });
    body.append(form);
  }

  async function base64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  // 3. choose -------------------------------------------------------------------------

  function chooseStep() {
    const summary = summaryBox();
    const form = h("form", { novalidate: true }, summary, heading(t("Choose")));
    const counter = h("p", { class: "count", role: "status" });
    const recount = debounce(() => {
      const sel = selection();
      counter.textContent = countText(sel);
    }, 400);
    disposers.push(() => recount.cancel());
    if (st.resume) {
      form.append(
        notice(
          "info",
          h("p", null, t("Continuing: {count} rows left.", { count: num(st.resume.length) })),
          h("button", { type: "button", onclick: () => {
            st.resume = null;
            draw();
          } }, t("Choose again")),
        ),
      );
    } else if (!numbers()) {
      const name = uid("mode");
      const modes = [
        ["all", t("All rows")],
        ["pick", t("Some rows")],
        ["ranges", t("Ranges of a field")],
      ];
      form.append(
        h(
          "fieldset",
          { class: "choices" },
          h("legend", null, t("Which rows?")),
          modes.map(([value, text]) => {
            const input = h("input", { type: "radio", name, value, id: uid("m"), checked: st.mode === value });
            input.addEventListener("change", () => {
              st.mode = value;
              draw();
            });
            return h("div", { class: "check" }, input, h("label", { for: input.id }, text));
          }),
        ),
      );
    }
    const checks = [];
    if (!st.resume) {
      if (numbers() || st.mode === "ranges") checks.push(...rangeBlocks(form, recount));
      else if (st.mode === "pick") rowPicker(form, recount);
    }
    form.append(counter, nav({ next: true }));
    counter.textContent = countText(selection());
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const problems = checks.flatMap((check) => check());
      const sel = selection();
      if (!problems.length && !sel.ok) problems.push({ text: sel.text, input: form.querySelector("input") });
      if (!problems.length && sel.count === 0) problems.push({ text: t("No row selected."), input: form.querySelector("input") });
      errorSummary(summary, problems.filter((p) => p.input));
      if (!problems.length) go(3);
    });
    body.append(form);
  }

  // From, To and Step for each field; "3000-5685" typed or pasted into From
  // is split. Returns the checks run on Next.
  function rangeBlocks(form, recount) {
    const rule = numbers()
      ? t("Several fields: their values are put together row by row (the first with the first), never every combination.")
      : t("Several fields: a row is printed if its values lie in the ranges of all of them.");
    form.append(h("p", { class: "hint" }, rule));
    const checks = [];
    for (const column of columns) {
      if (!numbers() && st.mapping[column]?.column === undefined) continue; // a fixed value
      const ranges = (st.ranges[column] ??= [{ from: "", to: "", step: "" }]);
      const set = h("fieldset", { class: "range-field" }, h("legend", null, column));
      const list = h("div");
      const rows = [];
      const drawRanges = () => {
        list.replaceChildren();
        rows.length = 0;
        ranges.forEach((range, index) => {
          const make = (key, label, hint, optional) => {
            const input = h("input", { type: "text", inputmode: key === "step" ? "numeric" : "text", value: range[key] ?? "", autocomplete: "off" });
            const one = field({ label, hint, input, optional });
            input.addEventListener("input", () => {
              range[key] = input.value;
              if (key === "from") {
                const split = splitRange(input.value);
                if (split && !range.to) {
                  [range.from, range.to] = split;
                  input.value = range.from;
                  rows[index].to.input.value = range.to;
                }
              }
              recount();
            });
            input.addEventListener("blur", () => validate(index));
            return one;
          };
          const from = make("from", t("From"), index === 0 ? t("For example 3000, 00300 or INV-0001; or the whole range, 3000-5685") : null);
          const to = make("to", t("To"));
          const step = make("step", t("Step"), null, true);
          const remove = ranges.length > 1 && h("button", { type: "button", class: "link", onclick: () => {
            ranges.splice(index, 1);
            drawRanges();
            recount();
          } }, t("Remove range"));
          rows.push({ from, to, step });
          list.append(h("div", { class: "range" }, from.node, to.node, step.node, remove));
        });
      };
      const validate = (index) => {
        const { from, to, step } = rows[index];
        [from, to, step].forEach((f) => f.clear());
        const range = ranges[index];
        if (!range.from && !range.to) return null;
        const checked = checkFields([{ column, ranges: [range] }]);
        if (checked.ok) return null;
        const target = { from, to, step }[checked.field];
        const text = rangeText(checked);
        target.error(text);
        return { text: `${column}: ${text}`, input: target.input };
      };
      drawRanges();
      const add = h("button", { type: "button", onclick: () => {
        ranges.push({ from: "", to: "", step: "" });
        drawRanges();
        rows[rows.length - 1].from.input.focus();
      } }, t("Add a range"));
      set.append(list, add);
      if (numbers()) {
        const fixed = h("input", { type: "text", value: st.fixed[column] ?? "", autocomplete: "off" });
        fixed.addEventListener("input", () => {
          st.fixed[column] = fixed.value;
          recount();
        });
        set.append(field({ label: t("Or the same value for all"), hint: t("Used if no range is given for this field."), input: fixed, optional: true }).node);
      }
      form.append(set);
      checks.push(() => ranges.map((_, i) => validate(i)).filter(Boolean));
    }
    return checks;
  }

  // a table of the rows with checkboxes (a page at a time) and the same as
  // text: "1-20, 25"
  function rowPicker(form, recount) {
    const total = st.data.rows.length;
    const list = h("input", { type: "text", value: rowListText([...st.picked].sort((a, b) => a - b)), autocomplete: "off" });
    const listField = field({ label: t("Rows"), hint: t("For example 1-20, 25, 30-"), input: list });
    const all = h("input", { type: "checkbox", id: uid("all"), "aria-label": t("All rows") });
    const pageText = h("p", { class: "hint", "aria-live": "polite" });
    const holder = h("div");
    let page = 0;
    let last = null;
    const names = st.data.columns.slice(0, 8);
    const sync = () => {
      all.checked = st.picked.size === total && total > 0;
      all.indeterminate = st.picked.size > 0 && st.picked.size < total;
      list.value = rowListText([...st.picked].sort((a, b) => a - b));
      for (const box of holder.querySelectorAll("tbody input")) box.checked = st.picked.has(Number(box.dataset.row));
      recount();
    };
    const drawPage = () => {
      const first = page * PAGE;
      const rows = st.data.rows.slice(first, first + PAGE);
      const tableNode = h(
        "div",
        { class: "table-scroll", tabindex: "0", role: "region", "aria-label": t("Rows") },
        h(
          "table",
          null,
          h("caption", null, t("Rows {first} to {last} of {total}", { first: num(first + 1), last: num(first + rows.length), total: num(total) })),
          h("thead", null, h("tr", null, h("th", { scope: "col" }, all), h("th", { scope: "col" }, t("Row")), names.map((n) => h("th", { scope: "col" }, n)))),
          h(
            "tbody",
            null,
            rows.map((row, i) => {
              const index = first + i;
              const box = h("input", { type: "checkbox", "data-row": index, checked: st.picked.has(index), "aria-label": t("Row {n}", { n: num(index + 1) }) });
              box.addEventListener("click", (event) => {
                if (event.shiftKey && last !== null) {
                  const [a, b] = [Math.min(last, index), Math.max(last, index)];
                  for (let n = a; n <= b; n++) box.checked ? st.picked.add(n) : st.picked.delete(n);
                } else if (box.checked) st.picked.add(index);
                else st.picked.delete(index);
                last = index;
                sync();
              });
              box.addEventListener("keydown", (event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                const boxes = [...holder.querySelectorAll("tbody input")];
                const next = boxes[boxes.indexOf(box) + (event.key === "ArrowDown" ? 1 : -1)];
                if (!next) return;
                event.preventDefault();
                if (event.shiftKey) {
                  const n = Number(next.dataset.row);
                  box.checked ? st.picked.add(n) : st.picked.delete(n);
                  sync();
                }
                next.focus();
              });
              return h("tr", null, h("td", null, box), h("th", { scope: "row" }, num(index + 1)), names.map((n) => h("td", null, row[n] ?? "")));
            }),
          ),
        ),
      );
      const pages = Math.ceil(total / PAGE);
      pageText.textContent = t("Page {page} of {pages}", { page: num(page + 1), pages: num(pages) });
      holder.replaceChildren(
        tableNode,
        h(
          "div",
          { class: "actions" },
          h("button", { type: "button", disabled: page === 0, onclick: () => ((page -= 1), drawPage()) }, t("Previous page")),
          h("button", { type: "button", disabled: page >= pages - 1, onclick: () => ((page += 1), drawPage()) }, t("Next page")),
        ),
        pageText,
      );
      sync();
    };
    all.addEventListener("change", () => {
      st.picked = all.checked ? new Set(st.data.rows.map((_, i) => i)) : new Set();
      sync();
    });
    list.addEventListener("change", () => {
      listField.clear();
      const picked = list.value.trim() ? parseRowList(list.value, total) : [];
      if (picked === null) {
        listField.error(t("Write the rows as numbers and ranges, such as 1-20, 25."));
        return;
      }
      st.picked = new Set(picked);
      sync();
    });
    form.append(listField.node, holder);
    drawPage();
  }

  // 4. check ----------------------------------------------------------------------------

  function checkStep() {
    const summary = summaryBox();
    const sel = selection();
    const form = h("form", { novalidate: true }, summary, heading(t("Check")));
    if (!sel.ok || !sel.count) {
      form.append(notice("warn", h("p", null, sel.ok ? t("No row selected.") : sel.text)), nav({}));
      body.append(form);
      return;
    }
    const refresh = debounce(() => previews(), 400);
    const picker = printerPicker(printers, label, () => {
      st.printer = picker.value();
      refresh();
      update();
    });
    if (st.printer && printers) {
      const select = picker.node.querySelector("select");
      if (select && [...select.options].some((o) => o.value === st.printer)) select.value = st.printer;
    }
    st.printer = picker.value();
    const copiesInput = h("input", { type: "text", inputmode: "numeric", value: st.copies, autocomplete: "off" });
    const copiesField = field({ label: t("Copies of each label"), input: copiesInput });
    const variables = labelFields(label)
      .filter((f) => f.kind === "variable")
      .map((spec) => {
        const input = h("input", { type: "text", value: st.variables[spec.name] ?? "", autocomplete: "off" });
        input.addEventListener("input", () => {
          st.variables[spec.name] = input.value;
          refresh();
        });
        return field({ label: spec.name, hint: spec.hint, input, optional: true });
      });
    const counter = h("p", { class: "count", role: "status" });
    const limits = h("p", { class: "hint" });
    const problem = h("div");
    const first = previewPane(label, "");
    const last = previewPane(label, "");
    disposers.push(() => {
      first.dispose();
      last.dispose();
      refresh.cancel();
    });
    const warnings = h("div");
    let warningState = warningsBox([]);
    const button = h("button", { type: "submit", class: "primary" });
    const reason = h("p", { class: "hint", id: uid("reason") });
    form.append(
      ...[
        picker.node,
        copiesField.node,
        variables.length && h("fieldset", null, h("legend", null, t("The same for all labels")), variables.map((v) => v.node)),
        counter,
        limits,
        problem,
        h("div", { class: "pair" }, first.node, last.node),
        warnings,
        h("div", { class: "actions" }, h("button", { type: "button", onclick: () => go(2) }, t("Back")), button),
        reason,
      ].filter(Boolean),
    );
    body.append(form);

    function total() {
      return sel.count * (copies() || 0);
    }

    function blocked() {
      if (!session.may("print")) return t("Printing needs a token with the role Print or Admin.");
      const n = copies();
      if (!(n >= 1)) return t("Copies must be a whole number from 1.");
      if (n > picker.maxCopies()) return t("At most {max} copies on this printer.", { max: num(picker.maxCopies()) });
      if (sel.count > session.me.limits.max_rows) return t("At most {max} rows in one print job.", { max: num(session.me.limits.max_rows) });
      if (total() > limit()) return t("At most {max} labels in one print job. Choose fewer rows, or print them in several jobs.", { max: num(limit()) });
      return picker.problem() ?? (warningState.confirmed() ? null : t("Confirm above that it should print on the labels loaded."));
    }

    function update() {
      st.copies = copiesInput.value;
      counter.textContent = countText(sel);
      const n = total();
      limits.textContent = t("At most {max} labels in one print job; {left} more could be added.", { max: num(limit()), left: num(Math.max(0, limit() - n)) });
      button.textContent = t("Print {count} labels", { count: num(n || 0) });
      const why = blocked();
      button.setAttribute("aria-disabled", why ? "true" : "false");
      reason.textContent = why ?? "";
      if (why) button.setAttribute("aria-describedby", reason.id);
      else button.removeAttribute("aria-describedby");
      return why;
    }

    function request(row) {
      const out = { label: name, data_rows: [row] };
      if (st.printer) out.printer = st.printer;
      const set = Object.fromEntries(Object.entries(st.variables).filter(([, v]) => v !== ""));
      if (Object.keys(set).length) out.set_literal = set;
      return out;
    }

    async function previews() {
      const a = sel.row(0);
      const b = sel.row(sel.count - 1);
      const firstName = shown(a, sel, 0);
      const lastName = shown(b, sel, sel.count - 1);
      first.node.querySelector("figcaption").textContent = t("First: {value}", { value: firstName });
      last.node.querySelector("figcaption").textContent = t("Last: {value}", { value: lastName });
      try {
        const [one, two] = await Promise.all([
          first.load(request(a), t("Preview of the label {value}", { value: firstName })),
          sel.count > 1 ? last.load(request(b), t("Preview of the label {value}", { value: lastName })) : null,
        ]);
        last.node.hidden = sel.count <= 1;
        const seen = new Map();
        for (const w of [...(one?.warnings ?? []), ...(two?.warnings ?? [])]) seen.set(`${w.code}|${w.message}`, w);
        warningState = warningsBox([...seen.values()]);
        warnings.replaceChildren(warningState.node);
        warningState.check?.addEventListener("change", update);
      } catch {
        warnings.replaceChildren();
      }
      update();
    }

    copiesInput.addEventListener("input", () => {
      copiesField.clear();
      update();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const why = update();
      if (why) {
        errorSummary(summary, [{ text: why, input: copies() >= 1 ? button : copiesInput }]);
        return;
      }
      const count = total();
      if (count >= CONFIRM_FROM) {
        const printer = picker.printer()?.name ?? label.printer.name;
        const yes = await confirm({
          title: t("Print {count} labels?", { count: num(count) }),
          lines: [
            countText(sel),
            t("Template: {name}", { name: title(name) }),
            t("Printer: {name}", { name: printer }),
          ],
          action: t("Print {count} labels", { count: num(count) }),
        });
        if (!yes) return;
      }
      const rows = sel.rows();
      const payload = { ...request(rows[0]), data_rows: rows, copies: copies() };
      const size = new TextEncoder().encode(JSON.stringify(payload)).length;
      if (size > session.me.limits.max_body) {
        errorSummary(summary, [{ text: t("Too much data for one print job ({size} MB). Choose fewer rows.", { size: num(Math.ceil(size / 1048576)) }), input: button }]);
        return;
      }
      button.disabled = true;
      try {
        const job = await api("POST", "/print", payload);
        st.job = { id: job.id, rows, job, sel: { ranged: sel.ranged } };
        st.ended = null;
        go(4);
      } catch (error) {
        errorSummary(summary, [{ text: errorText(error), input: button }]);
      } finally {
        button.disabled = false;
      }
    });
    update();
    previews();
  }

  // 5. print: progress, then the result ---------------------------------------------

  function printStep() {
    const job = st.job;
    body.append(heading(t("Print")));
    if (!job) {
      go(3);
      return;
    }
    const end = (ended) => {
      st.ended = ended;
      body.querySelector(".progress")?.remove();
      body.append(result(ended));
      ctx.status(t("Print job ended: {printed} of {total} labels printed.", { printed: num(ended.printed), total: num(ended.total) }));
      body.querySelector(".result h2")?.setAttribute("tabindex", "-1");
      body.querySelector(".result h2")?.focus();
    };
    if (st.ended) {
      body.append(result(st.ended));
      return;
    }
    const progress = jobProgress(job.job, { onEnd: end, status: ctx.status });
    disposers.push(progress.stop);
    body.append(progress.node);
  }

  function result(ended) {
    const rows = st.job.rows;
    const printed = Math.min(ended.rows_printed ?? 0, rows.length);
    const sel = st.job.sel;
    const extra = [];
    if (printed > 0) {
      extra.push(h("p", null, t("First printed: {first}, last printed in full: {last}", { first: shown(rows[0], sel, 0), last: shown(rows[printed - 1], sel, printed - 1) })));
    }
    const actions = h("div", { class: "actions" });
    if (ended.state !== "done" && printed < rows.length) {
      const next = shown(rows[printed], sel, printed);
      actions.append(
        h("button", { type: "button", class: "primary", onclick: () => {
          st.resume = rows.slice(printed);
          st.job = null;
          st.ended = null;
          go(3);
        } }, t("Continue from {value}", { value: next })),
      );
      extra.push(h("p", { class: "hint" }, t("A label of a row that was printed in part is printed again in full.")));
    }
    actions.append(
      h("button", { type: "button", onclick: () => {
        st.job = null;
        st.ended = null;
        st.resume = null;
        go(2);
      } }, t("New series")),
    );
    extra.push(actions);
    return jobResult(ended, extra);
  }

  draw();
}
