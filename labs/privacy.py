"""
Identifies WHICH app/site a raw window-title capture belongs to, e.g.
"Pwede gamitin ang Plus - Google Chrome" -> "ChatGPT". The full raw title
and URL are kept as-is in PCActivityLog (nothing is redacted or replaced)
so the PC Activity Log shows everything a student did — this module is
only used to attach an accurate "which site" label alongside that raw
detail, for the "Most Used Applications" ranking and the small site tag
shown next to each activity row.

Two ways this is figured out, in order of preference:

1. By URL (reliable). When the agent could read the browser's address bar
   via UI Automation, PCActivityLog.page_url holds the actual address —
   the domain is matched against _KNOWN_DOMAINS directly. This is the only
   reliable way to identify single-page apps like ChatGPT or Claude, whose
   window title is just the current conversation/document name and never
   contains the site's own name at all.

2. By title text (best-effort fallback). When no URL was captured (older
   agent without the UI Automation dependency, or a non-browser window),
   falls back to guessing from keywords in the title text — this only
   works for sites whose page title happens to include their own name.
"""
from urllib.parse import urlparse

# Suffix Windows appends to a browser tab's title, mapped to a short
# display name. Checked longest-first isn't needed since these don't
# overlap as suffixes.
_BROWSER_SUFFIXES = {
    'google chrome': 'Chrome',
    'mozilla firefox': 'Firefox',
    'microsoft edge': 'Edge',
    'opera': 'Opera',
    'brave': 'Brave',
}

# domain (no "www.") -> display label. Checked by exact match first, then
# as a suffix (so "chat.openai.com" and "openai.com" both work), longest
# domain first so a more specific match wins over a shorter parent domain.
_KNOWN_DOMAINS = {
    'chatgpt.com': 'ChatGPT',
    'chat.openai.com': 'ChatGPT',
    'openai.com': 'ChatGPT',
    'claude.ai': 'Claude',
    'youtube.com': 'YouTube',
    'facebook.com': 'Facebook',
    'messenger.com': 'Messenger',
    'instagram.com': 'Instagram',
    'tiktok.com': 'TikTok',
    'twitter.com': 'Twitter/X',
    'x.com': 'Twitter/X',
    'mail.google.com': 'Gmail',
    'docs.google.com': 'Google Docs',
    'sheets.google.com': 'Google Sheets',
    'slides.google.com': 'Google Slides',
    'drive.google.com': 'Google Drive',
    'classroom.google.com': 'Google Classroom',
    'meet.google.com': 'Google Meet',
    'canva.com': 'Canva',
    'spotify.com': 'Spotify',
    'netflix.com': 'Netflix',
    'discord.com': 'Discord',
    'github.com': 'GitHub',
    'stackoverflow.com': 'Stack Overflow',
    'wikipedia.org': 'Wikipedia',
    'zoom.us': 'Zoom',
}
_KNOWN_DOMAINS_BY_LENGTH = sorted(_KNOWN_DOMAINS, key=len, reverse=True)

# (keyword to look for anywhere in the title, display label). Checked in
# order, first match wins — order roughly by how likely a lab student is
# to have it open. Only used as a fallback when no page_url is available.
#
# IMPORTANT: sites whose page title is fully user-controlled dynamic
# content (ChatGPT conversation names, Claude conversation names, etc.)
# are deliberately NOT in this list, even though they're in _KNOWN_DOMAINS
# above. Their own title never contains their own site name — so any
# "match" here would actually be the conversation's own topic coincidentally
# containing another site's name (e.g. a ChatGPT chat titled "Upload Video
# to GitHub" would wrongly match the 'github' keyword below and get
# mislabeled "GitHub" instead of "ChatGPT"). Without a captured URL, these
# sites correctly fall through to "Other site" — honest uncertainty beats
# a confident wrong guess.
_KNOWN_SITES = [
    ('youtube', 'YouTube'),
    ('facebook', 'Facebook'),
    ('messenger', 'Messenger'),
    ('instagram', 'Instagram'),
    ('tiktok', 'TikTok'),
    ('twitter', 'Twitter/X'),
    ('gmail', 'Gmail'),
    ('google docs', 'Google Docs'),
    ('google sheets', 'Google Sheets'),
    ('google slides', 'Google Slides'),
    ('google drive', 'Google Drive'),
    ('google classroom', 'Google Classroom'),
    ('google meet', 'Google Meet'),
    ('canva', 'Canva'),
    ('spotify', 'Spotify'),
    ('netflix', 'Netflix'),
    ('discord', 'Discord'),
    ('github', 'GitHub'),
    ('stack overflow', 'Stack Overflow'),
    ('wikipedia', 'Wikipedia'),
    ('zoom', 'Zoom'),
]


