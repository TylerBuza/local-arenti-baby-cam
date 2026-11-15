package dev.tvmodding.privatecamera;

import android.content.Context;
import android.util.AttributeSet;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import androidx.media3.ui.PlayerView;

/** Pinch/drag the existing TextureView. Overlay controls and timestamps stay fixed. */
@androidx.annotation.OptIn(markerClass = androidx.media3.common.util.UnstableApi.class)
public final class ZoomPlayerView extends PlayerView {
    private final ZoomState zoom = new ZoomState();
    private final GestureDetector gestures;
    private final ScaleGestureDetector scaling;
    private Runnable tap;
    private float restoreScale = 1, restoreX, restoreY;
    private boolean restorePending;

    public ZoomPlayerView(Context context, AttributeSet attrs) {
        super(context, attrs);
        scaling = new ScaleGestureDetector(context, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override public boolean onScale(ScaleGestureDetector detector) {
                View surface = getVideoSurfaceView();
                if (surface == null || surface.getWidth() == 0) return false;
                int[] here = new int[2], there = new int[2];
                getLocationOnScreen(here); surface.getLocationOnScreen(there);
                // getLocationOnScreen includes the View's scale/translation. Undo
                // that offset to express the pinch focus around its original centre.
                float centerX = there[0] - here[0] + surface.getWidth() * zoom.scale / 2;
                float centerY = there[1] - here[1] + surface.getHeight() * zoom.scale / 2;
                zoom.zoom(detector.getScaleFactor(), detector.getFocusX() - centerX + zoom.x,
                    detector.getFocusY() - centerY + zoom.y);
                applyZoom();
                return true;
            }
        });
        scaling.setQuickScaleEnabled(false);
        gestures = new GestureDetector(context, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent event) { return true; }
            @Override public boolean onSingleTapConfirmed(MotionEvent event) {
                performClick();
                if (tap != null) tap.run();
                return true;
            }
            @Override public boolean onDoubleTap(MotionEvent event) { resetZoom(); return true; }
            @Override public boolean onScroll(MotionEvent start, MotionEvent end, float dx, float dy) {
                if (!scaling.isInProgress() && end.getPointerCount() == 1) { zoom.pan(-dx, -dy); applyZoom(); }
                return true;
            }
        });
        View surface = getVideoSurfaceView();
        if (surface != null) surface.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> applyZoom());
    }
    void setOnVideoTap(Runnable action) { tap = action; }
    public float getZoom() { return zoom.scale; }
    float getZoomX() { return zoom.normalizedX(); }
    float getZoomY() { return zoom.normalizedY(); }
    void restoreZoom(float scale, float x, float y) {
        restoreScale = scale; restoreX = x; restoreY = y; restorePending = true; applyZoom();
    }
    public void resetZoom() { restorePending = false; zoom.reset(); applyZoom(); }
    private void applyZoom() {
        View surface = getVideoSurfaceView();
        if (surface == null || surface.getWidth() <= 0 || surface.getHeight() <= 0) return;
        zoom.resize(surface.getWidth(), surface.getHeight());
        if (restorePending) { zoom.restore(restoreScale, restoreX, restoreY); restorePending = false; }
        surface.setPivotX(surface.getWidth() / 2f); surface.setPivotY(surface.getHeight() / 2f);
        surface.setScaleX(zoom.scale); surface.setScaleY(zoom.scale);
        surface.setTranslationX(zoom.x); surface.setTranslationY(zoom.y);
    }
    @Override public boolean onInterceptTouchEvent(MotionEvent event) { return true; }
    @Override public boolean onTouchEvent(MotionEvent event) {
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN && getParent() != null)
            getParent().requestDisallowInterceptTouchEvent(true);
        scaling.onTouchEvent(event);
        gestures.onTouchEvent(event);
        return true;
    }
    @Override public boolean performClick() { super.performClick(); return true; }
}
