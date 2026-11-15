package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class AdaptiveQualityPolicyTest {
    private AdaptiveQualityPolicy live() {
        AdaptiveQualityPolicy policy = new AdaptiveQualityPolicy(); policy.select("auto", 0);
        policy.sample(1000, true, false, 1500); return policy;
    }
    private void low(AdaptiveQualityPolicy policy) {
        policy.sample(2000, false, true, 0);
        assertTrue(policy.sample(4500, false, true, 0));
        assertEquals("low", policy.quality());
    }
    @Test public void briefJitterAndOrdinaryReserveDoNotSwitch() {
        AdaptiveQualityPolicy policy = live();
        for (long now = 2000; now < 90000; now += 500) {
            boolean buffer = now % 5000 == 0;
            assertFalse(policy.sample(now, !buffer, buffer, buffer ? 0 : 800));
        }
        assertEquals("high", policy.quality());
    }
    @Test public void sustainedRebufferDropsToLowOnce() {
        AdaptiveQualityPolicy policy = live(); low(policy);
        for (long now = 5000; now < 90000; now += 500) assertFalse(policy.sample(now, false, true, 0));
        assertEquals("low", policy.quality());
    }
    @Test public void repeatedModerateRebuffersDropBeforeLongStall() {
        AdaptiveQualityPolicy policy = live();
        assertFalse(policy.sample(2000, false, true, 0));
        assertFalse(policy.sample(3000, false, true, 0));
        assertFalse(policy.sample(3500, true, false, 1000));
        assertFalse(policy.sample(9000, false, true, 0));
        assertTrue(policy.sample(10000, false, true, 0));
    }
    @Test public void startupHasGraceThenTriesLowerProfile() {
        AdaptiveQualityPolicy policy = new AdaptiveQualityPolicy(); policy.select("auto", 0);
        for (long now = 0; now < 12000; now += 500) assertFalse(policy.sample(now, false, true, 0));
        assertTrue(policy.sample(12000, false, true, 0));
    }
    @Test public void sustainedThinBufferLowersBeforeEmptyButSingleDipDoesNot() {
        AdaptiveQualityPolicy policy = live();
        policy.sample(2000, true, false, 200); policy.sample(3000, true, false, 1000);
        assertFalse(policy.sample(4000, true, false, 200));
        assertFalse(policy.sample(7999, true, false, 200));
        assertTrue(policy.sample(8000, true, false, 200));
    }
    @Test public void manualModesNeverAdaptEvenAcrossRepeatedErrors() {
        for (String mode : new String[]{"high", "low"}) {
            AdaptiveQualityPolicy policy = new AdaptiveQualityPolicy(); policy.select(mode, 0);
            for (long now = 0; now < 1000000; now += 500) {
                assertFalse(policy.sample(now, false, true, 0)); assertFalse(policy.failure(now));
            }
            assertEquals(mode, policy.quality());
        }
    }
    @Test public void failuresAccumulateAcrossNewSessionsButNotAcrossLongHealthyIntervals() {
        AdaptiveQualityPolicy policy = live();
        assertFalse(policy.failure(10000)); policy.begin(12000);
        assertTrue(policy.failure(15000));
        policy.select("auto", 20000); assertFalse(policy.failure(21000));
        assertFalse(policy.failure(60000)); assertEquals("high", policy.quality());
    }
    @Test public void upgradeRequiresContinuousStableLowAndReserve() {
        AdaptiveQualityPolicy policy = live(); low(policy);
        policy.sample(5000, true, false, 1200);
        assertFalse(policy.sample(124999, true, false, 1200));
        policy.sample(125000, true, false, 200); // Thin reserve restarts stability.
        assertFalse(policy.sample(126000, true, false, 1200));
        assertFalse(policy.sample(245999, true, false, 1200));
        assertTrue(policy.sample(246000, true, false, 1200));
        assertEquals("high", policy.quality());
    }
    @Test public void failedHighProbesBackOffToFifteenMinutesRatherThanFlapping() {
        AdaptiveQualityPolicy policy = live(); low(policy);
        long now = 5000;
        for (int attempt = 0; attempt < 8; attempt++) {
            policy.sample(now, true, false, 1500);
            now += policy.probeDelay();
            assertTrue(policy.sample(now, true, false, 1500));
            policy.begin(now);
            assertTrue(policy.failure(now + 1000));
            assertEquals("low", policy.quality());
            assertEquals(Math.min(900000L, 120000L << (attempt + 1)), policy.probeDelay());
            assertFalse(policy.sample(now + 2000, true, false, 1500));
            assertFalse(policy.sample(now + 121000, true, false, 1500));
            now += 122000;
        }
    }
    @Test public void restoreKeepsLowAndCooldownButDoesNotCountBackgroundTimeAsStable() {
        AdaptiveQualityPolicy policy = new AdaptiveQualityPolicy();
        policy.restore("auto", "low", 480000, 1000);
        assertEquals("low", policy.quality()); assertEquals(480000, policy.probeDelay());
        policy.sample(2000, true, false, 1500); policy.begin(500000);
        assertFalse(policy.sample(500000, true, false, 1500));
        assertTrue(policy.sample(620000, true, false, 1500));
    }
    @Test public void selectingAutoOrManualResetsOldFailureState() {
        AdaptiveQualityPolicy policy = live(); low(policy);
        policy.select("high", 5000); assertEquals("high", policy.quality());
        policy.select("auto", 6000); assertFalse(policy.failure(7000));
        assertEquals("Auto · High", policy.label());
        assertEquals("auto", AdaptiveQualityPolicy.checkedMode("unexpected"));
    }
}
