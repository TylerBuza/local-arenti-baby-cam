package dev.tvmodding.privatecamera;

/** Two existing camera profiles; never rewrites encoder settings or guesses link Mbps. */
final class AdaptiveQualityPolicy {
    static final long REBUFFER_MS = 2500, STARTUP_MS = 12000, WINDOW_MS = 30000;
    static final long STABLE_MS = 120000, MAX_PROBE_DELAY_MS = 900000;
    private String mode = "auto", quality = "high";
    private long started, bufferingSince = -1, thinSince = -1, stableSince = -1;
    private long previousRebuffer = -1, previousFailure = -1, probeAt = -1;
    private long nextProbe, probeDelay = STABLE_MS;
    private boolean sawFrame, countedRebuffer;

    static String checkedMode(String value) {
        return "high".equals(value) || "low".equals(value) ? value : "auto";
    }
    void select(String value, long now) {
        mode = checkedMode(value); quality = "low".equals(mode) ? "low" : "high";
        probeDelay = STABLE_MS; probeAt = previousRebuffer = previousFailure = -1;
        nextProbe = now + probeDelay; begin(now);
    }
    void restore(String mode, String quality, long delay, long now) {
        select(mode, now);
        if (automatic() && "low".equals(quality)) {
            this.quality = "low";
            probeDelay = Math.max(STABLE_MS, Math.min(MAX_PROBE_DELAY_MS, delay));
            nextProbe = now + probeDelay;
        }
    }
    void begin(long now) {
        started = now; sawFrame = countedRebuffer = false;
        bufferingSince = thinSince = stableSince = -1;
    }
    String quality() { return quality; }
    String mode() { return mode; }
    long probeDelay() { return probeDelay; }
    boolean automatic() { return "auto".equals(mode); }
    String label() { return automatic() ? "Auto · " + ("low".equals(quality) ? "Low" : "High") : "low".equals(quality) ? "Low" : "High"; }

    // Return true only on a real selection change. The caller opens one fresh,
    // non-seekable MP4 session; it never changes quality in the middle of a file.
    boolean sample(long now, boolean fresh, boolean buffering, long bufferedMs) {
        if (!automatic()) return false;
        if (fresh) sawFrame = true;
        if ("high".equals(quality)) {
            if (buffering) {
                if (bufferingSince < 0) bufferingSince = now;
                if (sawFrame && now - bufferingSince >= 750 && !countedRebuffer) {
                    countedRebuffer = true;
                    boolean repeated = previousRebuffer >= 0 && now - previousRebuffer <= WINDOW_MS;
                    previousRebuffer = now;
                    if (repeated) return lower(now);
                }
                if ((sawFrame && now - bufferingSince >= REBUFFER_MS)
                        || (!sawFrame && now - started >= STARTUP_MS)) return lower(now);
            } else { bufferingSince = -1; countedRebuffer = false; }
            if (fresh && bufferedMs >= 0 && bufferedMs < 300) {
                if (thinSince < 0) thinSince = now;
                if (now - thinSince >= 4000) return lower(now);
            } else thinSince = -1;
            if (fresh) {
                if (stableSince < 0) stableSince = now;
                if (now - stableSince >= STABLE_MS) {
                    probeDelay = STABLE_MS; probeAt = previousFailure = previousRebuffer = -1;
                }
            } else stableSince = -1;
        } else {
            // A quiet Low stream is not a bandwidth measurement. Probe High only
            // after two minutes of uninterrupted fresh video with a useful reserve.
            if (fresh && !buffering && bufferedMs >= 750) {
                if (stableSince < 0) stableSince = now;
                if (now >= nextProbe && now - stableSince >= STABLE_MS) {
                    quality = "high"; probeAt = now; previousFailure = previousRebuffer = -1;
                    begin(now); return true;
                }
            } else stableSince = -1;
        }
        return false;
    }
    boolean failure(long now) {
        stableSince = -1;
        if (!automatic() || !"high".equals(quality)) return false;
        boolean repeated = previousFailure >= 0 && now - previousFailure <= WINDOW_MS;
        previousFailure = now;
        if (repeated || (probeAt >= 0 && now - probeAt <= WINDOW_MS)) return lower(now);
        return false;
    }
    private boolean lower(long now) {
        if (probeAt >= 0) probeDelay = Math.min(MAX_PROBE_DELAY_MS, probeDelay * 2);
        quality = "low"; nextProbe = now + probeDelay; probeAt = -1;
        previousFailure = previousRebuffer = -1; begin(now); return true;
    }
}
