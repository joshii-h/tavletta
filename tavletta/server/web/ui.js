// Pieces the views share: fields with their hints and errors, the error
// summary, the confirmation dialog, the preview with the elements' boxes,
// warnings, the progress of a job.

import { api, ApiError, labelPath } from "./api.js";
import { debounce, h, icon, svg, uid } from "./dom.js";
import { mm, num, t, time } from "./i18n.js";

export const CONFIRM_FROM = 100; // labels: printing more asks first
const LOADING_DELAY = 300; // ms before "Loading…" shows

// --- the session's data -------------------------------------------------------------

export const session = {
  me: null, // GET /me
  labels: new Map(), // name -> GET /labels/{name}
  clear() {
    this.me = null;
    this.labels.clear();
  },
  may(role) {
    const order = ["preview", "print", "admin"];
    return this.me !== null && order.indexOf(this.me.role) >= order.indexOf(role);
  },
};

export async function describe(name) {
  if (!session.labels.has(name)) {
    session.labels.set(name, await api("GET", `/labels/${labelPath(name)}`));
  }
  return session.labels.get(name);
}

// The printers a print can choose: those of printers.toml the token may
// use (with their state), or null without printers.toml (the label's
// printer model prints, on the address set on the server).
export async function printerChoice() {
  const { printers } = await api("GET", "/printers?discover=false");
  const named = printers.filter((p) => "max_copies" in p);
  return named.length || !printers.length ? named : null;
}

// --- texts for codes -------------------------------------------------------------------

export function stateText(state) {
  const texts = {
    IDLE: t("Ready"),
    BUSY: t("Busy"),
    PAUSED: t("Paused"),
    ERROR: t("Problem"),
    UNKNOWN: t("Reachable, no status"),
    UNREACHABLE: t("Unreachable"),
  };
  return texts[state] ?? state;
}

export function stateIcon(state) {
  return { IDLE: "ok", BUSY: "busy", UNKNOWN: "info", PAUSED: "warn" }[state] ?? "error";
}

export function roleText(role) {
  return { preview: t("Preview"), print: t("Print"), admin: t("Admin") }[role] ?? role;
}

export function kindText(kind) {
  return { text: t("Text"), code: t("Code"), image: t("Image") }[kind] ?? t("Element");
}

// what an error answer means for people; the server's message is translated
export function errorText(error) {
  if (!(error instanceof ApiError)) return t("Something went wrong in the page.");
  if (error.status === 403 && error.code === "forbidden") return t("This needs a token with more rights.");
  if (error.status === 429 && error.code === "too_many_requests") {
    return t("Too many wrong tokens from this computer. Wait a minute, then sign in again.");
  }
  return error.message;
}

export function media(info) {
  const parts = [];
  const m = info?.media ?? {};
  if (m.label_mm) parts.push(t("Labels loaded: {width} × {height} mm", { width: mm(m.label_mm[0]), height: mm(m.label_mm[1]) }));
  if (m.tape_mm) parts.push(t("Tape loaded: {width} mm", { width: mm(m.tape_mm) }));
  if (m.pitch_mm && !m.label_mm) parts.push(t("Label distance measured: {length} mm", { length: mm(m.pitch_mm) }));
  if (m.remaining_mm != null) parts.push(t("Left on the roll: {length} mm", { length: num(m.remaining_mm) }));
  return parts;
}

export function printerState(printer, checked) {
  const state = printer.status?.state ?? "UNKNOWN";
  const problem = printer.status?.error?.message;
  const text = state === "UNREACHABLE" ? t("Unreachable (checked {time})", { time: time(checked) }) : stateText(state);
  return h(
    "div",
    { class: `state state-${stateIcon(state)}` },
    h("p", { class: "state-line" }, icon(stateIcon(state)), " ", text),
    problem && h("p", { class: "hint" }, problem),
  );
}

// --- loading, errors ---------------------------------------------------------------------

// "Loading…" with aria-busy, shown only if it takes longer than 300 ms
export function loading(container) {
  container.setAttribute("aria-busy", "true");
  const note = h("p", { class: "loading", hidden: true }, t("Loading…"));
  container.append(note);
  const timer = setTimeout(() => (note.hidden = false), LOADING_DELAY);
  return () => {
    clearTimeout(timer);
    note.remove();
    container.removeAttribute("aria-busy");
  };
}

