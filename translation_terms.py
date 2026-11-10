"""Festival names and navigation labels that remain English in every locale.

Keep this glossary independent of providers and translation catalogues. Longer
phrases win; word boundaries avoid changing ordinary words or plural fragments.
"""
import re

FIXED_TERMS = frozenset({
    'HIM Dub Festival', 'HIM Dub', 'The Experience', 'Experience',
    'Tickets', 'Participate', 'Dub Temple', 'River Station', 'Roots Garden',
    'Nomad Embassy Sound System', 'Mindel Reggae Sound System',
    'Music is the most high', 'HIM DUB MEMORIES',
    'TicketSwap', 'Teya', 'Stripe', 'Resend',
    'Bunny', 'Lifes Work Glamping',
})

TERM_PATTERN = re.compile(
    r'(?<!\w)(?:' + '|'.join(re.escape(term) for term in
                             sorted(FIXED_TERMS, key=lambda term: (-len(term), term)))
    + r')(?!\w)'
)


def is_fixed(text):
    return text in FIXED_TERMS


def protected_terms(text):
    return [match.group() for match in TERM_PATTERN.finditer(text)]
