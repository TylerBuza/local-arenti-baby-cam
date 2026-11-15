package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class HoldGuardTest {
    @Test public void cannotStartWithoutFreshLiveState() { assertFalse(new HoldGuard().press(1000, false)); }
    @Test public void boundedHoldMustBeReleasedBeforeAnotherPress() {
        HoldGuard guard = new HoldGuard();
        assertTrue(guard.press(1000, true));
        assertTrue(guard.canRenew(1500, 1499, true));
        assertFalse(guard.canRenew(4000, 3999, true));
        assertFalse(guard.press(4001, true));
        assertTrue(guard.release());
        assertTrue(guard.press(4002, true));
    }
    @Test public void staleOrPausedOrRewoundTimeCannotRenew() {
        HoldGuard guard = new HoldGuard(); guard.press(1000, true);
        assertFalse(guard.canRenew(2500, 1200, true));
        assertFalse(guard.canRenew(1500, 1499, false));
        assertFalse(guard.canRenew(999, 998, true));
        assertFalse(guard.canRenew(1500, 1600, true));
    }
    @Test public void releaseAndFocusCancellationEndRenewals() {
        HoldGuard guard = new HoldGuard(); guard.press(1000, true);
        assertTrue(guard.release()); assertFalse(guard.release());
        assertFalse(guard.canRenew(1100, 1099, true));
    }
    @Test public void noFrameIsNotFreshVideo() {
        HoldGuard guard = new HoldGuard(); guard.press(1000, true);
        assertFalse(guard.canRenew(1100, 0, true));
    }
}
