package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class ZoomStateTest {
    private ZoomState view() { ZoomState zoom = new ZoomState(); zoom.resize(320, 180); return zoom; }
    @Test public void initiallyShowsWholeImage() {
        ZoomState zoom = view(); assertEquals(1, zoom.scale, 0); assertEquals(0, zoom.x, 0); assertEquals(0, zoom.y, 0);
    }
    @Test public void digitalZoomIsBounded() {
        ZoomState zoom = view(); zoom.zoom(100, 0, 0); assertEquals(6, zoom.scale, 0);
        zoom.zoom(0.001f, 0, 0); assertEquals(1, zoom.scale, 0);
    }
    @Test public void zoomKeepsPinchFocusOnSameSourcePoint() {
        ZoomState zoom = view(); zoom.zoom(2, 50, 20);
        assertEquals(-50, zoom.x, 0); assertEquals(-20, zoom.y, 0);
        assertEquals(50, (50 - zoom.x) / zoom.scale, 0);
    }
    @Test public void panningCannotExposeOutsideImage() {
        ZoomState zoom = view(); zoom.zoom(2, 0, 0); zoom.pan(10000, -10000);
        assertEquals(160, zoom.x, 0); assertEquals(-90, zoom.y, 0);
    }
    @Test public void unzoomedImageCannotBePanned() {
        ZoomState zoom = view(); zoom.pan(50, 90); assertEquals(0, zoom.x, 0); assertEquals(0, zoom.y, 0);
    }
    @Test public void rotationAndPipResizeKeepNormalizedCrop() {
        ZoomState zoom = view(); zoom.zoom(3, 0, 0); zoom.pan(100, -30); zoom.resize(640, 360);
        assertEquals(200, zoom.x, 0); assertEquals(-60, zoom.y, 0); assertEquals(3, zoom.scale, 0);
    }
    @Test public void savedCropRestoresWithoutChangingZoom() {
        ZoomState zoom = view(); zoom.restore(2, 0.25f, -0.25f);
        assertEquals(80, zoom.x, 0); assertEquals(-45, zoom.y, 0);
        assertEquals(0.25f, zoom.normalizedX(), 0); assertEquals(-0.25f, zoom.normalizedY(), 0);
    }
    @Test public void resetRestoresEntireFrame() {
        ZoomState zoom = view(); zoom.zoom(4, 40, -20); zoom.reset();
        assertEquals(1, zoom.scale, 0); assertEquals(0, zoom.x, 0); assertEquals(0, zoom.y, 0);
    }
    @Test public void malformedGestureValuesDoNotPoisonTransform() {
        ZoomState zoom = view(); zoom.zoom(Float.NaN, 0, 0); zoom.zoom(-2, 0, 0);
        zoom.zoom(Float.POSITIVE_INFINITY, 0, 0); zoom.pan(Float.NaN, 1); zoom.resize(0, 0);
        assertEquals(1, zoom.scale, 0); assertEquals(0, zoom.x, 0); assertEquals(0, zoom.y, 0);
    }
    @Test public void malformedSavedStateFailsToWholeImage() {
        ZoomState zoom = view(); zoom.restore(Float.NaN, Float.POSITIVE_INFINITY, Float.NaN);
        assertEquals(1, zoom.scale, 0); assertEquals(0, zoom.x, 0); assertEquals(0, zoom.y, 0);
    }
}
