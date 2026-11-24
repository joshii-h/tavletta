// The print server's web page: one page, views by the address's #fragment
// (#/templates, #/printers, #/print/<label>, #/batch/<label>, #/job/<id>).
// Everything shown comes from the REST API with the token of this tab (or
// the session of signing in with OpenID Connect); the server checks every
// request, the page only leaves out what a token may not do.

import { api, endSession, labelPath, loginInfo, token, whenUnauthorized } from "./api.js";
import { batchView, forgetBatches } from "./batch.js";
import { h, icon } from "./dom.js";
import { chooseLanguage, lang, languageName, languages, loadMessages, mm, num, t } from "./i18n.js";
import { forgetDrafts, printView } from "./print.js";
import {
  confirm,
  describe,
  media,
  printerState,
  errorBanner,
  errorText,
  field,
  jobProgress,
  jobResult,
  loading,
  notice,
  roleText,
  session,
  summaryBox,
} from "./ui.js";

const main = () => document.getElementById("main");
let current = { dispose() {} }; // the view shown: what it holds is let go on leaving
let generation = 0; // a view's answers that come after the next view started are dropped
let afterSignIn = null; // the address asked for before signing in
let provider = null; // signing in with OpenID Connect: { provider, token_login }, else null
let signInError = null; // why signing in with the provider failed (#/signin-error/<code>)

// --- the live region ----------------------------------------------------------------

export function status(text) {
  const region = document.getElementById("status");
  region.textContent = "";
  setTimeout(() => (region.textContent = text), 50);
}

// --- the frame: header, navigation, session -------------------------------------------

const VIEWS = [
  ["#/templates", () => t("Templates")],
  ["#/printers", () => t("Printers")],
];

function header() {
  const nav = document.getElementById("nav");
  nav.setAttribute("aria-label", t("Main"));
  const route = location.hash.split("/").slice(0, 2).join("/");
  nav.replaceChildren(
    h(
      "ul",
      null,
      session.me &&
        VIEWS.map(([href, name]) =>
          h("li", null, h("a", { href, "aria-current": route === href ? "page" : null }, name())),
        ),
    ),
  );
  const box = document.getElementById("session");
  const select = h(
    "select",
    { id: "language", onchange: async (event) => {
      await chooseLanguage(event.target.value);
      header();
      render();
    } },
    languages.map((code) => h("option", { value: code, selected: code === lang }, languageName(code))),
  );
  const signedIn = Boolean(session.me?.token_required);
  box.replaceChildren(
    ...shown([
      h("label", { for: "language", class: "visually-hidden" }, t("Language")),
      select,
      signedIn && h("span", { class: "who" }, `${session.me.display_name ?? session.me.name} (${roleText(session.me.role)})`),
      signedIn && h("button", { type: "button", onclick: signOut }, t("Sign out")),
    ]),
  );
  const skip = document.getElementById("skip");
  skip.textContent = t("Skip to content");
}

// signing out forgets the token and everything entered or shown; a
// session ends on the server, and at the provider if it offers that
async function signOut() {
  const ended = session.me?.session ? await endSession() : null;
  signInError = null;
  token.clear();
  session.clear();
  forgetDrafts();
  forgetBatches();
  current.dispose();
  main().replaceChildren();
  if (ended && ended !== "/") {
    location.assign(ended);
    return;
  }
  location.hash = "#/";
  signIn();
}

// --- views -------------------------------------------------------------------------------

// replaceChildren() would show null as "null": what is left out is left out
function shown(parts) {
  return parts.filter((part) => part !== null && part !== undefined && part !== false);
}

// show a view: one <h1>, focused, its title in the window's title
function mount(title, ...content) {
  const heading = h("h1", { tabindex: "-1" }, title);
  main().replaceChildren(heading, ...shown(content));
  document.title = `${title} – Tavletta`;
  heading.focus();
  return heading;
}

