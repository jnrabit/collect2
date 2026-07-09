"""Ad-hoc-Dateikontext (Teil A): Gate, Lesegrenzen, Chunk-Auswahl, Pipeline."""

import numpy as np
import pytest

from collect.config import settings
from collect.retrieval.filecontext import (
    build_file_context,
    detect_paths,
    path_allowed,
    read_sources,
)
from tests.conftest import fake_embedding


@pytest.fixture
def allow(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "read_paths", [tmp_path])
    return tmp_path


def _embed(texts):
    return np.stack([fake_embedding(t) for t in texts])


# ── detect_paths ──────────────────────────────────────────────────────────

def test_detect_paths_finds_existing(tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("x = 1")
    assert detect_paths(f"lies und analysiere {f}") == [str(f)]


def test_detect_paths_ignores_nonexistent():
    assert detect_paths("was ist mit /gibt/es/nicht.py") == []


def test_detect_paths_none_without_path():
    assert detect_paths("was ist quantenverschränkung") == []


def test_detect_paths_multiple(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("1"); b.write_text("2")
    found = detect_paths(f"vergleiche {a} und {b}")
    assert set(found) == {str(a), str(b)}


# ── path_allowed (Sicherheit) ─────────────────────────────────────────────

def test_path_allowed_inside_and_outside(allow, tmp_path):
    inside = tmp_path / "ok.py"; inside.write_text("x")
    assert path_allowed(inside) is True
    assert path_allowed("/etc/passwd") is False


def test_symlink_escape_rejected(allow, tmp_path):
    # Symlink INNERHALB der Allowlist, der nach /etc zeigt → muss scheitern
    link = tmp_path / "escape"
    try:
        link.symlink_to("/etc")
    except OSError:
        pytest.skip("keine Symlink-Rechte")
    assert path_allowed(link / "passwd") is False


def test_dotdot_escape_rejected(allow, tmp_path):
    assert path_allowed(f"{tmp_path}/../../etc/passwd") is False


# ── read_sources: Limits ──────────────────────────────────────────────────

def test_read_single_file(allow, tmp_path):
    f = tmp_path / "s.py"; f.write_text("print('hi')")
    srcs = read_sources(str(f))
    assert len(srcs) == 1 and "print" in srcs[0].content


def test_read_rejects_outside_allowlist(allow):
    assert read_sources("/etc/passwd") == []


def test_dir_extension_filter_and_binary_skip(allow, tmp_path, monkeypatch):
    (tmp_path / "keep.py").write_text("code")
    (tmp_path / "skip.exe").write_text("nope")           # nicht in Endungs-Allowlist
    (tmp_path / "bin.py").write_bytes(b"\x00\x01\x02bin")  # Binär → übersprungen
    paths = {s.path for s in read_sources(str(tmp_path))}
    assert str(tmp_path / "keep.py") in paths
    assert str(tmp_path / "skip.exe") not in paths
    assert str(tmp_path / "bin.py") not in paths


def test_dir_max_files_limit(allow, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_max_files", 3)
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text(f"x{i}")
    assert len(read_sources(str(tmp_path))) == 3


# ── build_file_context: klein direkt, groß chunk-selektiert ───────────────

def test_small_file_goes_whole(allow, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_direct_threshold", 8192)
    f = tmp_path / "small.py"; f.write_text("def add(a, b):\n    return a + b\n")
    ctx = build_file_context("was macht add?", [str(f)], _embed)
    assert ctx.has_content and len(ctx.chunks) == 1
    assert "return a + b" in ctx.chunks[0]["text"]


def test_large_file_selects_relevant_chunk(allow, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_direct_threshold", 100)
    monkeypatch.setattr(settings, "file_chunk_chars", 60)
    monkeypatch.setattr(settings, "file_top_chunks", 2)
    lines = ["# fueller zeile ohne bezug\n"] * 20
    lines.insert(10, "def quantum_entanglement_helper():\n")
    f = tmp_path / "big.py"; f.write_text("".join(lines))

    # Deterministischer Marker-Embedder: Chunk mit dem Term ist zur Query
    # ausgerichtet (cos=1), Rest orthogonal (cos=0). Testet die Top-k-Auswahl,
    # nicht die (nicht-semantische) hash-basierte fake_embedding.
    term = "quantum_entanglement_helper"

    def marker_embed(texts):
        return np.array([[1.0, 0.0] if term in t else [0.0, 1.0]
                         for t in texts], dtype=np.float32)

    ctx = build_file_context(term, [str(f)], marker_embed)
    assert ctx.has_content
    assert any(term in c["text"] for c in ctx.chunks)


def test_rejected_path_in_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "read_paths", [tmp_path / "erlaubt"])
    (tmp_path / "erlaubt").mkdir()
    outside = tmp_path / "outside.py"; outside.write_text("x")
    ctx = build_file_context("q", [str(outside)], _embed)
    assert not ctx.has_content and str(outside) in ctx.rejected
