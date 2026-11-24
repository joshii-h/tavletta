"""The render limits of the print server (release review of 1.1.0): the free
layout, texts and codes as they are filled in, and pages of shipping labels
(image formats, the pixel budget, no caches, PDFs in a child process)."""

from __future__ import annotations

import base64
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from shipping_samples import a4_pdf
from test_server import call, label_file, wait_for

from tavletta import imagefx, typeset
from tavletta import render as render_module
from tavletta.model import CodeElement, ImageElement, Label, PageElement, TextElement
from tavletta.render import RenderTooLarge, limited, render
from tavletta.server import main as server_main
from tavletta.server import service as service_module
from tavletta.shipping import pdfchild, source
from tavletta.shipping.source import SourceError


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def png(size, mode="RGB") -> str:
    buffer = io.BytesIO()
    Image.new(mode, size, "white").save(buffer, format="PNG")
    return b64(buffer.getvalue())


def encoded(image: Image.Image, fmt: str) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return b64(buffer.getvalue())


def has_poppler() -> bool:
    try:
        source._poppler()
    except SourceError:
        return False
    return True


needs_poppler = pytest.mark.skipif(not has_poppler(), reason="Poppler (poppler-glib) missing")


@pytest.fixture(autouse=True)
def _fresh_caches():
    source.clear_caches()
    yield
    source.clear_caches()


@pytest.fixture(scope="module")
def pdf():
    return b64(a4_pdf("top-left", pages=2))


@pytest.fixture
def made(monkeypatch):
    """The sizes of the images Pillow makes with Image.new meanwhile."""
    sizes = []
    real = Image.new

    def new(mode, size, *args, **kwargs):
        sizes.append(tuple(size))
        return real(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image, "new", new)
    return sizes


def free(*elements, **fields) -> Label:
    fields.setdefault("tape_mm", 12)
    return Label(elements=list(elements), layout="free", **fields)


# --- H1: the free layout -----------------------------------------------------------


def test_free_layout_code_is_checked_before_its_mask_is_made(made):
    """A PDF417 of 10 mm modules: refused before its mask (gigabytes at
    the server's limits) is made; RenderTooLarge passes _part()."""
    code = CodeElement(data="x" * 40, symbology="pdf417", module_mm=10)
    with limited(1_000_000), pytest.raises(RenderTooLarge):
        render(free(code), 300)
    assert all(w * h <= 1_000_000 for w, h in made)


def test_free_layout_budget_is_counted_as_elements_are_drawn(monkeypatch):
    drawn = []
    real = render_module.render_image

    def counting(*args, **kwargs):
        drawn.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(render_module, "render_image", counting)
    data = png((10, 10))
    images = [ImageElement(data=data, height_mm=10) for _n in range(16)]
    with limited(30_000), pytest.raises(RenderTooLarge):  # each 118², two too many
        render(free(*images), 300)
    assert len(drawn) == 3  # not all 16 first
    assert render(free(*images), 300).image.width > 0  # without limits


# --- H2: texts and codes as they are filled in -------------------------------------


def test_expanded_text_is_refused_before_pango(monkeypatch):
    laid_out = []
    real = typeset._attributes

    def watched(*args):
        laid_out.append(1)  # Pango is about to get the text
        return real(*args)

    monkeypatch.setattr(typeset, "_attributes", watched)
    for text in ("W" * 5001, "W\n" * 100 + "W", "a\nb" * 100):
        with limited(10**9), pytest.raises(RenderTooLarge):
            render(Label(tape_mm=12, elements=[TextElement(text=text)]), 100)
        with limited(10**9), pytest.raises(RenderTooLarge):
            render(free(TextElement(text=text)), 100)
    assert laid_out == []
    # at the limits, and without limits at all
    with limited(10**9):
        render(Label(tape_mm=12, elements=[TextElement(text="W\n" * 99 + "W")]), 100)
    assert render(Label(tape_mm=12, elements=[TextElement(text="W" * 5001)]), 50)


def test_expanded_code_is_refused():
    code = CodeElement(data="1" * 5001, symbology="qr")
    with limited(10**9), pytest.raises(RenderTooLarge):
        render(Label(tape_mm=12, elements=[code]), 100)
    with limited(10**9), pytest.raises(RenderTooLarge):
        render(free(code), 100)


