package dev.tvmodding.privatecamera;

final class AudioRecoveryPolicy {
    static boolean shouldFallback(boolean soundWanted, boolean alreadyFallback, boolean audioTrack,
                                  long now, long bufferingSince, long lastRead, long lastFrame) {
        return soundWanted && !alreadyFallback && audioTrack && bufferingSince > 0
            && now >= bufferingSince && now - bufferingSince >= 3000
            && lastRead > 0 && now >= lastRead && now - lastRead <= 1000
            && (lastFrame == 0 || now >= lastFrame && now - lastFrame >= 2500);
    }
}
