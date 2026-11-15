package dev.tvmodding.privatecamera;

/** Pure monotonic-time guard; no network or device commands. */
final class HoldGuard {
    private long started = -1;
    boolean press(long now, boolean live) {
        if (!live || started >= 0) return false;
        started = now;
        return true;
    }
    boolean canRenew(long now, long lastFrame, boolean playing) {
        return started >= 0 && now >= started && now - started < 3000
            && playing && lastFrame > 0 && now >= lastFrame && now - lastFrame <= 1200;
    }
    boolean release() { boolean held = started >= 0; started = -1; return held; }
}
