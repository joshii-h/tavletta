"""The backend registry, the shared types in base.py and the contract for
the test-only mono backend."""

from types import SimpleNamespace

import pytest
from backend_contract import BackendContract
from fake_backends import FakeMono
from PIL import Image

import tavletta.backends as registry
from tavletta.backends import backends, get_backend
from tavletta.backends.base import (
    Backend,
    ColorAdjust,
    LabelTooLong,
    PrinterError,
    PrinterInfo,
    PrintMode,
    PrintOptions,
    Raster,
    apply_color_model,
)
from tavletta.backends.vc500w import VC500W
from tavletta.model import DEFAULT_PRINTER, Label


@pytest.fixture
def mono(monkeypatch):
    backend = FakeMono()
    registry.register(backend)
    yield backend
    registry.unregister(backend.id)


# --- registry ------------------------------------------------------------------


def test_default_backend_is_registered():
    backend = get_backend()
    assert backend.id == DEFAULT_PRINTER == "vc500w"
    assert isinstance(backend, VC500W)
    assert get_backend("vc500w") is backend


def test_unknown_backend():
    with pytest.raises(KeyError):
        get_backend("no-such-printer")


def test_register_orders_default_first(mono):
    # the default first, then by name, whatever the case
    # names: Brother P-touch, Brother QL (twice, in the order registered), cab,
    # CUPS / IPP, DYMO, ESC/POS, Fake Mono, NIIMBOT, TSPL, Zebra EPL2, Zebra ZPL
    ql = ["brother_ql", "brother_ql_diecut"]
    builtin = ["ptouch", *ql, "cab", "system", "dymo", "escpos"]
    assert [b.id for b in backends()] == [
        "vc500w",
        *builtin,
        "fake-mono",
        "niimbot",
        "tspl",
        "epl",
        "zpl",
    ]
    assert get_backend("fake-mono") is mono
    registry.unregister("fake-mono")
    registry.unregister("fake-mono")  # twice is fine
    assert [b.id for b in backends()] == ["vc500w", *builtin, "niimbot", "tspl", "epl", "zpl"]


def test_register_needs_an_id():
    class Nameless(FakeMono):
        id = ""

    with pytest.raises(ValueError):
        registry.register(Nameless())


def test_entry_points(monkeypatch):
    class Plugin(FakeMono):
        id = "plugin"

    entries = [
        SimpleNamespace(name="class", load=lambda: Plugin),
        SimpleNamespace(name="instance", load=lambda: FakeMono()),
        SimpleNamespace(name="broken", load=lambda: 1 / 0),
    ]
    groups = []

    def entry_points(group):
        groups.append(group)
        return entries

    monkeypatch.setattr(registry, "entry_points", entry_points)
    monkeypatch.setattr(registry, "_loaded", False)
    monkeypatch.setattr(registry, "_registry", {})
    ids = [b.id for b in backends()]
    assert groups == ["tavletta.backends"]
    assert ids[0] == "vc500w"
    assert sorted(ids[1:]) == sorted(
        [
            "brother_ql",
            "brother_ql_diecut",
            "cab",
            "fake-mono",
            "niimbot",
            "plugin",
            "ptouch",
            "system",
            "zpl",
            "epl",
            "tspl",
            "escpos",
            "dymo",
        ]
    )
    backends()  # loaded once only
    assert groups == ["tavletta.backends"]


def test_every_backend_has_a_contract_test():
    import test_brother_ql  # noqa: F401 - define their BackendContract subclasses
    import test_cab  # noqa: F401
    import test_niimbot  # noqa: F401
    import test_ptouch  # noqa: F401
    import test_raw_backends  # noqa: F401
    import test_system  # noqa: F401
    import test_vc500w  # noqa: F401

    tested = {cls.backend_id for cls in BackendContract.__subclasses__()}
    assert {b.id for b in backends()} <= tested


# --- shared types ----------------------------------------------------------------


@pytest.mark.parametrize(
    "data, expected",
    [
        (None, ColorAdjust()),
        ("junk", ColorAdjust()),
        ({}, ColorAdjust()),
        ({"brightness": 2, "red": -1}, ColorAdjust(2, -1, 0, 0)),
        ({"brightness": 9, "green": -7.4, "blue": 1.6}, ColorAdjust(3, 0, -3, 2)),
        ({"red": True, "green": "2", "blue": None}, ColorAdjust()),
        # settings.json read by Python's json: NaN and Infinity are numbers
        ({"brightness": float("nan"), "red": float("inf")}, ColorAdjust()),
        ({"green": float("-inf"), "blue": 10**400}, ColorAdjust(0, 0, 0, 0)),
        ({"brightness": 2.5, "red": -2.5}, ColorAdjust(2, -2, 0, 0)),  # rounded half to even
        ({"brightness": 3, "red": -3}, ColorAdjust(3, -3, 0, 0)),
    ],
)
def test_color_adjust_from_dict(data, expected):
    assert ColorAdjust.from_dict(data) == expected


