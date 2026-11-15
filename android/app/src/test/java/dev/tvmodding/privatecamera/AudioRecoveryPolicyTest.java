package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class AudioRecoveryPolicyTest {
    @Test public void continuedIncomingDataWithMissingAudioCanFallBackToVideoOnly() {
        assertTrue(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 6000, 9900, 6000));
    }
    @Test public void missingNetworkDataNeverGetsMisdiagnosedAsAudioOnlyFailure() {
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 6000, 7000, 6000));
    }
    @Test public void ordinaryBriefBufferingDoesNotDisableSound() {
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 8000, 9900, 9000));
    }
    @Test public void mutedPausedAbsentOrAlreadyFallbackAudioDoesNotTrigger() {
        assertFalse(AudioRecoveryPolicy.shouldFallback(false, false, true, 10000, 6000, 9900, 6000));
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, true, true, 10000, 6000, 9900, 6000));
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, false, 10000, 6000, 9900, 6000));
    }
    @Test public void futureOrUninitializedTimingDoesNotTrigger() {
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 0, 9900, 0));
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 11000, 9900, 0));
        assertFalse(AudioRecoveryPolicy.shouldFallback(true, false, true, 10000, 6000, 11000, 0));
    }
}
