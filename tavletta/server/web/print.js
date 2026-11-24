// One print: the template's fields filled in, a live preview whose
// elements light up with the field that fills them, the printer, copies.

import { api, labelPath } from "./api.js";
import { debounce, h } from "./dom.js";
import { num, t } from "./i18n.js";
import {
  CONFIRM_FROM,
  confirm,
  describe,
  elementsOf,
  errorBanner,
  errorSummary,
  errorText,
  field,
  loading,
  previewPane,
  printerChoice,
  printerState,
  session,
  summaryBox,
  warningsBox,
} from "./ui.js";

// what was entered for each template, kept while the tab is open: also
// after signing in again (a token expired) or going elsewhere and back
const drafts = new Map();

export function forgetDrafts() {
  drafts.clear();
}

export function title(name) {
  return name.replace(/\.tavletta$/, "");
}

export function backLink() {
  return h("p", { class: "back" }, h("a", { href: "#/templates" }, t("Back to the templates")));
}

// The printer to print on: a choice of the printers of printers.toml, with
// the state of the one chosen. node, value(), printer(), maxCopies(),
// usable(): false (with the reason) while it cannot print.
export function printerPicker(printers, label, onChange) {
  if (printers === null) {
    return {
      node: h("p", null, t("Printer: {model}, at the address set on the server", { model: label.printer.name })),
      value: () => undefined,
      printer: () => null,
      maxCopies: () => session.me.limits.max_copies,
      problem: () => null,
    };
  }
  const select = h(
    "select",
    { onchange: () => {
      showState();
      onChange();
    } },
    printers.map((p) =>
      h("option", { value: p.name, selected: p.default }, p.description ? `${p.name} – ${p.description}` : p.name),
    ),
  );
  const state = h("div", { "aria-live": "polite" });
  const chosen = () => printers.find((p) => p.name === select.value) ?? null;
  const showState = () => {
    const p = chosen();
    state.replaceChildren(p ? printerState(p, new Date().toISOString()) : "");
  };
  showState();
  const picker = field({ label: t("Printer"), input: select });
  picker.node.append(state);
  return {
    node: printers.length ? picker.node : h("p", { class: "error-text" }, t("No printer this token may use. Ask the administrator.")),
    value: () => select.value || undefined,
    printer: chosen,
    maxCopies: () => Math.min(chosen()?.max_copies ?? Infinity, session.me.limits.max_copies),
    problem() {
      const p = chosen();
      if (!p) return t("No printer chosen.");
      return p.status?.state === "UNREACHABLE" ? t("The printer is unreachable. Check that it is on and connected, then reload the page.") : null;
    },
  };
}

// The fields of a label: its data columns, variables and named elements.
export function labelFields(label) {
  const fields = [];
  for (const name of label.columns) {
    fields.push({ kind: "column", name, hint: t("Data field {placeholder}", { placeholder: `{${name}}` }), value: "" });
  }
  for (const name of label.variables) {
    fields.push({ kind: "variable", name, hint: t("Variable {placeholder}", { placeholder: `{=${name}}` }), value: "", optional: true });
  }
  for (const element of label.elements) {
    if (!element.name || label.columns.includes(element.name) || label.variables.includes(element.name)) continue;
    const value = element.text ?? element.data ?? "";
    fields.push({ kind: "element", name: element.name, hint: t("Text of the element; changed here for this print only"), value, original: value, optional: true });
  }
  return fields;
}