def test_color_adjust_round_trip_and_neutral():
    adjust = ColorAdjust(1, -2, 3, 0)
    assert adjust.to_dict() == {"brightness": 1, "red": -2, "green": 3, "blue": 0}
    assert ColorAdjust.from_dict(adjust.to_dict()) == adjust
    assert ColorAdjust().is_neutral() and not adjust.is_neutral()
    assert not ColorAdjust(blue=-1).is_neutral()


def test_printer_error_keeps_details_and_params():
    error = LabelTooLong("17.5 in", max_mm=431.8)
    assert isinstance(error, PrinterError) and error.code == "too_long"
    assert error.details == str(error) == "17.5 in"
    assert error.params == {"max_mm": 431.8}
    assert PrinterError().code == "generic" and PrinterError().details == ""


def test_apply_color_model_dithers_mono_only():
    grey = Image.new("RGB", (40, 40), (128, 128, 128))
    dithered = apply_color_model(grey, "mono")
    colours = {c: n for n, c in dithered.getcolors()}
    assert set(colours) == {(0, 0, 0), (255, 255, 255)}
    assert abs(colours[(0, 0, 0)] - 800) < 80  # half black: Floyd-Steinberg, not a threshold
    assert apply_color_model(grey, "color") is grey
    assert apply_color_model(grey, "two-color") is grey


class Minimal(Backend):
    """Only what a backend must implement: the defaults of base.Backend."""

    id = "minimal"
    name = "Minimal"
    capabilities = FakeMono.capabilities

    def discover(self, timeout=3.0):
        return []

    def query(self, host):
        return PrinterInfo("Minimal", None, None, None, "IDLE")

    def print_label(self, image, host, options, progress=None):
        pass


def test_backend_defaults():
    backend = Minimal()
    assert backend.configured_host() is None
    assert backend.raster(12, PrintOptions()) == Raster(203)
    reported = (PrintMode("fine", 600),)
    assert backend.modes(None) == FakeMono.capabilities.modes
    assert backend.modes(PrinterInfo("m", 12, None, None, "IDLE")) == FakeMono.capabilities.modes
    info = PrinterInfo("m", 12, None, None, "IDLE", modes=reported)
    assert backend.modes(info) == reported


def test_prepare_crops_to_the_raster_width(monkeypatch):
    backend = Minimal()
    monkeypatch.setattr(
        backend, "raster", lambda tape, options: Raster(203, tape_px=90, bleed_px=5, width_px=99)
    )
    image = backend.prepare(Label(tape_mm=12, background="#000000"), PrintOptions())
    assert image.height == 99  # 90 + 2 × 5, one row cut off


# --- the contract for the test backend -------------------------------------------


class TestFakeMono(BackendContract):
    backend_id = "fake-mono"

    @pytest.fixture
    def harness(self):
        backend = FakeMono()

        def fail():
            backend.error = PrinterError("out of paper")

        return SimpleNamespace(backend=backend, fail=fail, printed=lambda: len(backend.jobs))


def test_a_code_too_large_for_the_tape_is_not_printed():
    from tavletta.backends.base import CodeTooLarge
    from tavletta.model import CodeElement, Label

    label = Label(tape_mm=9, elements=[CodeElement("x" * 1000)])  # 105 modules on 9 mm
    with pytest.raises(CodeTooLarge):
        get_backend("vc500w").prepare(label, PrintOptions())


def test_the_render_resolution_follows_the_print():
    """Backend.render_dpi: the resolution a label is printed at, from the
    options as the print has them (preview and command line check)."""
    from tavletta.backends.base import PrinterInfo, PrintOptions

    zpl = get_backend("zpl")
    assert zpl.render_dpi(0, PrintOptions(settings={"dpi": "300"})) == pytest.approx(12 * 25.4)
    assert zpl.render_dpi(0, PrintOptions(settings={"dpi": "600"})) == pytest.approx(24 * 25.4)
    ql = get_backend("brother_ql")
    assert ql.render_dpi(62, PrintOptions()) == 300
    assert ql.render_dpi(62, PrintOptions(quality="high")) == 600
    ptouch = get_backend("ptouch")
    assert ptouch.render_dpi(12, PrintOptions()) == 180
    assert ptouch.render_dpi(12, PrintOptions(high_res=True)) == 360
    niimbot = get_backend("niimbot")
    b32 = PrinterInfo("B32", None, None, None, "IDLE")
    assert niimbot.render_dpi(0, PrintOptions(info=b32)) == pytest.approx(300)
    system = get_backend("system")
    office = PrinterInfo("Office", None, None, None, "IDLE", dpi=600)
    assert system.render_dpi(0, PrintOptions(info=office)) == 600
    assert system.render_dpi(0, PrintOptions(info=office), max_dpi=400) == 400
    assert get_backend("ptouch").raster(12, PrintOptions(high_res=True)).across_step == 2
    assert get_backend("vc500w").raster(24, PrintOptions()).across_step == 1


