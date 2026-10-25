import sys
from types import ModuleType, SimpleNamespace

import pytest

from decilist import LiquidLocalProvider


@pytest.fixture
def vendor(monkeypatch):
    package = "decilist_test_pinned_liquid"
    parent = ModuleType(package)
    parent.__path__ = []
    hybrid = ModuleType(package + ".hybrid")
    exec("""
fused_queries = []
def _fused(query):
    fused_queries.append(query)
    return query.is_cuda and query.half and query.supported_architecture
def _explicit(*args):
    return "existing-vendor-explicit"
def _over_trunk(query):
    return "vendor-flash" if _fused(query) else _explicit(query)
def _own(query):
    return "vendor-flash" if _fused(query) else _explicit(query)
""", hybrid.__dict__)
    module = ModuleType(package + ".modeling_d1")
    calls = []
    class Model:
        def to(self, device):
            calls.append(("to", device))
            return self
        def eval(self):
            calls.append(("eval",))
            return self
    Model.__module__ = module.__name__
    module.D1Model = Model
    for name, value in [(package, parent), (hybrid.__name__, hybrid), (module.__name__, module)]:
        monkeypatch.setitem(sys.modules, name, value)
    availability = []
    torch = ModuleType("torch")
    torch.float32, torch.bfloat16 = "float32", "bfloat16"
    def available():
        availability.append("checked")
        return False
    torch.backends = SimpleNamespace(cuda=SimpleNamespace(is_flash_attention_available=available))
    transformers = ModuleType("transformers")
    def load(snapshot, **kwargs):
        calls.append(("load", snapshot, kwargs))
        return Model()
    transformers.AutoModel = SimpleNamespace(from_pretrained=load)
    hub = ModuleType("huggingface_hub")
    def snapshot_download(repo, **kwargs):
        calls.append(("snapshot", repo, kwargs))
        return "pinned-cached-snapshot"
    hub.snapshot_download = snapshot_download
    for name, value in [("torch", torch), ("transformers", transformers), ("huggingface_hub", hub)]:
        monkeypatch.setitem(sys.modules, name, value)
    return SimpleNamespace(model_class=Model, hybrid=hybrid, torch=torch, calls=calls, availability=availability)


def query(*, cuda=True, half=True, architecture=True):
    return SimpleNamespace(is_cuda=cuda, half=half, supported_architecture=architecture)


def test_cuda_without_compiled_flash_uses_existing_vendor_fallback_without_device_change(vendor):
    original, explicit = vendor.hybrid._fused, vendor.hybrid._explicit
    provider = LiquidLocalProvider.load(allow_model_code=True, device="cuda:1")
    assert provider.attention_backend == "vendor-explicit"
    assert type(provider._model) is vendor.model_class
    assert vendor.calls == [
        ("snapshot", LiquidLocalProvider.model, {"revision": LiquidLocalProvider.revision, "local_files_only": True}),
        ("load", "pinned-cached-snapshot", {"trust_remote_code": True, "local_files_only": True, "dtype": "bfloat16"}),
        ("to", "cuda:1"), ("eval",),
    ]
    assert vendor.availability == ["checked"]
    assert vendor.hybrid._explicit is explicit
    assert vendor.hybrid._fused._decilist_vendor_fused is original
    assert vendor.hybrid._over_trunk(query()) == "existing-vendor-explicit"
    assert vendor.hybrid._own(query()) == "existing-vendor-explicit"
    assert vendor.hybrid.fused_queries == []


def test_compiled_flash_keeps_vendor_predicate_and_architecture_dtype_checks(vendor):
    vendor.torch.backends.cuda.is_flash_attention_available = lambda: True
    original = vendor.hybrid._fused
    provider = LiquidLocalProvider.load(allow_model_code=True, device="cuda")
    assert provider.attention_backend == "vendor-default"
    assert vendor.hybrid._fused is original
    assert vendor.hybrid._over_trunk(query()) == "vendor-flash"
    assert vendor.hybrid._own(query(half=False)) == "existing-vendor-explicit"
    assert vendor.hybrid._own(query(architecture=False)) == "existing-vendor-explicit"


@pytest.mark.parametrize("device", ["cpu", "cpu:0", "mps", "xpu"])
def test_non_cuda_preserves_vendor_predicate_without_cuda_build_probe(vendor, device):
    def must_not_probe():
        pytest.fail("non-CUDA loading must not require a CUDA build probe")
    vendor.torch.backends.cuda.is_flash_attention_available = must_not_probe
    original = vendor.hybrid._fused
    provider = LiquidLocalProvider.load(allow_model_code=True, device=device)
    assert provider.attention_backend == "vendor-default"
    assert vendor.hybrid._fused is original
    assert vendor.hybrid._over_trunk(query(cuda=False)) == "existing-vendor-explicit"
    assert ("to", device) in vendor.calls


def test_repeated_loads_do_not_stack_adapters_or_touch_an_unrelated_module(vendor, monkeypatch):
    unrelated = ModuleType("unrelated.hybrid")
    unrelated._fused = lambda q: "unrelated"
    monkeypatch.setitem(sys.modules, unrelated.__name__, unrelated)
    original = vendor.hybrid._fused
    LiquidLocalProvider.load(allow_model_code=True)
    patched = vendor.hybrid._fused
    LiquidLocalProvider.load(allow_model_code=True)
    assert vendor.hybrid._fused is patched
    assert unrelated._fused(None) == "unrelated"
    vendor.torch.backends.cuda.is_flash_attention_available = lambda: True
    provider = LiquidLocalProvider.load(allow_model_code=True)
    assert provider.attention_backend == "vendor-default"
    assert vendor.hybrid._fused is original


@pytest.mark.parametrize("hook", ["_fused", "_explicit"])
def test_missing_pinned_hook_fails_clearly(vendor, hook):
    delattr(vendor.hybrid, hook)
    with pytest.raises(RuntimeError, match="hooks _fused/_explicit are missing"):
        LiquidLocalProvider.load(allow_model_code=True)


def test_missing_hybrid_sibling_fails_clearly(vendor, monkeypatch):
    monkeypatch.delitem(sys.modules, vendor.hybrid.__name__)
    with pytest.raises(RuntimeError, match="hybrid attention module is missing"):
        LiquidLocalProvider.load(allow_model_code=True)


def test_unknown_model_module_is_not_patched(vendor):
    class Unrelated:
        pass
    with pytest.raises(RuntimeError, match="Cannot locate the pinned Liquid"):
        LiquidLocalProvider._configure_attention(Unrelated(), vendor.torch, device="cuda")


@pytest.mark.parametrize("probe", [None, lambda: "unknown"])
def test_unknown_build_availability_fails_instead_of_guessing(vendor, probe):
    vendor.torch.backends.cuda.is_flash_attention_available = probe
    with pytest.raises(RuntimeError, match="Flash Attention"):
        LiquidLocalProvider.load(allow_model_code=True)