export async function printView(name, ctx) {
  const area = h("div");
  ctx.mount(t("Print {name}", { name: title(name) }), backLink(), area);
  const done = loading(area);
  let label;
  let printers;
  try {
    [label, printers] = await Promise.all([describe(name), printerChoice()]);
  } catch (error) {
    done();
    if (ctx.live()) area.append(errorBanner(error, () => printView(name, ctx)));
    return;
  }
  done();
  if (!ctx.live()) return;

  if (!drafts.has(name)) drafts.set(name, {});
  const draft = drafts.get(name);
  const pane = previewPane(label, t("Preview"));
  ctx.onDispose(() => pane.dispose());
  const refresh = debounce(() => showPreview(), 400);
  const picker = printerPicker(printers, label, refresh);
  const summary = summaryBox();
  const inputs = labelFields(label).map((spec) => {
    const key = `${spec.kind}:${spec.name}`;
    const input = h("input", { type: "text", value: draft[key] ?? spec.value, autocomplete: "off" });
    input.addEventListener("input", () => (draft[key] = input.value));
    const one = field({ label: spec.name, hint: spec.hint, input, optional: spec.optional });
    const indexes = elementsOf(label, spec.kind, spec.name);
    const light = () => pane.highlight(indexes);
    const dark = () => pane.highlight([]);
    input.addEventListener("focus", light);
    input.addEventListener("blur", dark);
    one.node.addEventListener("pointerenter", light);
    one.node.addEventListener("pointerleave", () => document.activeElement !== input && dark());
    input.addEventListener("input", refresh);
    return { ...spec, ...one };
  });
  let counter = null;
  if (label.counter.used) {
    const input = h("input", { type: "text", inputmode: "numeric", value: draft.counter ?? String(label.counter.next), autocomplete: "off" });
    input.addEventListener("input", () => (draft.counter = input.value));
    counter = field({ label: t("Start number"), hint: t("The counter {placeholder} starts here; next number saved in the template: {next}", { placeholder: "{=counter}", next: num(label.counter.next) }), input });
    input.addEventListener("input", refresh);
  }
  const copiesInput = h("input", { type: "text", inputmode: "numeric", value: draft.copies ?? "1", autocomplete: "off" });
  copiesInput.addEventListener("input", () => (draft.copies = copiesInput.value));
  const copies = field({ label: t("Copies"), hint: t("At most {max}", { max: num(picker.maxCopies()) }), input: copiesInput });
  const printButton = h("button", { type: "submit", class: "primary" }, t("Print"));
  const reason = h("p", { class: "hint", id: "print-reason" });
  const warnings = h("div");
  let warningState = warningsBox([]);
  const form = h(
    "form",
    { class: "form", novalidate: true },
    summary,
    picker.node,
    inputs.length ? h("fieldset", null, h("legend", null, t("Fields")), inputs.map((f) => f.node)) : h("p", null, t("This template has no fields to fill in.")),
    counter?.node,
    copies.node,
    warnings,
    h("div", { class: "actions" }, printButton),
    reason,
  );
  area.append(h("div", { class: "split" }, form, pane.node));

  function body() {
    const request = { label: name };
    const printer = picker.value();
    if (printer) request.printer = printer;
    const set = {};
    for (const f of inputs) {
      if (f.kind === "variable" && f.input.value !== "") set[f.name] = f.input.value;
      if (f.kind === "element" && f.input.value !== f.original) set[f.name] = f.input.value;
    }
    if (Object.keys(set).length) request.set_literal = set;
    const columns = inputs.filter((f) => f.kind === "column");
    if (columns.length) request.data_rows = [Object.fromEntries(columns.map((f) => [f.name, f.input.value]))];
    const start = counter?.input.value.trim();
    if (start && /^\d+$/.test(start)) request.counter_start = Number(start);
    return request;
  }

  async function showPreview() {
    try {
      const preview = await pane.load(body(), t("Preview of {name}", { name: title(name) }));
      if (!preview) return;
      warningState = warningsBox(preview.warnings);
      warnings.replaceChildren(warningState.node);
      warningState.check?.addEventListener("change", gate);
    } catch {
      warnings.replaceChildren();
    } finally {
      gate();
    }
  }

  // whether it can print; else the button stays focusable, with the reason
  function gate() {
    let why = null;
    if (!session.may("print")) why = t("Printing needs a token with the role Print or Admin.");
    else why = picker.problem() ?? (warningState.confirmed() ? null : t("Confirm above that it should print on the labels loaded."));
    printButton.setAttribute("aria-disabled", why ? "true" : "false");
    reason.textContent = why ?? "";
    if (why) printButton.setAttribute("aria-describedby", reason.id);
    else printButton.removeAttribute("aria-describedby");
    return why === null;
  }

  function check() {
    const problems = [];
    for (const f of [...inputs, counter, copies]) f?.clear();
    for (const f of inputs) {
      if (f.kind === "column" && !f.input.value.trim()) problems.push([f, t("{name} is empty. Enter a value.", { name: f.name })]);
    }
    const start = counter?.input.value.trim();
    if (counter && !/^\d+$/.test(start ?? "")) problems.push([counter, t("The start number must be a whole number from 0.")]);
    const count = copiesInput.value.trim().replace(/['’\s]/g, "");
    const max = picker.maxCopies();
    if (!/^\d+$/.test(count) || Number(count) < 1) problems.push([copies, t("Copies must be a whole number from 1.")]);
    else if (Number(count) > max) problems.push([copies, t("At most {max} copies on this printer.", { max: num(max) })]);
    else if (Number(count) > session.me.limits.max_labels) problems.push([copies, t("At most {max} labels in one print job.", { max: num(session.me.limits.max_labels) })]);
    for (const [f, text] of problems) f.error(text);
    errorSummary(summary, problems.map(([f, text]) => ({ text, input: f.input })));
    return problems.length ? null : Number(count);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!gate()) {
      reason.focus?.();
      return;
    }
    const count = check();
    if (count === null) return;
    const request = { ...body(), copies: count };
    if (count >= CONFIRM_FROM) {
      const printer = picker.printer()?.name ?? label.printer.name;
      const yes = await confirm({
        title: t("Print {count} labels?", { count: num(count) }),
        lines: [t("Template: {name}", { name: title(name) }), t("Printer: {name}", { name: printer })],
        action: t("Print {count} labels", { count: num(count) }),
      });
      if (!yes) return;
    }
    printButton.disabled = true;
    try {
      const job = await api("POST", "/print", request);
      location.hash = `#/job/${encodeURIComponent(job.id)}`;
    } catch (error) {
      summary.hidden = false;
      summary.replaceChildren(h("h2", null, t("There is a problem")), h("p", null, errorText(error)));
      summary.focus();
    } finally {
      printButton.disabled = false;
    }
  });
  gate();
  await showPreview();
}

export { labelPath };
