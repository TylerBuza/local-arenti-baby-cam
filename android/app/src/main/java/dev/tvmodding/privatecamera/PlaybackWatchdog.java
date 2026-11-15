package dev.tvmodding.privatecamera;

/** Monotonic frame deadlines; only sustained fresh playback resets recovery backoff. */
final class PlaybackWatchdog {
    static final long STARTUP_MS = 20000, STALL_MS = 12000, STABLE_MS = 30000;
    private boolean armed, sawFrame;
    private long started, stableSince = -1;
    private int recoveries;

    void begin(long now) { started = now; armed = true; sawFrame = false; stableSince = -1; }
    void stop() { armed = false; stableSince = -1; }
    void reset() { recoveries = 0; stop(); }
    boolean stalled(long now, long lastFrame, boolean playing, boolean wantsPlayback) {
        if (!armed || !wantsPlayback) { stableSince = -1; return false; }
        if (lastFrame >= started && lastFrame > 0) sawFrame = true;
        if (VideoDisplay.fresh(playing, now, lastFrame)) {
            if (stableSince < 0) stableSince = now;
            if (now - stableSince >= STABLE_MS) recoveries = 0;
        } else stableSince = -1;
        long progress = sawFrame ? lastFrame : started;
        return now >= progress && now - progress >= (sawFrame ? STALL_MS : STARTUP_MS);
    }
    void recover() {
        stop();
        // Saturate: a long outage must never overflow or turn into a user Stop.
        recoveries = Math.min(recoveries + 1, 5);
    }
    int attempts() { return recoveries; }
}
