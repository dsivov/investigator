"""Text/JSON chunking.

Thin facades over langchain_text_splitters; kept as a small module so the
pipeline orchestrator depends on a focused API rather than a 1k-line utils
file.
"""

from langchain_text_splitters import RecursiveJsonSplitter, TextSplitter


def text_chunker(text, chunk_size):
    splitter = TextSplitter(max_chunk_size=chunk_size)
    return splitter.split_text(text)


def json_chunker(json_object, chunk_size):
    splitter = RecursiveJsonSplitter(max_chunk_size=chunk_size)
    return splitter.split_json(json_object, convert_lists=True)
