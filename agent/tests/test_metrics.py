"""metrics-server 사용량 파싱/집계 헬퍼 테스트."""

import os

os.environ.setdefault("CANGGU_MOCK", "1")

from app.kube import _cpu_to_nano, _max_usage, _mem_to_bytes  # noqa: E402


def test_cpu_to_nano():
    assert _cpu_to_nano("123456n") == 123456
    assert _cpu_to_nano("18m") == 18_000_000
    assert _cpu_to_nano("1") == 1_000_000_000
    assert _cpu_to_nano("") == -1


def test_mem_to_bytes():
    assert _mem_to_bytes("1024Ki") == 1024 * 1024
    assert _mem_to_bytes("256Mi") == 256 * 1024**2
    assert _mem_to_bytes("") == -1


def test_max_usage_picks_largest_across_pods():
    metrics = {
        ("ns", "p1"): {"c": ("100000000n", "100Mi")},   # 0.1 core
        ("ns", "p2"): {"c": ("450m", "500Mi")},          # 0.45 core  ← max
        ("ns", "p3"): {"c": ("200m", "300Mi")},
    }
    cpu, mem = _max_usage(metrics, "ns", ["p1", "p2", "p3"], "c")
    assert cpu == "450m"
    assert mem == "500Mi"


def test_max_usage_missing_metrics():
    cpu, mem = _max_usage({}, "ns", ["p1"], "c")
    assert cpu == "" and mem == ""
