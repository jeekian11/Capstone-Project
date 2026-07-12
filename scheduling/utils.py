import random
import string

CODE_CHARS = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '').replace('1', '')


def generate_reservation_code(length=6):
    """
    Generates a short, human-typeable reservation code (avoids ambiguous
    characters like O/0 and I/1) that's unique across both pending requests
    and the official schedule.
    """
    from scheduling.models import Session, SessionRequest

    while True:
        code = ''.join(random.choices(CODE_CHARS, k=length))
        if not SessionRequest.objects.filter(reservation_code=code).exists() and \
           not Session.objects.filter(reservation_code=code).exists():
            return code