def test_check_text():
    render_module.check_text("x" * 10**6)  # no limits
    with limited(10**9):
        render_module.check_text("x\n" * 99)  # 100 lines
        render_module.check_text("x\n" * 200, lines=False)
        with pytest.raises(RenderTooLarge):
            render_module.check_text("x\n" * 100)


@pytest.fixture
def api(make_server):
    server, base = make_server()
    return server, base


def content(*elements, **fields):
    fields.setdefault("printer", "fake-mono")
    fields.setdefault("tape_mm", 12)
    return Label(elements=list(elements), **fields).to_dict()


@pytest.mark.parametrize(
    "body",
    [
        {"label_content": content(TextElement(text="{=a}" * 1250)), "set": {"a": "W" * 5000}},
        {"label_content": content(TextElement(text="{=a}" * 3)), "set": {"a": "W\n" * 99}},
        {
            "label_content": content(TextElement(text="{Name}" * 3)),
            "data_rows": [{"Name": "x\n" * 99}],
        },
        {"label_content": content(CodeElement(data="{=a}{=a}")), "set": {"a": "1" * 3000}},
    ],
)
def test_filled_in_texts_are_refused_over_http(api, body):
    _server, base = api
    status, _r, answer = call(base, "POST", "/api/v1/preview", body=body)
    assert status == 413 and answer["error"]["code"] == "label_too_large", answer


def test_every_row_of_a_series_is_within_the_limits(api, server_env):
    server, base = api
    rows = [{"Name": "fine"}, {"Name": "x\n" * 99}]
    body = {
        "label_content": content(TextElement(text="{Name}{Name}")),
        "data_rows": rows,
        "wait": 10,
    }
    status, _r, job = call(base, "POST", "/api/v1/print", body=body)
    assert status in (200, 202), job
    wait_for(lambda: server.service.job(job["id"]).state in ("failed", "done"))
    assert server.service.job(job["id"]).state == "failed"


def test_max_lines_counts_lines_not_newlines():
    assert service_module._too_long("W\n" * 100)  # 101 lines
    assert not service_module._too_long("W\n" * 99)


# --- H3: pages of shipping labels --------------------------------------------------


@pytest.mark.parametrize("fmt", ["ICO", "PCX", "TGA", "SGI", "DDS"])
def test_pages_take_only_the_safe_image_formats(fmt):
    data = encoded(Image.new("RGB", (16, 16), "black"), fmt)
    with pytest.raises(SourceError) as error:
        source.page_info(data)
    assert error.value.code == "unreadable"


def test_eps_is_never_run(monkeypatch):
    from PIL import EpsImagePlugin

    ran = []

    def ghostscript(*args, **_kwargs):
        ran.append(args)
        raise OSError("no Ghostscript in tests")

    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", ghostscript)
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n"
    with pytest.raises(SourceError):
        source.draw(b64(eps), 0, (0, 0, 1, 1), 0, (10, 10))
    label = Label(tape_mm=12, elements=[PageElement(data=b64(eps))])
    assert render(label, 100).warnings[0].key == "page_unreadable"
    assert ran == []


def test_image_pages_count_in_the_image_budget():
    page = PageElement(data=png((300, 200)))
    label = Label(tape_mm=12, elements=[page])
    with limited(100_000), pytest.raises(RenderTooLarge):  # 60 000 > 50 000
        render(label, 100)
    # an image and a page of the same file: decoded apart, counted apart
    both = Label(tape_mm=12, elements=[PageElement(data=png((150, 200))), ImageElement()])
    both.elements[1].data = both.elements[0].data
    with limited(100_000), pytest.raises(RenderTooLarge):
        render(both, 100)
    with limited(200_000):
        assert render(both, 100).warnings == []


