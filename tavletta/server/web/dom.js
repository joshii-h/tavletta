// Building the page with createElement and textContent only: data from
// files and labels (CSV cells, names) never become markup. No innerHTML,
// no inline styles (the Content-Security-Policy forbids them anyway).

const SVG = "http://www.w3.org/2000/svg";

function apply(el, props) {
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (key === "text") {
      el.textContent = value;
    } else if (key === "class") {
      el.setAttribute("class", Array.isArray(value) ? value.filter(Boolean).join(" ") : value);
    } else if (key === "value" || key === "checked" || key === "indeterminate") {
      el[key] = value;
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
}

function add(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

// h("button", {class: "primary", onclick}, "Print")
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  apply(el, props);
  add(el, children);
  return el;
}

export function svg(tag, props, ...children) {
  const el = document.createElementNS(SVG, tag);
  apply(el, props);
  add(el, children);
  return el;
}

let unique = 0;
export function uid(prefix = "id") {
  unique += 1;
  return `${prefix}-${unique}`;
}

// The small symbols beside a state: never colour alone.
const ICONS = {
  ok: "M4 8.5l2.5 2.5L12 5.5",
  warn: "M8 2.5l6 11H2zM8 6.5v3.5M8 12v.5",
  error: "M4 4l8 8M12 4l-8 8",
  info: "M8 4.5v.5M8 7v5",
  busy: "M8 2.5a5.5 5.5 0 1 1-5.5 5.5",
};

export function icon(name) {
  return svg(
    "svg",
    { class: `icon icon-${name}`, viewBox: "0 0 16 16", width: 16, height: 16, "aria-hidden": "true", focusable: "false" },
    svg("path", { d: ICONS[name] ?? ICONS.info }),
  );
}

// A function called once input has paused for `ms`.
export function debounce(fn, ms) {
  let timer = 0;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}
