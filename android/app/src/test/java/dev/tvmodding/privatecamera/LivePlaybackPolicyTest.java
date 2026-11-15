package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class LivePlaybackPolicyTest {
    private LivePlaybackPolicy clocked() {
        LivePlaybackPolicy policy = new LivePlaybackPolicy(); policy.begin(1000); policy.receiveClock("0", 1000); return policy;
    }
    @Test public void optionalHeaderIsStrictAndBounded() {
        assertEquals(0, LivePlaybackPolicy.parseAge("0")); assertEquals(60000, LivePlaybackPolicy.parseAge("60000"));
        for (String value : new String[]{null, "", "60001", "+1", "-1", "1.5", "1\n", "99999999999999999999"})
            assertEquals(-1, LivePlaybackPolicy.parseAge(value));
    }
    @Test public void ordinaryPlaybackDoesNotChangeSpeed() {
        assertEquals(LivePlaybackPolicy.Action.NORMAL, clocked().sample(3000, 1000, 1000, true));
    }
    @Test public void sustainedExcessVideoEnablesGentleCatchup() {
        LivePlaybackPolicy policy = clocked();
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(4000, 1000, 2000, true));
        assertEquals(LivePlaybackPolicy.Action.CATCH_UP, policy.sample(5500, 2500, 2000, true));
        assertTrue(policy.isCatchingUp()); assertEquals(1.05f, LivePlaybackPolicy.CATCH_UP_SPEED, 0);
    }
    @Test public void hysteresisPreventsSpeedFlapping() {
        LivePlaybackPolicy policy = clocked();
        policy.sample(4000, 1000, 2000, true);
        assertEquals(LivePlaybackPolicy.Action.CATCH_UP, policy.sample(5500, 2500, 2000, true));
        assertEquals(LivePlaybackPolicy.Action.CATCH_UP, policy.sample(6000, 3025, 1500, true));
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(6500, 3550, 1200, true));
    }
    @Test public void lowBufferNeverAcceleratesIntoAnotherStall() {
        assertEquals(LivePlaybackPolicy.Action.NORMAL, clocked().sample(20000, 1000, 400, true));
    }
    @Test public void bufferingAndPauseNeverAccelerateOrReconnect() {
        LivePlaybackPolicy policy = clocked();
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(100000, 1000, 2000, false));
        assertFalse(policy.tooFarBehind()); assertFalse(policy.isCatchingUp());
    }
    @Test public void persistentLargeBacklogNeedsGraceAndHoldBeforeReconnect() {
        LivePlaybackPolicy policy = clocked();
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(10000, 1000, 1200, true));
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(13999, 1500, 1200, true));
        assertEquals(LivePlaybackPolicy.Action.RECONNECT, policy.sample(14000, 1600, 1200, true));
    }
    @Test public void initialStartupCannotImmediatelyLoopReconnects() {
        LivePlaybackPolicy policy = clocked(); policy.receiveClock("10000", 1000);
        for (long now = 1000; now < 9000; now += 500)
            assertNotEquals(LivePlaybackPolicy.Action.RECONNECT, policy.sample(now, 0, 1200, true));
    }
    @Test public void aBriefLagSpikeDoesNotAccumulateAcrossRecovery() {
        LivePlaybackPolicy policy = clocked();
        policy.sample(10000, 1000, 1200, true);
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(12000, 10000, 1200, true));
        assertNotEquals(LivePlaybackPolicy.Action.RECONNECT, policy.sample(14000, 7000, 1200, true));
    }
    @Test public void legacyConnectorFallsBackToBufferDepthWithoutGuessingCaptureAge() {
        LivePlaybackPolicy policy = new LivePlaybackPolicy(); policy.begin(1000);
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(10000, 1000, 1900, true));
        assertEquals(LivePlaybackPolicy.Action.CATCH_UP, policy.sample(11500, 2500, 1900, true));
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(12000, 3000, 1000, true));
        assertFalse(policy.tooFarBehind());
    }
    @Test public void knownDelayCanGuardMovementAndCurrentClockDisplay() {
        LivePlaybackPolicy policy = clocked(); policy.sample(6000, 1000, 1200, true);
        assertTrue(policy.tooFarBehind());
        policy.sample(6500, 5000, 1200, true); assertFalse(policy.tooFarBehind());
    }
    @Test public void newSessionClearsOldClockAndRecoveryState() {
        LivePlaybackPolicy policy = clocked(); policy.sample(10000, 1000, 1200, true); policy.begin(20000);
        assertFalse(policy.tooFarBehind()); assertFalse(policy.isCatchingUp());
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(21000, 0, 1000, true));
    }
    @Test public void impossibleFutureTimelineDisablesBadClockInsteadOfRestartLoop() {
        LivePlaybackPolicy policy = clocked();
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(2000, 10000, 1000, true));
        assertFalse(policy.tooFarBehind());
    }
    @Test public void relativeClockDoesNotDependOnMatchingPcPhoneWallClocksOrBootTime() {
        LivePlaybackPolicy policy = new LivePlaybackPolicy(); policy.begin(1000); policy.receiveClock("2000", 1000);
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(2000, 1000, 1200, true));
        policy.sample(4000, 1000, 1200, true); assertTrue(policy.tooFarBehind());
    }

    @Test public void irreducibleDeliveryDelayDoesNotSpendJitterReserveForMinutes() {
        LivePlaybackPolicy policy = clocked();
        // 1.7 s before data reaches the player + 1 s locally buffered. The old
        // clock-driven loop tried to reach 1 s total, below the delivery floor.
        for (long now = 5000; now <= 185000; now += 500) {
            long position = now - 1000 - 1700 - 1000;
            assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(now, position, 1000, true));
            assertFalse(policy.isCatchingUp()); assertFalse(policy.tooFarBehind());
        }
    }

    @Test public void pacedBacklogActuallyDrainsAndSettlesDespiteFixedDeliveryFloor() {
        for (boolean header : new boolean[]{true, false}) {
            LivePlaybackPolicy policy = header ? clocked() : new LivePlaybackPolicy();
            if (!header) policy.begin(1000);
            long buffered = 2000, position = 1300;
            boolean accelerated = false;
            for (long now = 6000; now <= 126000; now += 500) {
                LivePlaybackPolicy.Action action = policy.sample(now, position, buffered, true);
                assertNotEquals(LivePlaybackPolicy.Action.RECONNECT, action);
                long consumed = action == LivePlaybackPolicy.Action.CATCH_UP ? 525 : 500;
                accelerated |= consumed > 500;
                position += consumed; buffered += 500 - consumed;
                assertTrue("Catch-up consumed the jitter reserve", buffered >= 1200);
                if (now >= 30000) assertEquals(LivePlaybackPolicy.Action.NORMAL, action);
            }
            assertTrue(accelerated); assertEquals(1200, buffered); assertFalse(policy.tooFarBehind());
        }
    }

    @Test public void isolatedFragmentBurstsDoNotFlapSpeedOrAccumulateEntryTime() {
        LivePlaybackPolicy policy = clocked();
        for (long now = 5000; now < 65000; now += 500) {
            long buffered = now % 1500 == 0 ? 2000 : 1000;
            assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(now, now - 3500, buffered, true));
        }
    }

    @Test public void bufferingAndSessionChangeDiscardPendingCatchupEntry() {
        LivePlaybackPolicy policy = clocked();
        policy.sample(4000, 1000, 2000, true);
        policy.sample(5000, 2000, 2000, false);
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(5500, 2500, 2000, true));
        policy.begin(6000);
        assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(6500, 500, 2000, true));
    }

    @Test public void unrecoverableClockLagStillGuardsAndReconnectsWithoutExcessBuffer() {
        LivePlaybackPolicy policy = clocked();
        for (long now = 10000; now < 14000; now += 500) {
            assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(now, now - 8000, 1000, true));
            assertTrue(policy.tooFarBehind());
        }
        assertEquals(LivePlaybackPolicy.Action.RECONNECT, policy.sample(14000, 6000, 1000, true));
    }

    @Test public void ordinarySmallBufferFluctuationsCannotResetLargeLagRecoveryHold() {
        LivePlaybackPolicy policy = clocked();
        for (long now = 10000; now < 14000; now += 500) {
            long buffer = now % 1000 == 0 ? 750 : 400;
            assertEquals(LivePlaybackPolicy.Action.NORMAL, policy.sample(now, now - 8000, buffer, true));
            assertTrue(policy.tooFarBehind());
        }
        assertEquals(LivePlaybackPolicy.Action.RECONNECT, policy.sample(14000, 6000, 400, true));
    }
}