def test_a_larger_frame_than_counted_is_refused(monkeypatch):
    frames = [Image.new("L", (10, 10)), Image.new("L", (300, 300))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    page = PageElement(data=b64(buffer.getvalue()), page=1)
    with limited(100_000), pytest.raises(RenderTooLarge):
        render(Label(tape_mm=12, elements=[page]), 100)
    with limited(100_000), pytest.raises(SourceError):
        source._limited_picture("not base64!", 0, render_module.active_limits())


def test_nothing_of_a_page_is_kept_within_limits():
    label = Label(tape_mm=12, elements=[PageElement(data=png((40, 60)))])
    with limited(10**7):
        render(label, 100)
        render(label, 100, mono=True)
    assert len(source._pictures) == 0 and len(source._drawings) == 0
    render(label, 100, mono=True)  # the window keeps them
    assert len(source._pictures) == 1 and len(source._drawings) > 0


def test_background_brightness_is_not_cached_within_limits():
    render_module._image_brightness_cached.cache_clear()
    label = Label(tape_mm=12, background_image=ImageElement(data=png((20, 20))))
    with limited(10**7):
        render(label, 100, mono=True)
    assert render_module._image_brightness_cached.cache_info().currsize == 0
    render(label, 100, mono=True)
    assert render_module._image_brightness_cached.cache_info().currsize == 1


@needs_poppler
def test_pdf_pages_are_drawn_by_a_child_within_limits(pdf, monkeypatch):
    label = Label(printer="cab", tape_mm=50, elements=[PageElement(data=pdf)])
    plain = render(label, 300).image
    source.clear_caches()
    asked = []
    real = pdfchild.ask

    def counting(raw, request):
        asked.append(request["op"])
        return real(raw, request)

    monkeypatch.setattr(pdfchild, "ask", counting)
    monkeypatch.setattr(pdfchild, "SECONDS", 120.0)  # a busy test machine
    monkeypatch.setattr(source, "_open_pdf", lambda raw: pytest.fail("Poppler in the server"))
    with limited(40_000_000):
        assert render(label, 300).image.tobytes() == plain.tobytes()
        mono = render(label, 300, mono=True)
    # the second draw asks what the first did: kept within the block
    assert asked == ["draw", "info"] and mono.warnings == []
    assert len(source._drawings) == 0


def test_a_kept_pdf_answer_needs_the_file_neither_decoded_nor_hashed_again(monkeypatch):
    """A series draws the same page on every row: the answer is kept, and
    the PDF (up to 10 MB) is decoded only to ask, hashed once per block."""
    answer = json.dumps({"width": 2, "height": 1}).encode() + b"\n" + bytes(6)
    asked, decoded, hashed = [], [], []
    monkeypatch.setattr(
        pdfchild, "ask", lambda raw, request: asked.append(request["op"]) or fake_answer(answer)
    )
    real_decoded, real_sha = source._decoded, render_module.hashlib.sha256
    monkeypatch.setattr(source, "_decoded", lambda data: decoded.append(1) or real_decoded(data))
    monkeypatch.setattr(
        render_module.hashlib, "sha256", lambda data: hashed.append(1) or real_sha(data)
    )
    data = b64(b"%PDF-1.4 " + b"x" * 1000)
    with limited(10**6):
        for _row in range(5):
            source.draw(data, 0, (0, 0, 1, 1), 0, (2, 1))
    assert asked == ["draw"] and len(decoded) == 1 and len(hashed) == 1


def fake_answer(stdout: bytes) -> tuple[dict, bytes]:
    head, _newline, body = stdout.partition(b"\n")
    return json.loads(head), body


@needs_poppler
def test_pdf_limits_within_the_child(pdf, monkeypatch):
    label = Label(printer="cab", tape_mm=50, elements=[PageElement(data=pdf)])
    monkeypatch.setattr(source, "LIMITED_PDF_BYTES", 100)
    with limited(40_000_000), pytest.raises(RenderTooLarge):
        render(label, 300)
    monkeypatch.undo()
    with limited(1000), pytest.raises(RenderTooLarge):  # endless tape: the child checks
        source.draw(pdf, 0, (0, 0, 1, 1), 0, (None, 500))
    damaged = b64(b"%PDF-1.4\nnothing else")
    with limited(40_000_000):
        assert render(Label(tape_mm=12, elements=[PageElement(data=damaged)]), 100).warnings
        with pytest.raises(SourceError):
            source.page_info(damaged)


def fake_run(returncode, stdout=b"", stderr=b""):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    return run


@pytest.mark.parametrize(
    "said", [b"Out of memory\n", b"GLib-ERROR **: failed to allocate 4294967296 bytes"]
)
def test_a_child_aborted_by_its_memory_limit_is_too_large(monkeypatch, said):
    """Poppler's and GLib's allocators abort when the memory limit stops
    them: the PDF is too large for its limits, not damaged."""
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(-signal.SIGABRT, stderr=said))
    with pytest.raises(pdfchild.ChildLimit):
        pdfchild.ask(b"", {})
    with limited(10**6), pytest.raises(RenderTooLarge):
        source.page_info(b64(b"%PDF-1.4"))
    # another abort (an assertion in Poppler): unreadable
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(-signal.SIGABRT, stderr=b"assert"))
    with pytest.raises(pdfchild.ChildFailed):
        pdfchild.ask(b"", {})


