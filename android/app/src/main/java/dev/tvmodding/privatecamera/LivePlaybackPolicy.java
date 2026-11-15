package dev.tvmodding.privatecamera;

/** Relative Windows-read-to-phone timing, never claimed to be camera capture age. */
final class LivePlaybackPolicy {
    enum Action { NORMAL, CATCH_UP, RECONNECT }
    static final float CATCH_UP_SPEED = 1.05f;
    private long started, origin, behindSince = -1, excessSince = -1, lag = -1;
    private boolean clockKnown, catchingUp;

    void begin(long now) {
        started = now; clockKnown = false; catchingUp = false; behindSince = excessSince = lag = -1;
    }
    void receiveClock(String age, long receivedAt) {
        long parsed = parseAge(age);
        if (parsed >= 0 && receivedAt >= 0) { origin = receivedAt - parsed; clockKnown = true; }
    }
    static long parseAge(String value) {
        if (value == null || !value.matches("[0-9]{1,5}")) return -1;
        long age = Long.parseLong(value);
        return age <= 60000 ? age : -1;
    }
    Action sample(long now, long position, long buffered, boolean freshAndPlaying) {
        if (!freshAndPlaying || position < 0 || buffered < 0 || now < started) {
            catchingUp = false; behindSince = excessSince = lag = -1; return Action.NORMAL;
        }
        lag = clockKnown ? now - origin - position : -1;
        if (clockKnown && lag < -2000) { clockKnown = false; lag = -1; }
        // Recovery needs fresh progressing playback and sustained age, not excess
        // buffer: a delayed source can still deliver near the local buffer floor.
        if (clockKnown && lag > 5000 && now - started >= 8000) {
            if (behindSince < 0) behindSince = now;
            if (now - behindSince >= 4000) return Action.RECONNECT;
        } else behindSince = -1;
        // Clock lag includes mux/transit/timestamp offset that speed cannot remove.
        // Only spend locally available excess video, retaining the jitter reserve.
        // A sustained entry threshold also ignores ordinary fragment arrival bursts.
        if (catchingUp) {
            if (buffered <= 1200) catchingUp = false;
            excessSince = -1;
        } else if (buffered > 1800) {
            if (excessSince < 0) excessSince = now;
            if (now - excessSince >= 1500) { catchingUp = true; excessSince = -1; }
        } else excessSince = -1;
        return catchingUp ? Action.CATCH_UP : Action.NORMAL;
    }
    boolean tooFarBehind() { return clockKnown && lag > 3000; }
    boolean isCatchingUp() { return catchingUp; }
}
