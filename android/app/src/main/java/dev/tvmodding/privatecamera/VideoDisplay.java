package dev.tvmodding.privatecamera;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

final class VideoDisplay {
    static boolean fresh(boolean playing, long now, long lastFrame) {
        return playing && lastFrame > 0 && now >= lastFrame && now - lastFrame <= 1200;
    }
    static String clock(long millis, ZoneId zone, boolean compact) {
        return DateTimeFormatter.ofPattern(compact ? "yyyy-MM-dd\nHH:mm:ss" : "yyyy-MM-dd HH:mm:ss", Locale.ROOT)
            .withZone(zone).format(Instant.ofEpochMilli(millis));
    }
}
