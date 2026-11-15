"""Health overlays are not configured or shipped in the public camera release."""


def load_address():
    raise ValueError('Health integration is not included in this public release')
