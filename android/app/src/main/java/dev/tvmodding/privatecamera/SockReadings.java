package dev.tvmodding.privatecamera;

/** Reference readings expire against the monotonic clock, even without a reply. */
final class SockReadings {
    private final String text;
    private final long receivedAt, validFor;
    SockReadings(boolean live, int oxygen, int heartRate, String sleep, double sourceAgeSeconds, long receivedAt) {
        this.receivedAt = receivedAt;
        boolean validAge = Double.isFinite(sourceAgeSeconds) && sourceAgeSeconds >= 0 && sourceAgeSeconds <= 90;
        boolean oxygenValid = oxygen > 0 && oxygen <= 100, heartValid = heartRate > 0 && heartRate <= 400;
        validFor = live && validAge && (oxygenValid || heartValid) ? (long) ((90 - sourceAgeSeconds) * 1000) : -1;
        String label = "Awake".equals(sleep) || "Light sleep".equals(sleep) || "Deep sleep".equals(sleep) ? sleep : "Unknown";
        text = "O₂ " + (oxygenValid ? oxygen + "%" : "—") + " · HR " + (heartValid ? heartRate + " bpm" : "—") + " · " + label;
    }
    String display(long now) { return validFor >= 0 && now >= receivedAt && now - receivedAt <= validFor ? text : ""; }
}