def _browser_from_title(lowered_title):
    for suffix, label in _BROWSER_SUFFIXES.items():
        if lowered_title.endswith(suffix):
            return label
    return None


def _domain_from_url(raw_url):
    """'https://chatgpt.com/c/abc123' -> 'chatgpt.com'. '' -> None."""
    if not raw_url:
        return None
    try:
        netloc = urlparse(raw_url if '//' in raw_url else f'//{raw_url}').netloc
    except ValueError:
        return None
    netloc = netloc.split('@')[-1].split(':')[0].lower().strip()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    return netloc or None


def _label_for_domain(domain):
    """Exact match first, then longest known domain that domain ends with
    (so 'chat.openai.com' matches even though only 'openai.com' is listed),
    finally the bare domain itself so an unrecognized site is still shown
    specifically instead of collapsing into a generic bucket."""
    if domain in _KNOWN_DOMAINS:
        return _KNOWN_DOMAINS[domain]
    for known in _KNOWN_DOMAINS_BY_LENGTH:
        if domain.endswith(f'.{known}'):
            return _KNOWN_DOMAINS[known]
    return domain


def resolve_site_label(raw_title, page_url=''):
    """Returns a short '<Browser> — <Site>' tag for a raw window title,
    e.g. 'Chrome — ChatGPT', WITHOUT altering the raw title/url anywhere —
    this is purely an additional label for display/grouping.

    'Pwede gamitin ang Plus - Google Chrome' + page_url='https://chatgpt.com/c/x'
        -> 'Chrome — ChatGPT'  (title text alone can't tell; the URL can)
    'CompuLab Project Report.docx - Word' -> None (not a browser window,
        nothing meaningful to tag it with)
    '' -> None
    """
    raw_title = (raw_title or '').strip()
    if not raw_title:
        return None

    lowered = raw_title.lower()
    browser = _browser_from_title(lowered)
    if browser is None:
        return None  # not a browser window — no site to identify

    domain = _domain_from_url(page_url)
    if domain:
        return f'{browser} — {_label_for_domain(domain)}'

    # No URL captured (older agent, or UI Automation couldn't read the
    # address bar this time) — fall back to guessing from the title, but
    # ONLY when the title has a genuine "<page> - <site> - <browser>"
    # structure (a dedicated site-name segment before the browser name).
    # A title with just one dash before the browser ("<content> - <browser>",
    # no middle segment) means the page itself never appended its own site
    # name — exactly how ChatGPT/Claude/Notion-style single-page apps
    # behave — so there's no reliable text to match against; guessing from
    # the content itself would just catch coincidental mentions (a ChatGPT
    # chat titled "Upload Video to GitHub" is NOT actually on github.com).
    # Honest "Other site" beats a confident wrong guess.
    for suffix, label in _BROWSER_SUFFIXES.items():
        if lowered.endswith(f' - {suffix}'):
            remainder = raw_title[:-(len(suffix) + 3)]
            if ' - ' not in remainder:
                return f'{browser} — Other site'
            site_segment = remainder.rsplit(' - ', 1)[-1].lower()
            break
    else:
        site_segment = lowered

    for keyword, label in _KNOWN_SITES:
        if keyword in site_segment:
            return f'{browser} — {label}'

    return f'{browser} — Other site'