// A banner with the cause, "Try again", and the details folded
export function errorBanner(error, retry) {
  const banner = h(
    "div",
    { class: "banner banner-error", role: "alert" },
    icon("error"),
    h("div", null, h("p", null, errorText(error)), retry && h("button", { type: "button", onclick: retry }, t("Try again"))),
  );
  if (error instanceof ApiError && error.code) {
    banner.lastChild.append(h("details", null, h("summary", null, t("Details")), h("code", null, `${error.status} ${error.code}`)));
  }
  return banner;
}

export function notice(kind, ...content) {
  return h("div", { class: `banner banner-${kind}` }, icon(kind === "warn" ? "warn" : "info"), h("div", null, ...content));
}

// --- fields --------------------------------------------------------------------------

// A labelled input with an optional hint; field.error(text) shows an error
// under the label, before the input (GOV.UK), field.clear() removes it.
export function field({ label, hint, input, optional = false }) {
  const id = input.id || uid("f");
  input.id = id;
  const hintId = hint ? `${id}-hint` : null;
  const errorId = `${id}-error`;
  const error = h("p", { id: errorId, class: "field-error", hidden: true });
  const described = () => [hintId, error.hidden ? null : errorId].filter(Boolean).join(" ");
  if (hintId) input.setAttribute("aria-describedby", hintId);
  const wrap = h(
    "div",
    { class: "field" },
    h("label", { for: id }, label, optional && h("span", { class: "optional" }, ` (${t("optional")})`)),
    hint && h("p", { id: hintId, class: "hint" }, hint),
    error,
    input,
  );
  return {
    node: wrap,
    input,
    error(text) {
      error.textContent = text;
      error.hidden = false;
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", described());
      wrap.classList.add("invalid");
    },
    clear() {
      error.hidden = true;
      input.removeAttribute("aria-invalid");
      const ids = described();
      if (ids) input.setAttribute("aria-describedby", ids);
      else input.removeAttribute("aria-describedby");
      wrap.classList.remove("invalid");
    },
  };
}

// The list of problems above a form, each a link to its field; focused.
export function errorSummary(container, problems) {
  container.replaceChildren();
  container.hidden = !problems.length;
  if (!problems.length) return;
  container.append(
    h("h2", null, t("There is a problem")),
    h(
      "ul",
      null,
      problems.map(({ text, input }) =>
        h(
          "li",
          null,
          h(
            "a",
            {
              href: `#${input.id}`,
              onclick: (event) => {
                event.preventDefault();
                input.focus();
              },
            },
            text,
          ),
        ),
      ),
    ),
  );
  container.focus();
}

export function summaryBox() {
  return h("div", { class: "error-summary", tabindex: "-1", role: "alert", hidden: true });
}

// --- the confirmation dialog -------------------------------------------------------------

// A modal dialog; resolves true if `action` (the button naming what
// happens) was chosen. "Cancel" has the focus; Esc closes.
export function confirm({ title, lines, action, danger = false }) {
  const dialog = document.getElementById("dialog");
  const opener = document.activeElement;
  const titleId = uid("dialog-title");
  return new Promise((resolve) => {
    const cancel = h("button", { type: "button", value: "cancel" }, t("Cancel"));
    const ok = h("button", { type: "button", class: danger ? "danger" : "primary", value: "ok" }, action);
    dialog.replaceChildren(
      h("h2", { id: titleId }, title),
      ...lines.map((line) => h("p", null, line)),
      h("div", { class: "actions" }, cancel, ok),
    );
    dialog.setAttribute("aria-labelledby", titleId);
    const listening = new AbortController(); // Esc of this dialog only
    const close = (result) => {
      listening.abort();
      dialog.close();
      dialog.replaceChildren();
      opener?.focus?.();
      resolve(result);
    };
    cancel.addEventListener("click", () => close(false));
    ok.addEventListener("click", () => close(true));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close(false);
    }, { signal: listening.signal });
    dialog.showModal();
    cancel.focus();
  });
}

// --- warnings --------------------------------------------------------------------------