def test_the_child_gets_no_secrets_of_the_server(monkeypatch):
    """Only what finds Python, the libraries and fonts: not the server's
    token or anything else of its environment."""
    seen = {}

    def run(*_args, env, **_kwargs):
        seen.update(env)
        return subprocess.CompletedProcess([], 0, b"{}\n", b"")

    monkeypatch.setenv("TAVLETTA_TOKEN", "secret-token-of-the-server")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "elsewhere")
    monkeypatch.setenv("FONTCONFIG_FILE", "/fonts.conf")
    monkeypatch.setenv("PYTHONPATH", "/extra")
    monkeypatch.setattr(pdfchild.subprocess, "run", run)
    pdfchild.ask(b"", {})
    assert "TAVLETTA_TOKEN" not in seen and "AWS_SECRET_ACCESS_KEY" not in seen
    assert set(seen) <= {*pdfchild.KEPT_ENVIRONMENT, "PYTHONPATH"}
    assert seen["FONTCONFIG_FILE"] == "/fonts.conf"
    root = str(Path(pdfchild.__file__).resolve().parent.parent.parent)
    assert seen["PYTHONPATH"] == os.pathsep.join((root, "/extra"))


def test_child_outcomes(monkeypatch):
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(-signal.SIGXCPU))
    with pytest.raises(pdfchild.ChildLimit):
        pdfchild.ask(b"", {})
    with limited(10**6), pytest.raises(RenderTooLarge):
        source.page_info(b64(b"%PDF-1.4"))
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(-signal.SIGSEGV))
    with pytest.raises(pdfchild.ChildFailed):
        pdfchild.ask(b"", {})
    with limited(10**6), pytest.raises(SourceError):
        source.page_info(b64(b"%PDF-1.4"))
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(0, b"not json"))
    with pytest.raises(pdfchild.ChildFailed):
        pdfchild.ask(b"", {})
    answer = json.dumps({"width": 3, "height": 3}).encode() + b"\n" + b"short"
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(0, answer))
    with limited(10**6), pytest.raises(SourceError):
        source.draw(b64(b"%PDF-1.4"), 0, (0, 0, 1, 1), 0, (3, 3))
    too_large = json.dumps({"error": "too_large"}).encode() + b"\n"
    monkeypatch.setattr(pdfchild.subprocess, "run", fake_run(0, too_large))
    with limited(10**6), pytest.raises(RenderTooLarge):
        source.page_info(b64(b"%PDF-1.4"))

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("python", 1)

    monkeypatch.setattr(pdfchild.subprocess, "run", timeout)
    with pytest.raises(pdfchild.ChildLimit):
        pdfchild.ask(b"", {})

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("python")

    monkeypatch.setattr(pdfchild.subprocess, "run", missing)
    with pytest.raises(pdfchild.ChildFailed):
        pdfchild.ask(b"", {})


@needs_poppler
def test_child_main_in_process(monkeypatch, capsysbinary):
    """What the child does, here (its limits not set on the tests)."""
    limits = []
    monkeypatch.setattr(pdfchild, "_limit", limits.append)
    raw = a4_pdf("top-left")

    def run(request):
        stdin = io.BytesIO(json.dumps(request).encode() + b"\n" + raw)
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(stdin))
        assert pdfchild.main() == 0
        head, _newline, body = capsysbinary.readouterr().out.partition(b"\n")
        return json.loads(head), body

    info, _body = run({"op": "info", "page": 0})
    assert info["pages"] == 1 and info["width"] > 0
    drawing = {"op": "draw", "page": 0, "crop": [0, 0, 1, 1], "rotation": 90, "fit": "fit"}
    answer, body = run({**drawing, "box": [40, 50], "sharp": True, "max_pixels": 10**6})
    assert len(body) == answer["width"] * answer["height"] * 3
    answer, _body = run({**drawing, "box": [400, 500], "sharp": False, "max_pixels": 100})
    assert answer["error"] == "too_large"
    raw = b"%PDF-1.4 damaged"
    answer, _body = run({"op": "info", "page": 0})
    assert answer["error"] == "unreadable"
    assert limits == [0, 10**6, 100, 0]

    def exhausted(_request, _raw):
        raise MemoryError

    monkeypatch.setattr(pdfchild, "_answer", exhausted)
    assert run({"op": "info"})[0]["error"] == "too_large"


