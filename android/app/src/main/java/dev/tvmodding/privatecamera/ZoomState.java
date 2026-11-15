package dev.tvmodding.privatecamera;

/** Digital display crop only; never changes the camera, decoder or stream. */
final class ZoomState {
    static final float MAX = 6f;
    float scale = 1f, x, y;
    private float width, height;

    void resize(float nextWidth, float nextHeight) {
        if (!Float.isFinite(nextWidth) || !Float.isFinite(nextHeight) || nextWidth <= 0 || nextHeight <= 0) return;
        x = width > 0 ? x * nextWidth / width : 0;
        y = height > 0 ? y * nextHeight / height : 0;
        width = nextWidth; height = nextHeight;
        clamp();
    }
    void zoom(float factor, float focusX, float focusY) {
        if (!Float.isFinite(factor) || factor <= 0 || !Float.isFinite(focusX) || !Float.isFinite(focusY)) return;
        float next = Math.max(1f, Math.min(MAX, scale * factor));
        float ratio = next / scale;
        x = focusX - (focusX - x) * ratio;
        y = focusY - (focusY - y) * ratio;
        scale = next;
        clamp();
    }
    void pan(float dx, float dy) {
        if (!Float.isFinite(dx) || !Float.isFinite(dy)) return;
        x += dx; y += dy; clamp();
    }
    void restore(float zoom, float normalizedX, float normalizedY) {
        scale = Float.isFinite(zoom) ? Math.max(1f, Math.min(MAX, zoom)) : 1f;
        x = Float.isFinite(normalizedX) ? normalizedX * width : 0;
        y = Float.isFinite(normalizedY) ? normalizedY * height : 0;
        clamp();
    }
    float normalizedX() { return width > 0 ? x / width : 0; }
    float normalizedY() { return height > 0 ? y / height : 0; }
    void reset() { scale = 1; x = y = 0; }
    private void clamp() {
        float boundX = width * (scale - 1) / 2, boundY = height * (scale - 1) / 2;
        x = Math.max(-boundX, Math.min(boundX, x));
        y = Math.max(-boundY, Math.min(boundY, y));
    }
}