// Warnings of a preview; other_labels_loaded needs a confirmation before
// printing: returns {node, confirmed()}.
export function warningsBox(warnings) {
  const box = h("div", { class: "warnings" });
  let check = null;
  for (const warning of warnings) {
    if (warning.code === "other_labels_loaded") {
      check = h("input", { type: "checkbox", id: uid("anyway") });
      box.append(
        notice(
          "warn",
          h("p", null, t("Other labels are loaded in the printer than the template needs.")),
          h("p", null, warning.message),
          h("div", { class: "check" }, check, h("label", { for: check.id }, t("Print anyway"))),
        ),
      );
    } else {
      box.append(notice("warn", h("p", null, warning.message)));
    }
  }
  return { node: box, confirmed: () => check === null || check.checked, needsConfirmation: check !== null, check };
}

// --- the preview with the elements' boxes ------------------------------------------------

// The fields of a label and the elements each fills: a column {Name}, a
// variable {=name}, or the element's name.
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

// A preview image with an outline over each element; hovering or focusing
// one names it, highlight(indexes) marks the elements a field fills.
export function previewPane(label, caption) {
  const figure = h("figure", { class: "preview" });
  const frame = h("div", { class: "preview-frame" });
  const note = h("p", { class: "preview-note", "aria-live": "polite" });
  const warnings = h("div");
  figure.append(frame, h("figcaption", null, caption ?? ""), note);
  let url = null;
  let boxes = [];
  let lit = []; // the elements highlighted, kept over a new image
  let request = null;
  let generation = 0;

  function highlight(indexes) {
    lit = indexes;
    for (const [i, box] of boxes.entries()) box.classList.toggle("on", indexes.includes(i));
  }

  function show(preview, alt) {
    if (url) URL.revokeObjectURL(url);
    const bytes = Uint8Array.from(atob(preview.png), (c) => c.charCodeAt(0));
    url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
    const img = h("img", { src: url, alt, width: preview.width, height: preview.height });
    const stack = h("div", { class: "stack" }, img);
    frame.replaceChildren(stack);
    boxes = [];
    const geometry = preview.geometry;
    if (!geometry) return;
    const scale = geometry.dpi / 25.4;
    const overlay = svg("svg", {
      class: "boxes",
      viewBox: `0 0 ${preview.width / scale} ${preview.height / scale}`,
      preserveAspectRatio: "none",
      "aria-hidden": "true",
    });
    for (const element of geometry.elements) {
      const [left, top, right, bottom] = element.box;
      const info = label.elements[element.index] ?? {};
      const name = t("Element {n}: {kind}", { n: element.index + 1, kind: kindText(element.kind) }) + (info.name ? ` (${info.name})` : "");
      const rect = svg("rect", { x: left, y: top, width: Math.max(right - left, 0.3), height: Math.max(bottom - top, 0.3) }, svg("title", null, name));
      rect.addEventListener("pointerenter", () => {
        highlight([element.index]);
        note.textContent = name;
      });
      rect.addEventListener("pointerleave", () => highlight([]));
      overlay.append(rect);
      boxes.push(rect);
    }
    stack.append(overlay);
    highlight(lit);
  }

  // POST /preview as JSON (image, warnings, geometry); stale answers are dropped
  async function load(body, alt) {
    generation += 1;
    const mine = generation;
    request?.abort();
    request = new AbortController();
    figure.setAttribute("aria-busy", "true");
    try {
      const preview = await api("POST", "/preview", { ...body, format: "json" }, { signal: request.signal });
      if (mine !== generation) return null;
      show(preview, alt);
      return preview;
    } catch (error) {
      if (error.name === "AbortError" || mine !== generation) return null;
      frame.replaceChildren(errorBanner(error));
      throw error;
    } finally {
      if (mine === generation) figure.removeAttribute("aria-busy");
    }
  }

  return {
    node: figure,
    warnings,
    load,
    highlight,
    dispose() {
      request?.abort();
      if (url) URL.revokeObjectURL(url);
    },
  };
}

// --- a job's progress ---------------------------------------------------------------------

const POLL = 1000; // ms between two looks at a job
const ANNOUNCE = 5000; // ms between two announcements of the progress

