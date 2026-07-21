"""
Reduces a raw window-title capture down to just "which app/site" instead of
the exact page title / document name / chat content that Windows puts in
the title bar. E.g. a browser tab titled "how do I center a div - ChatGPT"
becomes "Chrome — ChatGPT" — enough to see WHAT was used, without keeping
the specific page title text on file.

Applied server-side in pc_agent_activity_api, right before a PCActivityLog
row is created, so it's one place to maintain regardless of how many lab
PCs are running the agent.
"""

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

# (keyword to look for anywhere in the title, display label). Checked in
# order, first match wins — order roughly by how likely a lab student is
# to have it open.
_KNOWN_SITES = [
    ('chatgpt', 'ChatGPT'),
    ('claude.ai', 'Claude'),
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


def simplify_window_title(raw_title):
    """'how do I center a div - ChatGPT - Google Chrome' -> 'Chrome — ChatGPT'
    'CompuLab Project Report.docx - Word' -> left as-is (not a browser,
    and a filename alone isn't the kind of content this is meant to hide).
    '' -> ''
    """
    raw_title = (raw_title or '').strip()
    if not raw_title:
        return ''

    lowered = raw_title.lower()

    browser = None
    for suffix, label in _BROWSER_SUFFIXES.items():
        if lowered.endswith(suffix):
            browser = label
            break

    if browser is None:
        # Not a recognized browser window — a regular desktop app's title
        # isn't the "what were they looking at inside it" content this is
        # meant to strip, so leave it alone.
        return raw_title

    for keyword, label in _KNOWN_SITES:
        if keyword in lowered:
            return f'{browser} — {label}'

    return f'{browser} — Other site'