// the provider's answer came back as #/signin-error/<code>: kept for the
// sign in view, the address cleaned up
function takeSignInError() {
  const failed = location.hash.match(/^#\/signin-error\/([a-z_]+)$/);
  if (!failed) return;
  signInError = failed[1];
  history.replaceState(null, "", location.pathname);
}

async function render() {
  takeSignInError();
  generation += 1;
  const mine = generation;
  current.dispose();
  current = { dispose() {} };
  header();
  if (!session.me) {
    signIn();
    return;
  }
  const [, view, ...rest] = location.hash.split("/");
  let argument = "";
  try {
    argument = rest.map(decodeURIComponent).join("/");
  } catch {
    // a broken %-escape in the address: as if none were given
  }
  const views = {
    templates: templatesView,
    printers: printersView,
    print: (ctx) => printView(argument, ctx),
    batch: (ctx) => batchView(argument, ctx),
    job: (ctx) => jobView(argument, ctx),
  };
  const show = Object.hasOwn(views, view) ? views[view] : null;
  if (!show || ((view === "print" || view === "batch" || view === "job") && !argument)) {
    location.replace("#/templates");
    return;
  }
  const ctx = {
    mount,
    status,
    live: () => mine === generation,
    onDispose(fn) {
      const before = current.dispose;
      current.dispose = () => {
        before();
        fn();
      };
    },
  };
  await show(ctx);
}

// --- signing in ------------------------------------------------------------------------

// why signing in with the provider did not work
function signInErrorText(code) {
  const texts = {
    no_role: t("You are signed in at {provider}, but have no access to Tavletta. Ask the administrator for it.", { provider: provider?.provider ?? "" }),
    expired: t("Signing in took too long or was started in another window. Try again."),
    denied: t("Signing in was canceled."),
  };
  return texts[code] ?? t("Signing in did not work. Try again, or ask the administrator.");
}

function signIn() {
  document.getElementById("nav").replaceChildren();
  const parts = [];
  if (signInError) parts.push(notice("warn", h("p", null, signInErrorText(signInError))));
  if (provider) {
    const back = afterSignIn ?? (location.hash.length > 2 ? location.hash : "#/templates");
    const href = `/oidc/login?return=${encodeURIComponent(back)}`;
    parts.push(h("div", { class: "actions" }, h("a", { class: "button primary", href }, t("Sign in with {provider}", { provider: provider.provider }))));
    if (!provider.token_login) {
      mount(t("Sign in"), h("div", { class: "narrow" }, ...parts));
      return;
    }
    parts.push(h("h2", null, t("Or with an access token")));
  }
  const input = h("input", { type: "password", autocomplete: "off", spellcheck: "false", autocapitalize: "off", name: "token" });
  const tokenField = field({
    label: t("Access token"),
    hint: t("The administrator gives it to you. It is kept in this tab only and forgotten when the tab is closed."),
    input,
  });
  const show = h("input", { type: "checkbox", id: "show-token", onchange: (e) => (input.type = e.target.checked ? "text" : "password") });
  const summary = summaryBox();
  const button = h("button", { type: "submit", class: provider ? null : "primary" }, t("Sign in"));
  const form = h(
    "form",
    { class: "narrow", novalidate: true },
    ...parts,
    summary,
    tokenField.node,
    h("div", { class: "check" }, show, h("label", { for: "show-token" }, t("Show token"))),
    h("div", { class: "actions" }, button),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    tokenField.clear();
    const value = input.value.trim();
    if (!value) {
      tokenField.error(t("Enter the access token."));
      summary.hidden = true;
      input.focus();
      return;
    }
    button.disabled = true;
    token.set(value);
    try {
      session.me = await api("GET", "/me");
      signInError = null;
      input.value = "";
      location.hash = afterSignIn ?? "#/templates";
      afterSignIn = null;
      render();
    } catch (error) {
      token.clear();
      const text = error.status === 401 ? t("The token was not accepted. Check it, or ask the administrator for a new one.") : errorText(error);
      tokenField.error(text);
      input.focus();
    } finally {
      button.disabled = false;
    }
  });
  mount(t("Sign in"), form);
}

// --- printers ------------------------------------------------------------------------------

