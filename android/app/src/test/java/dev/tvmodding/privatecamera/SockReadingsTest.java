package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class SockReadingsTest {
    @Test public void expiresWithoutAnotherResponse() {
        SockReadings value = new SockReadings(true, 98, 120, "Light sleep", 89, 1000);
        assertEquals("O₂ 98% · HR 120 bpm · Light sleep", value.display(1500));
        assertEquals("", value.display(2001));
        assertEquals("", value.display(999));
    }
    @Test public void offlineAndInvalidAgeHideAllReadings() {
        assertEquals("", new SockReadings(false, 98, 120, "Awake", 0, 0).display(0));
        for (double age : new double[]{Double.NaN, Double.POSITIVE_INFINITY, -1, 91})
            assertEquals("", new SockReadings(true, 98, 120, "Awake", age, 0).display(0));
    }
    @Test public void missingValuesAreNotInventedAndSleepIsAllowlisted() {
        assertEquals("O₂ — · HR 120 bpm · Unknown", new SockReadings(true, -1, 120, "untrusted", 0, 0).display(1));
        assertEquals("", new SockReadings(true, 101, 401, "Awake", 0, 0).display(1));
    }
}