// Follows a job until it ends: a progress bar, "1'204 of 2'686", the time
// left once it can be told, and Cancel. onEnd(job) when it is done, failed
// or canceled.
export function jobProgress(job, { onEnd, status }) {
  const bar = h("progress", { max: job.total, value: job.printed });
  const count = h("p", { class: "count" });
  const left = h("p", { class: "hint" });
  const cancel = h("button", { type: "button", class: "danger-outline" }, t("Cancel printing"));
  const cancelHint = h("p", { class: "hint", id: uid("cancel-hint") }, t("Labels already printed stay printed."));
  cancel.setAttribute("aria-describedby", cancelHint.id);
  const headingId = uid("progress");
  const node = h("section", { class: "progress", "aria-labelledby": headingId }, h("h2", { id: headingId }, t("Printing")), bar, count, left, cancel, cancelHint);
  let started = null;
  let announced = 0;
  let stopped = false;
  let timer = 0;

  function update(current) {
    bar.max = current.total;
    bar.value = current.printed;
    const text = t("{printed} of {total}", { printed: num(current.printed), total: num(current.total) });
    count.textContent = text;
    bar.setAttribute("aria-valuetext", text);
    if (current.state === "printing" && started === null) started = Date.now();
    const elapsed = started ? (Date.now() - started) / 1000 : 0;
    // a time left only once it can be told: after 10 s and 20 labels
    if (elapsed > 10 && current.printed >= 20 && current.printed < current.total) {
      const seconds = ((current.total - current.printed) * elapsed) / current.printed;
      const minutes = Math.max(1, Math.round(seconds / 60));
      left.textContent = t("About {minutes} min left", { minutes: num(minutes) });
    } else {
      left.textContent = current.state === "queued" || current.state === "waiting" ? t("Waiting for the printer…") : "";
    }
    if (Date.now() - announced > ANNOUNCE) {
      announced = Date.now();
      status(text);
    }
  }

  async function poll() {
    try {
      const current = await api("GET", `/jobs/${encodeURIComponent(job.id)}`);
      update(current);
      if (["done", "failed", "canceled"].includes(current.state)) {
        stopped = true;
        onEnd(current);
        return;
      }
    } catch (error) {
      if (error.status === 401 || stopped) return;
      left.textContent = errorText(error);
    }
    if (!stopped) timer = setTimeout(poll, POLL);
  }

  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    try {
      await api("DELETE", `/jobs/${encodeURIComponent(job.id)}`);
      left.textContent = t("Stopping after the label being printed…");
    } catch (error) {
      if (error.code !== "job_finished") left.textContent = errorText(error);
    }
  });

  update(job);
  timer = setTimeout(poll, 300);
  return {
    node,
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
  };
}

// The result of a job: what happened, how many, how long, its id to copy.
export function jobResult(job, extra = []) {
  const kinds = { done: ["ok", t("Printed")], canceled: ["warn", t("Canceled")], failed: ["error", t("Failed")] };
  const [kind, title] = kinds[job.state] ?? ["info", job.state];
  const seconds = job.started && job.finished ? (new Date(job.finished) - new Date(job.started)) / 1000 : null;
  const idInput = h("input", { type: "text", readonly: true, value: job.id, class: "job-id", id: uid("job"), dir: "ltr" });
  const copy = h(
    "button",
    {
      type: "button",
      onclick: async () => {
        idInput.select();
        try {
          await navigator.clipboard.writeText(job.id);
          copy.textContent = t("Copied");
        } catch {
          document.execCommand?.("copy");
        }
      },
    },
    t("Copy"),
  );
  return h(
    "section",
    { class: `result result-${kind}` },
    h("h2", null, icon(kind), " ", title),
    h("p", null, t("{printed} of {total} labels printed.", { printed: num(job.printed), total: num(job.total) })),
    job.error && h("p", { class: "error-text" }, job.error.message),
    job.remark && h("p", null, job.remark),
    seconds !== null && h("p", null, t("Took {seconds} s, finished at {time}.", { seconds: num(Math.round(seconds)), time: time(job.finished) })),
    ...extra,
    h("div", { class: "field" }, h("label", { for: idInput.id }, t("Job number")), h("div", { class: "row" }, idInput, copy)),
  );
}

export { debounce, mm };