async function printersView(ctx) {
  const list = h("div", { class: "cards" });
  const refresh = h("button", { type: "button", onclick: () => load() }, t("Check again"));
  mount(t("Printers"), h("div", { class: "toolbar" }, refresh), list);

  async function load() {
    list.replaceChildren();
    const done = loading(list);
    try {
      const { printers } = await api("GET", "/printers?discover=false");
      const checked = new Date().toISOString();
      if (!ctx.live()) return;
      done();
      if (!printers.length) {
        list.append(h("p", null, t("No printer this token may use. Ask the administrator.")));
        return;
      }
      for (const printer of printers) list.append("max_copies" in printer ? namedCard(printer, checked) : modelCard(printer));
    } catch (error) {
      done();
      if (ctx.live()) list.append(errorBanner(error, load));
    }
  }

  function namedCard(printer, checked) {
    const details = [
      printer.description,
      printer.location && t("Location: {place}", { place: printer.location }),
      printer.media && t("Meant for: {media}", { media: printer.media }),
      t("Model: {model}", { model: printer.model }),
      ...media(printer.status),
    ].filter(Boolean);
    return h(
      "article",
      { class: "card" },
      h("h2", null, printer.name, printer.default && h("span", { class: "tag" }, t("Default"))),
      printerState(printer, checked),
      h("ul", { class: "facts" }, details.map((d) => h("li", null, d))),
    );
  }

  function modelCard(model) {
    const result = h("div", { "aria-live": "polite" });
    const ask = h(
      "button",
      {
        type: "button",
        onclick: async () => {
          ask.disabled = true;
          result.replaceChildren(h("p", null, t("Asking the printer…")));
          try {
            const info = await api("GET", `/printers/${encodeURIComponent(model.id)}/status`);
            result.replaceChildren(printerState({ status: info }, new Date().toISOString()), h("ul", { class: "facts" }, media(info).map((d) => h("li", null, d))));
          } catch (error) {
            result.replaceChildren(printerState({ status: { state: "UNREACHABLE", error: { message: errorText(error) } } }, new Date().toISOString()));
          } finally {
            ask.disabled = false;
          }
        },
      },
      t("Ask for its state"),
    );
    return h(
      "article",
      { class: "card" },
      h("h2", null, model.name),
      h("p", null, model.configured_host ? t("Address: {host}", { host: model.configured_host }) : t("No address set")),
      model.configured_host && ask,
      result,
    );
  }

  await load();
}

// --- templates -----------------------------------------------------------------------------

async function templatesView(ctx) {
  const list = h("ul", { class: "cards", role: "list" });
  const admin = session.may("admin");
  const upload = admin ? uploadForm(() => load()) : null;
  mount(t("Templates"), upload, list);
  const thumbnails = [];
  ctx.onDispose(() => thumbnails.forEach((url) => URL.revokeObjectURL(url)));

  async function load() {
    list.replaceChildren();
    const done = loading(list);
    try {
      const { labels } = await api("GET", "/labels");
      if (!ctx.live()) return;
      done();
      if (!labels.length) {
        list.append(
          h(
            "li",
            { class: "empty" },
            admin ? t("No templates yet. Upload one above.") : t("No templates yet. Ask the administrator."),
          ),
        );
        return;
      }
      const cards = labels.map((entry) => card(entry));
      list.append(...cards.map((c) => c.node));
      // details and thumbnails two at a time: the server renders two at once
      const queue = [...cards];
      const worker = async () => {
        while (queue.length && ctx.live()) await queue.shift().fill();
      };
      await Promise.all([worker(), worker()]);
    } catch (error) {
      done();
      if (ctx.live()) list.append(errorBanner(error, load));
    }
  }

  function card({ name }) {
    const figure = h("div", { class: "thumb" });
    const facts = h("ul", { class: "facts" });
    const title = name.replace(/\.tavletta$/, "");
    const actions = h(
      "div",
      { class: "actions" },
      h("a", { class: "button primary", href: `#/print/${labelPath(name)}` }, t("Print")),
      h("a", { class: "button", href: `#/batch/${labelPath(name)}` }, t("Series")),
      admin && h("button", { type: "button", class: "danger-outline", onclick: () => remove(name) }, t("Delete")),
    );
    const node = h("li", { class: "card" }, h("h2", null, title), figure, facts, actions);
    async function fill() {
      try {
        const label = await describe(name);
        const fields = label.columns.length + label.variables.length + label.elements.filter((e) => e.name).length;
        facts.replaceChildren(
          h("li", null, t("Size: {width} × {height} mm", { width: mm(label.size.width_mm), height: mm(label.size.height_mm) })),
          h("li", null, t("Fields: {count}", { count: num(fields) })),
          h("li", null, t("Printer model: {model}", { model: label.printer.name })),
        );
        const blob = await api("POST", "/preview", { label: name }, { binary: true });
        if (!ctx.live()) return;
        const url = URL.createObjectURL(blob);
        thumbnails.push(url);
        figure.replaceChildren(h("img", { src: url, alt: t("Preview of {name}", { name: title }) }));
      } catch (error) {
        if (ctx.live()) figure.replaceChildren(h("p", { class: "hint" }, icon("warn"), " ", errorText(error)));
      }
    }
    return { node, fill };
  }

  async function remove(name) {
    const title = name.replace(/\.tavletta$/, "");
    const yes = await confirm({
      title: t("Delete template?"),
      lines: [t("The template {name} is deleted from the server. Its data file stays.", { name: title })],
      action: t("Delete {name}", { name: title }),
      danger: true,
    });
    if (!yes) return;
    try {
      await api("DELETE", `/labels/${labelPath(name)}`);
      session.labels.delete(name);
      status(t("{name} deleted.", { name: title }));
      await load();
    } catch (error) {
      list.before(errorBanner(error));
    }
  }

  await load();
}

