package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class PlaybackWatchdogTest {
    @Test public void missingFirstFrameHasBoundedDeadline() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.begin(1000);
        assertFalse(guard.stalled(20999, 0, false, true)); assertTrue(guard.stalled(21000, 0, false, true));
    }
    @Test public void frozenReadyPlayerIsDetectedEvenWithoutBufferingEvent() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.begin(1000);
        assertFalse(guard.stalled(1500, 1500, true, true));
        assertFalse(guard.stalled(13499, 1500, true, true)); assertTrue(guard.stalled(13500, 1500, true, true));
    }
    @Test public void indefinitelyBufferingPlayerRecoversAfterLastFrameDeadline() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.begin(1000);
        guard.stalled(1500, 1500, true, true);
        assertTrue(guard.stalled(13500, 1500, false, true));
    }
    @Test public void explicitViewingStopNeverSchedulesRecovery() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.begin(1000);
        assertFalse(guard.stalled(100000, 1200, false, false));
        guard.stop(); assertFalse(guard.stalled(100000, 1200, false, true));
    }
    @Test public void repeatedFailuresKeepRecoveryArmedBeyondOldThreeAttemptLimit() {
        PlaybackWatchdog guard = new PlaybackWatchdog();
        for (int i = 0; i < 100000; i++) {
            guard.begin(1000); guard.stalled(1001, 1001, true, true);
            assertTrue(guard.stalled(21000, 1001, false, true));
            guard.recover();
            assertTrue(guard.attempts() > 0);
        }
        assertEquals(5, guard.attempts());
    }
    @Test public void continuousFreshFramesForThirtySecondsResetBudget() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.recover(); guard.begin(1000);
        for (long now = 1000; now <= 31000; now += 500) guard.stalled(now, now, true, true);
        assertEquals(0, guard.attempts());
    }
    @Test public void intermittentFramesDoNotCountAsStablePlayback() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.recover(); guard.begin(1000);
        guard.stalled(1000, 1000, true, true); guard.stalled(20000, 1000, false, true);
        guard.stalled(31000, 31000, true, true); assertEquals(1, guard.attempts());
    }
    @Test public void explicitStartResetsBackoffHistory() {
        PlaybackWatchdog guard = new PlaybackWatchdog(); guard.recover(); guard.recover(); guard.recover();
        guard.reset(); assertEquals(0, guard.attempts()); guard.recover(); assertEquals(1, guard.attempts());
    }
}
