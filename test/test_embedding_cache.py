from artmind.embedding_cache import EmbeddingCache, cached_embed


def test_roundtrip_and_miss(tmp_path):
    cache = EmbeddingCache(tmp_path / "c.sqlite")
    k = EmbeddingCache.key("ek1", "text")
    assert cache.get(k) is None
    cache.put(k, [0.25, -1.5])
    assert EmbeddingCache(tmp_path / "c.sqlite").get(k) == [0.25, -1.5]


def test_key_depends_on_embed_key_and_text():
    assert EmbeddingCache.key("a", "t") != EmbeddingCache.key("b", "t")
    assert EmbeddingCache.key("a", "t") != EmbeddingCache.key("a", "u")


def test_cached_embed_calls_the_model_once(tmp_path):
    cache = EmbeddingCache(tmp_path / "c.sqlite")
    calls = []
    embed = lambda text: calls.append(text) or [1.0]
    assert cached_embed(cache, "ek", "hello", embed) == [1.0]
    assert cached_embed(cache, "ek", "hello", embed) == [1.0]
    assert calls == ["hello"]


def test_unusable_location_disables_the_cache(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = EmbeddingCache(blocker / "sub" / "c.sqlite")   # parent is a file
    cache.put("k", [1.0])
    assert cache.get("k") is None
