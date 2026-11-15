package dev.tvmodding.privatecamera;

import java.time.Instant;
import java.time.ZoneId;
import org.junit.Test;
import static org.junit.Assert.*;

public class VideoDisplayTest {
    @Test public void clockUsesExplicitDisplayTimeAndZone() {
        long now = Instant.parse("2026-11-06T18:04:05Z").toEpochMilli();
        assertEquals("2026-11-06 13:04:05", VideoDisplay.clock(now, ZoneId.of("America/New_York"), false));
    }
    @Test public void pipKeepsBothDateAndSecondsInTwoCompactLines() {
        long now = Instant.parse("2026-11-06T18:04:05Z").toEpochMilli();
        assertEquals("2026-11-06\n18:04:05", VideoDisplay.clock(now, ZoneId.of("UTC"), true));
    }
    @Test public void clockRollsDateAtLocalMidnight() {
        long now = Instant.parse("2026-11-07T05:00:00Z").toEpochMilli();
        assertEquals("2026-11-07 00:00:00", VideoDisplay.clock(now, ZoneId.of("America/New_York"), false));
    }
    @Test public void currentDecodedFrameCanShowClockAndEnterPip() {
        assertTrue(VideoDisplay.fresh(true, 1500, 1490));
        assertTrue(VideoDisplay.fresh(true, 2500, 1300));
    }
    @Test public void staleOrAbsentFramesCannotAppearCurrent() {
        assertFalse(VideoDisplay.fresh(true, 2501, 1300)); assertFalse(VideoDisplay.fresh(true, 1500, 0));
    }
    @Test public void stoppedOrBufferingPlaybackCannotShowLiveClock() {
        assertFalse(VideoDisplay.fresh(false, 1500, 1490));
    }
    @Test public void futureFrameTimestampCannotPassFreshness() {
        assertFalse(VideoDisplay.fresh(true, 1500, 1600));
    }
}