def test_child_limits_its_own_memory_and_time():
    """The child sets its limits on itself (run here in a process of its own)."""
    code = (
        "import resource; from tavletta.shipping import pdfchild; pdfchild._limit(1000);"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0])"
    )
    code += ";print(resource.getrlimit(resource.RLIMIT_CORE)[0], resource.getrlimit(resource.RLIMIT_NOFILE)[0])"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, text=True)
    memory, cpu, core, files = map(int, done.stdout.split())
    assert memory == pdfchild._memory(1000) and cpu == pdfchild.CPU_SECONDS
    assert core == 0 and files <= pdfchild.OPEN_FILES


def test_pdfchild_memory():
    assert pdfchild._memory(40_000_000) > pdfchild.BASE_MEMORY


# --- the server: pages in label_content ----------------------------------------------


def test_pages_in_label_content_are_refused_by_default(api, server_env):
    server, base = api
    body = {"label_content": content(PageElement(data=png((20, 20))))}
    for path in ("/api/v1/preview", "/api/v1/print"):
        status, _r, answer = call(base, "POST", path, body=body)
        assert status == 422 and answer["error"]["code"] == "pages_not_allowed", answer
    # a label of the labels folder may hold one
    label_file(server_env.labels, "page.tavletta", PageElement(data=png((20, 20))))
    assert call(base, "POST", "/api/v1/preview", body={"label": "page"})[0] == 200
    server.service.allow_pdf_pages = True
    assert call(base, "POST", "/api/v1/preview", body=body)[0] == 200


def test_pages_count_as_pictures(api):
    server, base = api
    server.service.allow_pdf_pages = True
    pages = [PageElement(data=png((4, 4))) for _n in range(51)]
    status, _r, answer = call(
        base, "POST", "/api/v1/preview", body={"label_content": content(*pages)}
    )
    assert status == 413 and answer["error"]["code"] == "label_too_large"


def test_allow_pdf_pages_option(tmp_path):
    from tavletta.cli import parser

    for command in ("serve", "mcp"):
        args = parser().parse_args([command, "--labels-dir", str(tmp_path)])
        assert args.allow_pdf_pages is False
        args = parser().parse_args([command, "--labels-dir", str(tmp_path), "--allow-pdf-pages"])
        service = server_main._service(args, None)
        try:
            assert service.allow_pdf_pages
        finally:
            service.close()


# --- nits ------------------------------------------------------------------------------


def test_picture_kind():
    assert imagefx.picture_kind(Image.new("L", (10, 10), 0)) == "drawing"
    tones = Image.new("L", (30, 10), 60)
    tones.paste(120, (10, 0, 20, 10))
    tones.paste(180, (20, 0, 30, 10))
    assert imagefx.picture_kind(tones) == "tones"
    gradient = Image.linear_gradient("L").resize((64, 64))
    assert imagefx.picture_kind(gradient) == "photo"


class _SlowChild:
    """subprocess.run for the PDF child, on a fake clock: each child takes
    `seconds`, or its timeout if that is shorter (then TimeoutExpired).
    Unreadable PDFs, or (drawing=True) a one-page PDF of 100 × 50 points
    drawn grey."""

    def __init__(self, monkeypatch, seconds: float, drawing: bool = False):
        self.now = 1000.0
        self.seconds = seconds
        self.drawing = drawing
        self.timeouts: list[float] = []
        monkeypatch.setattr(render_module, "_clock", lambda: self.now)
        monkeypatch.setattr(pdfchild.subprocess, "run", self.run)

    def run(self, *_args, timeout, input, **_kwargs):  # noqa: A002 - subprocess.run's name
        self.timeouts.append(timeout)
        if timeout < self.seconds:
            self.now += timeout
            raise subprocess.TimeoutExpired("child", timeout)
        self.now += self.seconds
        if not self.drawing:
            return subprocess.CompletedProcess([], 0, b'{"error": "unreadable"}\n', b"")
        request = json.loads(input.partition(b"\n")[0])
        if request["op"] == "info":
            answer, body = {"width": 100.0, "height": 50.0, "pages": 1}, b""
        else:
            width, height = request["box"]
            width = width or 2 * height  # endless tape: as wide as the page needs
            answer, body = {"width": width, "height": height}, b"\x80" * (width * height * 3)
        return subprocess.CompletedProcess([], 0, json.dumps(answer).encode() + b"\n" + body, b"")