def test_every_continuous_backend_says_what_its_tape_comes_as():
    """Capabilities.media: a cassette or roll the printer recognises, or
    paper whose width the user chooses (one field, no second flag)."""
    for backend in backends():
        assert backend.capabilities.media in ("cassette", "roll", "paper")
    assert get_backend("escpos").capabilities.media == "paper"
    assert get_backend("brother_ql").capabilities.media == "roll"
    assert get_backend("vc500w").capabilities.media == "cassette"


def test_one_map_error_and_one_connect_for_the_tcp_and_usb_printers(monkeypatch):
    """raw, cab, QL and P-touch share base.map_error and net.connect; on
    USB the connection gets the backend's minimum rate like TCP."""
    from tavletta.backends import base, brother_ql, cab, net, ptouch, raw, usb

    assert raw.map_error is cab.map_error is brother_ql.map_error is ptouch.map_error
    assert raw.map_error is base.map_error
    assert type(base.map_error(OSError("unreachable"))) is base.PrinterNotFound
    assert type(base.map_error(ValueError("cannot resolve x"))) is base.PrinterNotFound
    assert type(base.map_error(ValueError("odd"))) is base.PrinterError

    class Connection:
        min_rate = usb.MIN_RATE

    made = []
    monkeypatch.setattr(
        usb, "connect", lambda host, timeout, wait: made.append(host) or Connection()
    )
    link = net.connect("usb://Brother/QL-820NWB", 9100, 10.0, 5.0, brother_ql.MIN_RATE)
    assert made and link.min_rate == brother_ql.MIN_RATE


def test_backends_offer_only_known_extras_and_cut_modes():
    from tavletta.backends import base

    for backend in backends():
        assert set(backend.capabilities.extras) <= set(base._EXTRAS), backend.id
        assert set(backend.capabilities.cut_modes) <= set(base.CUT_MODES), backend.id


def test_host_memory_forgets_the_least_recently_used_printer():
    """What backends learn per printer address stays bounded (a server whose
    --allow-printer pattern admits any address)."""
    from tavletta.backends import brother_ql, cab, niimbot, zpl
    from tavletta.backends.base import HOSTS_REMEMBERED, HostMemory

    memory: HostMemory[int] = HostMemory(3)
    for n, host in enumerate("abc"):
        memory[host] = n
    assert memory.get("a") == 0  # used: kept
    memory["d"] = 3
    assert list(memory) == ["c", "a", "d"] and "b" not in memory
    assert memory.get("b", -1) == -1 and memory == {"c": 2, "a": 0, "d": 3}
    memory["c"] = 5  # written again: the most recent
    memory["e"] = 4
    assert list(memory) == ["d", "c", "e"]  # "a" forgotten
    assert memory.pop("c") == 5 and memory.pop("x", None) is None

    backend = cab.Cab()
    for n in range(HOSTS_REMEMBERED + 10):
        backend._learn("192.0.2.%d" % n, 12.0)
    assert len(backend.reported) == HOSTS_REMEMBERED
    assert isinstance(cab._setups, HostMemory)
    assert isinstance(niimbot.Niimbot().models, HostMemory)
    assert isinstance(zpl.Zpl().reported, HostMemory)
    assert isinstance(brother_ql._found_models, HostMemory)


def test_host_memory_methods_keep_the_limit_and_the_order():
    from tavletta.backends.base import HostMemory

    memory: HostMemory[int] = HostMemory(2)
    assert memory.setdefault("a", 1) == 1 and memory.setdefault("a", 9) == 1
    memory.update({"b": 2}, c=3)  # through __setitem__: the limit holds
    assert list(memory.items()) == [("b", 2), ("c", 3)]
    assert "b" in memory and "a" not in memory and 5 not in memory
    del memory["b"]
    assert memory.popitem() == ("c", 3) and len(memory) == 0
    memory |= {"x": 1, "y": 2, "z": 3}
    assert isinstance(memory, HostMemory) and list(memory) == ["y", "z"]
    memory.clear()
    assert memory == {}


