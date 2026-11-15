"""Monotonic preview recovery policy. No network, Qt, credentials or capture."""

STARTUP_TIMEOUT = 20.0
FRAME_TIMEOUT = 12.0
FRESH_FRAME_AGE = 1.25
STABLE_RESET = 30.0
RETRY_DELAYS = (2.0, 4.0, 8.0, 16.0, 30.0)


class PreviewRecovery:
    def __init__(self):
        self.enabled = False
        self.attempts = 0
        self.started_at = None
        self.retry_at = None
        self.stable_since = None
        self.last_sample = None

    def start(self, now):
        self.enabled = True
        self.attempts = 0
        self.begin_attempt(now)

    def begin_attempt(self, now):
        self.started_at = now
        self.retry_at = self.stable_since = self.last_sample = None

    def stop(self):
        self.enabled = False
        self.started_at = self.retry_at = self.stable_since = self.last_sample = None

    def stalled(self, now, last_frame):
        if not self.enabled or self.started_at is None or self.retry_at is not None:
            return False
        if now < self.started_at:
            return False
        fresh = last_frame > 0 and 0 <= now - last_frame < FRESH_FRAME_AGE
        continuous = self.last_sample is not None and 0 <= now - self.last_sample < FRESH_FRAME_AGE
        self.last_sample = now
        if fresh:
            if self.stable_since is None or not continuous:
                self.stable_since = now
            if now - self.stable_since >= STABLE_RESET:
                self.attempts = 0
        else:
            self.stable_since = None
        return now - (last_frame if last_frame > 0 else self.started_at) >= (
            FRAME_TIMEOUT if last_frame > 0 else STARTUP_TIMEOUT)

    def failed(self, now):
        self.stable_since = self.last_sample = None
        if not self.enabled or self.retry_at is not None:
            return None
        delay = RETRY_DELAYS[min(self.attempts, len(RETRY_DELAYS) - 1)]
        self.attempts += 1
        self.retry_at = now + delay
        return delay

    @property
    def pending(self):
        return self.enabled and self.retry_at is not None

    def due(self, now):
        return self.pending and now >= self.retry_at
