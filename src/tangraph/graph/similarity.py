"""Similarity metrics for entity/edge sets and the shared text preprocessor.

`preprocess_text` lives here because it is only consumed by the similarity
functions below; nothing else in the codebase uses it directly.

NOTE (Phase 2 candidate): ``cosine_similarity_nodes`` and
``cosine_similarity_edges`` load a WordLlama model on every call — wasteful but
preserved as-is for behaviour parity.
"""

import json

from nltk.metrics import jaccard_distance

from tangraph.logging import get_logger
from tangraph.utils.text import find_and_cut_strings

tangos_log = get_logger()


def preprocess_text(text: str):
    """Strip whitespace artefacts then truncate long string fields."""
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    dict_chunk = json.loads(text)
    dict_chunk = find_and_cut_strings(dict_chunk, max_length=10000)
    return json.dumps(dict_chunk)


def jaccard_similarity_nodes(obj_set1, obj_set2):
    distances = []
    sorted_dict1 = sorted(obj_set1, key=lambda x: x["identifier"])
    sorted_dict2 = sorted(obj_set2, key=lambda x: x["identifier"])
    count = 0
    for s1 in sorted_dict1:
        for s2 in sorted_dict2:
            if s1["identifier"].upper() == s2["identifier"].upper():
                sorted_json1 = json.dumps(s1, sort_keys=True)
                sorted_json2 = json.dumps(s2, sort_keys=True)
                pt1 = preprocess_text(sorted_json1)
                pt2 = preprocess_text(sorted_json2)
                distances.append(1 - jaccard_distance(set(pt1), set(pt2)))
                count += 1
    return sum(distances) / count if count > 0 else 0


def cosine_similarity_nodes(obj_set1, obj_set2):
    from wordllama import WordLlama

    word_llama = WordLlama.load_m2v("potion_base_8m")
    distances = []
    sorted_dict1 = sorted(obj_set1, key=lambda x: x["identifier"])
    sorted_dict2 = sorted(obj_set2, key=lambda x: x["identifier"])
    count = 0
    for s1 in sorted_dict1:
        for s2 in sorted_dict2:
            if s1["identifier"].upper() == s2["identifier"].upper():
                sorted_json1 = json.dumps(s1, sort_keys=True)
                sorted_json2 = json.dumps(s2, sort_keys=True)
                pt1 = preprocess_text(sorted_json1)
                pt2 = preprocess_text(sorted_json2)
                distances.append(word_llama.similarity(pt1, pt2))
                count += 1
    return sum(distances) / count if count > 0 else 0


def jaccard_similarity_edges(obj_set1, obj_set2):
    distances = []
    sorted_dict1 = sorted(obj_set1, key=lambda x: x["src_identifier"])
    sorted_dict2 = sorted(obj_set2, key=lambda x: x["src_identifier"])
    count = 0
    for s1 in sorted_dict1:
        for s2 in sorted_dict2:
            if (
                s1["src_identifier"].upper() == s2["src_identifier"].upper()
                or s1["dst_identifier"].upper() == s2["dst_identifier"].upper()
            ):
                sorted_json1 = json.dumps(s1, sort_keys=True)
                sorted_json2 = json.dumps(s2, sort_keys=True)
                pt1 = preprocess_text(sorted_json1)
                pt2 = preprocess_text(sorted_json2)
                distances.append(1 - jaccard_distance(set(pt1), set(pt2)))
                count += 1
    return sum(distances) / count if count > 0 else 0


def cosine_similarity_edges(obj_set1, obj_set2):
    from wordllama import WordLlama

    word_llama = WordLlama.load_m2v("potion_base_8m")
    distances = []
    sorted_dict1 = sorted(obj_set1, key=lambda x: x["src_identifier"])
    sorted_dict2 = sorted(obj_set2, key=lambda x: x["src_identifier"])
    count = 0
    for s1 in sorted_dict1:
        for s2 in sorted_dict2:
            if (
                s1["src_identifier"].upper() == s2["src_identifier"].upper()
                or s1["dst_identifier"].upper() == s2["dst_identifier"].upper()
            ):
                sorted_json1 = json.dumps(s1, sort_keys=True)
                sorted_json2 = json.dumps(s2, sort_keys=True)
                pt1 = preprocess_text(sorted_json1)
                pt2 = preprocess_text(sorted_json2)
                distances.append(word_llama.similarity(pt1, pt2))
                count += 1
    return sum(distances) / count if count > 0 else 0
