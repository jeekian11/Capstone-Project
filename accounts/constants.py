"""
Central registry of Departments and the year levels each one offers.

Programs aren't all the same length (a 2-year certificate program only has
Year 1/2, a 5-year Engineering program goes up to Year 5), so year level
choices are defined *per department* here instead of as one fixed list —
this is what lets the User form / Import Students flow only offer the year
levels that are actually valid for the department picked.

To add/rename a department or change its year levels, edit DEPARTMENTS
below — everything else (choices, forms, JS for the import/user forms)
reads from this single source of truth.
"""

DEPARTMENTS = {
    'CCS': {
        'name': 'College of Computer Studies',
        'year_levels': [1, 2, 3, 4],
    },
    'COE': {
        'name': 'College of Engineering',
        'year_levels': [1, 2, 3, 4, 5],
    },
    'CBA': {
        'name': 'College of Business Administration',
        'year_levels': [1, 2, 3, 4],
    },
    'CAS': {
        'name': 'College of Arts and Sciences',
        'year_levels': [1, 2, 3, 4],
    },
    'COED': {
        'name': 'College of Education',
        'year_levels': [1, 2, 3, 4],
    },
    'CCJE': {
        'name': 'College of Criminal Justice Education',
        'year_levels': [1, 2, 3, 4],
    },
    'CHTM': {
        'name': 'College of Hospitality and Tourism Management',
        'year_levels': [1, 2, 3, 4],
    },
    'CTE': {
        'name': 'College of Technical Education',
        'year_levels': [1, 2],
    },
}

# e.g. [('CCS', 'College of Computer Studies'), ...] — for model/form choices
DEPARTMENT_CHOICES = [(code, info['name']) for code, info in DEPARTMENTS.items()]

YEAR_LEVEL_LABELS = {
    1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year',
    5: '5th Year', 6: '6th Year',
}


def year_level_choices_for(department_code):
    """All valid (value, label) year-level choices for a given department
    code. Returns every known year level if the department isn't recognized,
    so the field still degrades gracefully rather than becoming unusable."""
    levels = DEPARTMENTS.get(department_code, {}).get('year_levels') or sorted(YEAR_LEVEL_LABELS)
    return [(lvl, YEAR_LEVEL_LABELS.get(lvl, f'Year {lvl}')) for lvl in levels]


def department_year_levels_json():
    """{'CCS': [{'value': 1, 'label': '1st Year'}, ...], ...} — handed to
    templates so the Department -> Year Level dropdowns can be wired up
    client-side without a round trip."""
    return {
        code: [{'value': lvl, 'label': YEAR_LEVEL_LABELS.get(lvl, f'Year {lvl}')} for lvl in info['year_levels']]
        for code, info in DEPARTMENTS.items()
    }


def department_name(code):
    info = DEPARTMENTS.get(code)
    return info['name'] if info else (code or '')
