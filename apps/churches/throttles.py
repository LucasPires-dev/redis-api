from rest_framework.throttling import UserRateThrottle


class CampusInviteThrottle(UserRateThrottle):
    """Caps invite-code attempts (per account, or per IP when anonymous) so codes
    can't be guessed by brute force."""

    scope = "campus_invite"