def _pages(count: int, same: bool = False) -> Label:
    pages = [
        PageElement(data=b64(b"%%PDF-1.4 page %d" % (0 if same else n))) for n in range(count)
    ]
    return Label(tape_mm=12, elements=pages)


def test_the_pdf_pages_of_one_render_share_a_time_budget(monkeypatch):
    """Each page may take pdfchild.SECONDS, but all pages of one render
    (one label) together only pdf_seconds: fifty slow pages must not hold
    a render slot for minutes."""
    child = _SlowChild(monkeypatch, 0.2)
    with limited(10**7, pdf_seconds=0.5):
        with pytest.raises(RenderTooLarge):
            render(_pages(10), 100)
        # 0.5 s: two pages of 0.2 s, the third cut short, the rest not started
        assert child.timeouts == pytest.approx([0.5, 0.3, 0.1])
        # the next render (the next row of a series) has a budget of its own
        child.timeouts.clear()
        with pytest.raises(RenderTooLarge):
            render(
                _pages(
                    10,
                ),
                100,
            )
        assert child.timeouts[0] == pytest.approx(0.5)
    assert render_module.pdf_seconds_left() is None  # without limits: no budget


def test_a_series_with_the_same_page_asks_the_child_once(monkeypatch):
    """200 rows of a label with one PDF page, within one limited() block
    (the server's print job): the page is drawn by a child once, the rows
    take no time of their budgets."""
    child = _SlowChild(monkeypatch, 0.25, drawing=True)
    label = _pages(1)
    with limited(10**7, pdf_seconds=0.5):
        first = render(label, 100)
        asked = len(child.timeouts)
        assert asked >= 1 and first.warnings == []
        for _row in range(200):
            assert render(label, 100).image.tobytes() == first.image.tobytes()
        assert len(child.timeouts) == asked
    # a new block (another job) asks again
    with limited(10**7, pdf_seconds=0.5):
        render(label, 100)
    assert len(child.timeouts) == 2 * asked


def test_pdf_answers_are_bounded():
    answers = render_module.PdfAnswers(items=2, max_bytes=10, shared=render_module.KeptBytes(100))
    answers.put(("a",), {}, b"1234")
    answers.put(("b",), {}, b"1234")
    assert answers.get(("a",)) is not None  # used: kept
    answers.put(("c",), {}, b"1234")  # three items: "b" goes
    assert answers.get(("b",)) is None and answers.get(("c",)) is not None
    answers.put(("d",), {}, b"12345678")  # over the bytes: the oldest go
    assert answers.get(("a",)) is None and answers.get(("c",)) is None
    assert answers.get(("d",)) == ({}, b"12345678")
    answers.put(("e",), {}, b"x" * 11)  # larger than all: not kept
    assert answers.get(("e",)) is None and answers.get(("d",)) is not None
    answers.put(("d",), {"n": 1}, b"1")  # replaced
    assert answers.get(("d",)) == ({"n": 1}, b"1")


def test_pdf_answers_share_one_budget_for_the_process():
    """Many jobs at once keep PDF_KEPT_ALL_BYTES together at most: what
    finds no room is not kept, and a job that ends gives its bytes back."""
    shared = render_module.KeptBytes(10)
    one = render_module.PdfAnswers(shared=shared)
    other = render_module.PdfAnswers(shared=shared)
    one.put(("a",), {}, b"x" * 8)
    other.put(("b",), {}, b"y" * 4)  # no room left
    assert other.get(("b",)) is None and shared.used == 8
    other.put(("c",), {}, b"z")
    other.put(("d",), {}, b"w" * 4)  # no room: what "other" keeps stays
    assert other.get(("c",)) == ({}, b"z") and shared.used == 9
    one.put(("a",), {}, b"x" * 2)  # replaced: the 8 bytes given back
    other.put(("b",), {}, b"y" * 4)
    assert other.get(("b",)) is not None and shared.used == 7
    one.close()
    other.close()
    assert shared.used == 0 and one.get(("a",)) is None


