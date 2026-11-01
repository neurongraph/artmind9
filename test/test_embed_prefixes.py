from artmind import extraction, vector_query


def test_prefixes_by_model_base_name():
    assert extraction.embed_prefixes("nomic-embed-text:latest") == ("search_document: ", "search_query: ")
    assert extraction.embed_prefixes("library/nomic-embed-text:v1.5") == ("search_document: ", "search_query: ")
    assert extraction.embed_prefixes("mxbai-embed-large") == ("", "")


def test_embed_key_depends_on_model_and_prefix_version(monkeypatch):
    a = extraction.embed_key("nomic-embed-text:latest")
    assert a == extraction.embed_key("nomic-embed-text:latest") and len(a) == 16
    assert a != extraction.embed_key("other-model")
    monkeypatch.setattr(extraction, "PREFIX_VERSION", extraction.PREFIX_VERSION + 1)
    assert a != extraction.embed_key("nomic-embed-text:latest")


def test_document_and_query_embeddings_get_their_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.0])
    extraction.embed_document("nomic-embed-text:latest", "Body")
    extraction.embed_query("nomic-embed-text:latest", "Question?")
    assert sent == ["search_document: Body", "search_query: Question?"]


def test_embed_question_uses_the_query_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(vector_query, "_embedding_model", lambda: "nomic-embed-text:latest")
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.0])
    vector_query.embed_question("Where?")
    assert sent == ["search_query: Where?"]
