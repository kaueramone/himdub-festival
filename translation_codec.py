"""Shared translation contract for the website and its offline build task."""
import hashlib
import json
import re
from translation_terms import FIXED_TERMS, TERM_PATTERN, protected_terms

TARGETS = ('en', 'fr', 'es')
PLACEHOLDER = re.compile(r'%\([\w]+\)[#0 +\-\d.]*[sdif]|%%')
HTML_TAG = re.compile(r'</?[A-Za-z][^>]*>')
LITERAL = re.compile(r'https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\d+(?:[.,:/-]\d+)*(?:\s?[%€])?')
PROTECTED = re.compile('|'.join([HTML_TAG.pattern, PLACEHOLDER.pattern, TERM_PATTERN.pattern, LITERAL.pattern]))


def glossary_version():
    return hashlib.sha256(('\n'.join(sorted(FIXED_TERMS)) + PROTECTED.pattern).encode()).hexdigest()[:16]


def cache_key(source, language):
    return hashlib.sha256(json.dumps([glossary_version(), language, source],
                                    ensure_ascii=False).encode()).hexdigest()


def segments(source):
    """Protected names/markup never enter the translation engine at all."""
    result, position = [], 0
    for match in PROTECTED.finditer(source):
        if match.start() > position:
            result.append((False, source[position:match.start()]))
        result.append((True, match.group()))
        position = match.end()
    if position < len(source):
        result.append((False, source[position:]))
    return result


def validate_translation(source, text):
    if (not isinstance(text, str) or not text.strip()
            or len(text) > max(1000, len(source) * 8)
            or HTML_TAG.findall(text) != HTML_TAG.findall(source)
            or sorted(PLACEHOLDER.findall(text)) != sorted(PLACEHOLDER.findall(source))
            or sorted(LITERAL.findall(text)) != sorted(LITERAL.findall(source))
            or any(text.count(term) < protected_terms(source).count(term)
                   for term in set(protected_terms(source)))):
        raise ValueError('Translation changed protected content')
    return text