def test_a_job_makes_room_in_the_shared_budget_from_its_own_drawings():
    """Two jobs fill the shared budget; one switches to another page
    mid-series: its new drawing is kept by dropping its own oldest, never
    the other job's."""
    shared = render_module.KeptBytes(20)
    series = render_module.PdfAnswers(shared=shared)
    other = render_module.PdfAnswers(shared=shared)
    other.put(("other",), {}, b"o" * 10)
    series.put(("page 1",), {}, b"1" * 6)
    series.put(("page 1 info",), {}, b"i" * 4)
    assert shared.used == 20
    series.get(("page 1 info",))  # used again: the newest
    series.put(("page 2",), {}, b"2" * 6)  # room from "page 1" (6 bytes)
    assert series.get(("page 1",)) is None and series.get(("page 2",)) is not None
    assert series.get(("page 1 info",)) is not None and other.get(("other",)) is not None
    assert shared.used == 20
    series.put(("page 3",), {}, b"3" * 9)  # needs both of its own: dropped
    assert series.get(("page 3",)) is not None and series.get(("page 2",)) is None
    assert shared.used == 19
    series.put(("page 4",), {}, b"4" * 11)  # not even with all of its own
    assert series.get(("page 4",)) is None and series.get(("page 3",)) is not None
    assert shared.used == 19 and other.get(("other",)) is not None
    series.close()
    other.close()
    assert shared.used == 0


def test_dropping_for_its_own_limits_gives_the_bytes_back():
    shared = render_module.KeptBytes(100)
    answers = render_module.PdfAnswers(items=2, max_bytes=50, shared=shared)
    for n in range(5):
        answers.put((n,), {}, b"x" * 10)
    assert shared.used == 20  # two kept
    answers.put((4,), {}, b"x")  # replaced by a smaller one
    assert shared.used == 11
    answers.close()
    assert shared.used == 0


def test_a_limited_block_gives_its_kept_drawings_back(monkeypatch):
    _SlowChild(monkeypatch, 0.1, drawing=True)
    shared = render_module.KeptBytes(render_module.PDF_KEPT_ALL_BYTES)
    monkeypatch.setattr(render_module, "_KEPT_BYTES", shared)
    with limited(10**7):
        render(_pages(1), 100)
        assert shared.used > 0
    assert shared.used == 0
    assert render_module.PDF_KEPT_ALL_BYTES >= render_module.PDF_KEPT_BYTES


def test_without_limits_a_child_has_its_own_time(monkeypatch):
    child = _SlowChild(monkeypatch, 0.2)
    pdfchild.ask(b"", {})
    assert child.timeouts == [pdfchild.SECONDS]
    assert pdfchild._seconds() == (pdfchild.SECONDS, False)


def test_a_child_cut_short_by_the_budget_is_refused(monkeypatch):
    child = _SlowChild(monkeypatch, 10.0)
    monkeypatch.setattr(render_module, "_PDF_BUDGET", render_module.ContextVar("b"))
    render_module._PDF_BUDGET.set(render_module.PdfBudget(5))
    assert pdfchild._seconds() == (5, True)  # what is left, below SECONDS
    with pytest.raises(pdfchild.ChildLimit, match="their time"):
        pdfchild.ask(b"", {})
    assert child.timeouts == [5] and render_module.pdf_seconds_left() == 0
    with pytest.raises(pdfchild.ChildLimit, match="their time"):
        pdfchild.ask(b"", {})  # used up: not started
    assert child.timeouts == [5]
    # a budget larger than SECONDS: the child's own limit
    child.seconds = pdfchild.SECONDS + 1
    render_module._PDF_BUDGET.set(render_module.PdfBudget(100))
    with pytest.raises(pdfchild.ChildLimit, match="more than"):
        pdfchild.ask(b"", {})
    assert child.timeouts[-1] == pdfchild.SECONDS


def test_a_line_too_long_is_a_warning_within_limits_too(make_server):
    """The server's preview of a 5000-character line: text_too_long."""
    _server, base = make_server()
    text = {"kind": "text", "text": "x" * 5000}
    content = {"printer": "fake-mono", "tape_mm": 12, "elements": [text]}
    body = {"label_content": content, "format": "json"}
    status, _response, answer = call(base, "POST", "/api/v1/preview", body=body)
    assert status == 200
    assert [w["code"] for w in answer["warnings"]] == ["text_too_long"]
    assert "one line" in answer["warnings"][0]["message"]
    # 99 lines (the most) at 200 pt: too tall
    text.update(text="\n".join(["x"] * 99), size_pt=200)
    status, _response, answer = call(base, "POST", "/api/v1/preview", body=body)
    assert status == 200
    assert [w["code"] for w in answer["warnings"]] == ["text_too_tall"]
    assert "fewer lines" in answer["warnings"][0]["message"]
