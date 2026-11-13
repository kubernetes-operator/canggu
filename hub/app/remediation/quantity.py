"""Kubernetes resource quantity 파싱/포맷 (경량, Phase 0 범위).

CPU 는 cores(float), memory 는 bytes(int) 로 정규화한다.
"""

from __future__ import annotations

_CPU_SUFFIX = {"n": 1e-9, "u": 1e-6, "m": 1e-3}
_MEM_SUFFIX = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
    "k": 1000,
}


def parse_cpu(q: str) -> float | None:
    """CPU quantity -> cores. 빈 문자열/파싱 실패 시 None."""
    q = (q or "").strip()
    if not q:
        return None
    try:
        if q[-1] in _CPU_SUFFIX:
            return float(q[:-1]) * _CPU_SUFFIX[q[-1]]
        return float(q)
    except ValueError:
        return None


def parse_mem(q: str) -> int | None:
    """Memory quantity -> bytes. 빈 문자열/파싱 실패 시 None."""
    q = (q or "").strip()
    if not q:
        return None
    try:
        for suf in ("Ki", "Mi", "Gi", "Ti", "Pi"):
            if q.endswith(suf):
                return int(float(q[:-2]) * _MEM_SUFFIX[suf])
        if q and q[-1] in _MEM_SUFFIX:  # 단일 문자 접미사 (K/M/G/T/k)
            return int(float(q[:-1]) * _MEM_SUFFIX[q[-1]])
        return int(float(q))
    except ValueError:
        return None


def format_cpu(cores: float) -> str:
    """cores -> millicores 문자열 (예: 0.35 -> '350m')."""
    m = max(1, round(cores * 1000))
    return f"{m}m"


def format_mem(byts: int) -> str:
    """bytes -> Mi 문자열 (올림, 예: 268435456 -> '256Mi')."""
    mi = max(1, -(-int(byts) // (1024**2)))  # ceil
    return f"{mi}Mi"