def test_host_memory_copies_and_pickles():
    import copy
    import pickle

    from tavletta.backends.base import HostMemory

    memory: HostMemory[int] = HostMemory(3)
    memory.update(a=1, b=2)
    for twin in (
        copy.copy(memory),
        memory.copy(),
        copy.deepcopy(memory),
        pickle.loads(pickle.dumps(memory)),
    ):
        assert isinstance(twin, HostMemory) and twin.size == 3
        assert list(twin.items()) == [("a", 1), ("b", 2)]
        assert twin._lock is not memory._lock
        twin["c"] = 3
        assert "c" not in memory  # a copy of its own
    merged = memory | {"c": 3, "d": 4}
    assert isinstance(merged, HostMemory) and merged.size == 3
    assert list(merged) == ["b", "c", "d"] and list(memory) == ["a", "b"]
    merged = {"z": 0} | memory
    assert isinstance(merged, HostMemory) and list(merged.items()) == [
        ("z", 0),
        ("a", 1),
        ("b", 2),
    ]
    assert memory.__or__(5) is NotImplemented and memory.__ror__(5) is NotImplemented


def test_host_memory_from_many_threads():
    """Threads writing, reading and dropping at once: no error, and never
    more than the limit."""
    import threading

    from tavletta.backends.base import HostMemory

    memory: HostMemory[int] = HostMemory(8)
    errors: list[BaseException] = []

    def work(offset: int) -> None:
        try:
            for n in range(2000):
                host = "192.0.2.%d" % ((n + offset) % 20)
                memory[host] = n
                memory.get(host)
                memory.setdefault(host, n)
                if host in memory:
                    memory.pop(host, None)
                assert len(memory) <= 8
        except BaseException as e:  # noqa: BLE001 - collected for the assertion
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(memory) <= 8


def test_host_memory_is_read_through_snapshots():
    """Iterating, its views, len, ==, !=, repr: under the lock, over a copy:
    another thread may change it meanwhile without a RuntimeError."""
    import operator
    import threading

    from tavletta.backends.base import HostMemory

    memory: HostMemory[int] = HostMemory(3)
    memory.update(a=1, b=2)
    keys, values, items = memory.keys(), memory.values(), memory.items()
    memory["c"] = 3  # the views are snapshots
    assert list(keys) == ["a", "b"] and list(values) == [1, 2]
    assert list(items) == [("a", 1), ("b", 2)]
    for host in memory:  # changed while iterating
        memory.pop(host)
        memory["x" + host] = 0
    assert len(memory) == 3 and list(reversed(memory)) == ["xc", "xb", "xa"]
    memory.move_to_end("xa")
    assert list(memory) == ["xb", "xc", "xa"]
    assert memory == {"xb": 0, "xc": 0, "xa": 0}
    twin: HostMemory[int] = HostMemory(3)
    twin.update(xb=0, xc=0, xa=0)
    assert memory == twin and memory != HostMemory(3)
    assert repr(memory) == "<HostMemory size=3 {'xb': 0, 'xc': 0, 'xa': 0}>"
    assert operator.ne(memory, twin) is False and memory != {"xb": 0} and memory != 5
    calls = []
    real = HostMemory._items

    def counted(self):
        calls.append(self)
        return real(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(HostMemory, "_items", counted)
        assert operator.ne(memory, twin) is False  # through the snapshots, locked
    assert any(c is memory for c in calls) and any(c is twin for c in calls)
    errors: list[BaseException] = []

    def writer() -> None:
        for n in range(3000):
            memory["192.0.2.%d" % (n % 50)] = n

    def reader() -> None:
        try:
            for _ in range(300):
                list(memory)
                list(memory.items())
                assert len(memory) <= 3
        except BaseException as e:  # noqa: BLE001 - collected for the assertion
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_methods_set_on_a_backend_are_dropped_before_each_test():
    """conftest: a method left on a backend singleton's instance (what some
    pytest versions' monkeypatch.undo() puts back: the bound method, which
    hides any patch of the class) is dropped before each test; its data
    stays."""
    from conftest import drop_instance_overrides

    from tavletta.backends import backends, get_backend

    # the autouse fixture ran before this test: no method on any instance
    for backend in backends():
        assert not [
            name
            for name, value in vars(backend).items()
            if callable(value) and callable(getattr(type(backend), name, None))
        ], backend.id
    zpl = get_backend("zpl")
    reported = zpl.reported
    try:
        zpl.query = zpl.query  # the bound method, as an instance attribute
        assert "query" in vars(zpl)
        assert drop_instance_overrides() == ["zpl.query"]
    finally:
        vars(zpl).pop("query", None)
    assert "query" not in vars(zpl) and zpl.reported is reported  # data stays
    assert drop_instance_overrides() == []
