"""Generic dict/string helpers with no domain logic.

All functions are pure (or near-pure) — they don't read globals or call
external services. Safe to use anywhere in the codebase.
"""

import re
import urllib.parse


def flatten_and_clean_dict(d, parent_key="", sep="."):
    """Flatten a nested dict and drop empty fields (None, '', [], {})."""
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_and_clean_dict(v, new_key, sep=sep).items())
        elif isinstance(v, (list, tuple, set)) and not v:
            continue
        elif v is None or (isinstance(v, str) and not v):
            continue
        else:
            items.append((new_key, v))
    return dict(items)


def find_value_in_nested_dict(data, key_to_find):
    """Depth-first search for ``key_to_find`` in a nested dict/list.

    Returns the first matching value, or ``None`` if not found.
    """
    if isinstance(data, dict):
        if key_to_find in data:
            return data[key_to_find]
        for v in data.values():
            found = find_value_in_nested_dict(v, key_to_find)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_value_in_nested_dict(item, key_to_find)
            if found is not None:
                return found
    return None


def remove_empty_fields(data):
    if isinstance(data, dict):
        return {
            k: remove_empty_fields(v)
            for k, v in data.items()
            if v is not None and v != "" and v != [] and v != {}
        }
    elif isinstance(data, list):
        return [
            remove_empty_fields(elem)
            for elem in data
            if elem is not None and elem != "" and elem != [] and elem != {}
        ]
    return data


def remove_html_tags_regex(text):
    """Remove HTML tags from a string using a non-greedy regex."""
    clean = re.compile("<.*?>")
    return re.sub(clean, "", text)


def sanitize_json_string(json_string):
    """Strip control characters and non-breaking spaces."""
    sanitized = json_string.translate(str.maketrans("", "", "".join(map(chr, range(32)))))
    sanitized = sanitized.replace("\xa0", " ")
    return sanitized


def find_and_cut_strings(data, search_term=None, max_length=20000):
    """Walk a nested dict; URL-decode + sanitize + de-HTML strings;
    drop string fields that exceed ``max_length`` or are known noisy keys
    (``text``, ``response_text``, long ``query``).
    """
    for key in list(data.keys()):
        value = data[key]
        if isinstance(value, str):
            value = urllib.parse.unquote(value)
            value = sanitize_json_string(value)
            value = remove_html_tags_regex(value)
            if key == "text":
                del data[key]
            elif key == "response_text":
                del data[key]
            elif key == "query" and len(value) > 100:
                del data[key]
            else:
                if len(value) > max_length:
                    del data[key]
        elif isinstance(value, dict):
            find_and_cut_strings(value, max_length=max_length)
            if not value:
                del data[key]
    return data