function uploadForm(loaded) {
  const file = h("input", { type: "file", accept: ".tavletta,application/json" });
  const fileField = field({
    label: t("Template file"),
    hint: t("A .tavletta file saved by Tavletta, at most {size} MB.", { size: mm(session.me.limits.max_body / 1048576) }),
    input: file,
  });
  const replace = h("input", { type: "checkbox", id: "replace" });
  const button = h("button", { type: "submit" }, t("Upload"));
  const form = h(
    "form",
    { class: "panel", novalidate: true },
    h("h2", null, t("Upload a template")),
    fileField.node,
    h("div", { class: "check" }, replace, h("label", { for: "replace" }, t("Replace a template of the same name"))),
    h("div", { class: "actions" }, button),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    fileField.clear();
    const chosen = file.files[0];
    if (!chosen) {
      fileField.error(t("Choose a template file."));
      file.focus();
      return;
    }
    if (chosen.size > session.me.limits.max_body) {
      fileField.error(t("The file is too large: at most {size} MB.", { size: mm(session.me.limits.max_body / 1048576) }));
      file.focus();
      return;
    }
    button.disabled = true;
    try {
      const text = await chosen.text();
      const query = replace.checked ? "?replace=true" : "";
      const saved = await api("PUT", `/labels/${encodeURIComponent(chosen.name)}${query}`, text);
      session.labels.delete(saved.name);
      form.reset();
      status(t("{name} uploaded.", { name: saved.name }));
      loaded();
    } catch (error) {
      const text = error.code === "label_exists"
        ? t("A template of this name exists. Choose \"Replace a template of the same name\" to replace it.")
        : error.code === "invalid_field"
          ? t("The file name can have letters, digits, spaces and . _ - ( ) + , and must not start with a dot. Rename the file.")
          : errorText(error);
      fileField.error(text);
      file.focus();
    } finally {
      button.disabled = false;
    }
  });
  return form;
}

// --- a job -------------------------------------------------------------------------------

async function jobView(id, ctx) {
  const area = h("div");
  mount(t("Print job"), area);
  const done = loading(area);
  try {
    const job = await api("GET", `/jobs/${encodeURIComponent(id)}`);
    done();
    if (!ctx.live()) return;
    const label = job.label ? job.label.replace(/\.tavletta$/, "") : "";
    area.append(h("p", null, label && t("Template: {name}", { name: label }), " ", job.printer_name && t("Printer: {name}", { name: job.printer_name })));
    for (const warning of job.warnings ?? []) area.append(notice("warn", h("p", null, warning.message)));
    const finish = (ended) => {
      area.querySelector(".progress")?.remove();
      area.append(jobResult(ended));
      status(t("Print job ended: {printed} of {total} labels printed.", { printed: num(ended.printed), total: num(ended.total) }));
    };
    if (["done", "failed", "canceled"].includes(job.state)) {
      finish(job);
    } else {
      const progress = jobProgress(job, { onEnd: finish, status });
      ctx.onDispose(progress.stop);
      area.append(progress.node);
    }
  } catch (error) {
    done();
    if (ctx.live()) area.append(errorBanner(error, () => render()));
  }
}

// --- start ---------------------------------------------------------------------------------

whenUnauthorized(() => {
  if (!session.me) return;
  afterSignIn = location.hash;
  session.clear();
  token.clear();
  render();
});

async function start() {
  await loadMessages();
  const skip = document.getElementById("skip");
  skip.addEventListener("click", (event) => {
    event.preventDefault();
    main().focus();
  });
  window.addEventListener("hashchange", () => render());
  window.addEventListener("offline", () => document.getElementById("banner").replaceChildren(notice("warn", h("p", null, t("No connection to the server. Your input is kept.")))));
  window.addEventListener("online", () => document.getElementById("banner").replaceChildren());
  takeSignInError();
  provider = await loginInfo();
  try {
    session.me = await api("GET", "/me");
  } catch (error) {
    if (error.status !== 401) {
      header();
      mount("Tavletta", errorBanner(error, () => location.reload()));
      return;
    }
    if (location.hash.length > 2) afterSignIn = location.hash;
  }
  render();
}

start();
