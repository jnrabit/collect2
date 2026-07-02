"""Vault-Cipher + Roundtrip pinnen. Format-Kompatibilität zu den Alt-Vaults
wurde zusätzlich gegen die Produktivdaten verifiziert (258.873 + 1.469 Docs)."""

import numpy as np

from collect.retrieval.vault import Vault, chaos_cipher, _password_seed


def test_cipher_is_symmetric():
    data = np.frombuffer(b"collect2 roundtrip \x00\xff\x80 test", dtype=np.uint8)
    seed = _password_seed("irgendein-key")
    once = chaos_cipher(data, seed)
    twice = chaos_cipher(once, seed)
    assert bytes(twice) == bytes(data)
    assert bytes(once) != bytes(data)


def test_cipher_wrong_seed_differs():
    data = np.frombuffer(b"x" * 64, dtype=np.uint8)
    a = chaos_cipher(data, _password_seed("key-a"))
    b = chaos_cipher(data, _password_seed("key-b"))
    assert bytes(a) != bytes(b)


def test_vault_roundtrip(tmp_path):
    docs = [
        {"id": "doc-1", "title": "Erster", "content": "Inhalt mit Ümläuten"},
        {"id": "doc-2", "title": "Zweiter", "content": "x" * 1000},
    ]
    v = Vault(tmp_path / "test.monolith")
    v.save(docs)
    assert Vault(tmp_path / "test.monolith").load() == docs


def test_vault_wrong_password_fails_gracefully(tmp_path):
    v = Vault(tmp_path / "t.monolith", password="richtig")
    v.save([{"id": 1}])
    assert Vault(tmp_path / "t.monolith", password="falsch").load() == []


def test_vault_missing_and_empty_file(tmp_path):
    assert Vault(tmp_path / "fehlt.monolith").load() == []
    (tmp_path / "leer.monolith").write_bytes(b"")
    assert Vault(tmp_path / "leer.monolith").load() == []
