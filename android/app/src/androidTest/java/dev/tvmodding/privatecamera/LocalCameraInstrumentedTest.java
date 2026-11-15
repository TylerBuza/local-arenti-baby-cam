package dev.tvmodding.privatecamera;

import android.content.Context;
import android.net.ConnectivityManager;
import android.os.SystemClock;
import android.view.View;
import android.widget.TextView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.media3.exoplayer.ExoPlayer;
import org.junit.Test;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.*;
import static androidx.test.espresso.matcher.ViewMatchers.*;

@androidx.annotation.OptIn(markerClass = androidx.media3.common.util.UnstableApi.class)
public class LocalCameraInstrumentedTest {
    private final Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
    private static final String URL = "rtsp://10.0.2.2:18554/live";
    private static final String PASSWORD = "fixture:@% password";

    private void waitForVideo(ActivityScenario<MainActivity> scenario, Object previous) {
        AtomicBoolean ready = new AtomicBoolean();
        long deadline = SystemClock.elapsedRealtime() + 35000;
        while (!ready.get() && SystemClock.elapsedRealtime() < deadline) {
            scenario.onActivity(screen -> {
                ZoomPlayerView video = screen.findViewById(R.id.video);
                ready.set(video.getPlayer() != null && video.getPlayer() != previous
                    && video.getPlayer().isPlaying() && video.getPlayer().getCurrentPosition() > 700);
            });
            SystemClock.sleep(100);
        }
        assertTrue("Direct RTSP video did not play/recover", ready.get());
    }

    @Test public void directSetupPlaybackRecoveryStopAndReloadWithoutConnector() throws Exception {
        assertNull(new Profile.Store(context).load());
        try {
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                onView(withId(R.id.pair)).perform(click());
                onView(withText("Set up local camera (no PC)")).perform(click());
                onView(withHint("rtsp://192.168.1.20:554/stream-path")).perform(scrollTo(), replaceText(URL));
                onView(withHint("Camera username")).perform(scrollTo(), replaceText("fixture-viewer"));
                onView(withHint("Camera password")).perform(scrollTo(), replaceText(PASSWORD), closeSoftKeyboard());
                onView(withText("Save and connect")).perform(click());
                waitForVideo(scenario, null);
                AtomicReference<Object> first = new AtomicReference<>();
                scenario.onActivity(screen -> {
                    first.set(((ZoomPlayerView)screen.findViewById(R.id.video)).getPlayer());
                    assertEquals("Direct local camera · no PC", ((TextView)screen.findViewById(R.id.route)).getText().toString());
                    assertFalse(screen.findViewById(R.id.quality).isEnabled());
                    assertEquals(View.GONE, screen.findViewById(R.id.move_toggle).getVisibility());
                    assertEquals(View.GONE, screen.findViewById(R.id.clock).getVisibility());
                });
                waitForVideo(scenario, first.get()); // Fixture deliberately disconnects its first PLAY.
                scenario.onActivity(screen -> screen.findViewById(R.id.mute).performClick());
                waitForVideo(scenario, null);
                AtomicBoolean audio = new AtomicBoolean();
                long audioDeadline = SystemClock.elapsedRealtime() + 15000;
                while (!audio.get() && SystemClock.elapsedRealtime() < audioDeadline) {
                    scenario.onActivity(screen -> {
                        ExoPlayer player = (ExoPlayer)((ZoomPlayerView)screen.findViewById(R.id.video)).getPlayer();
                        audio.set(player != null && player.getAudioDecoderCounters() != null
                            && player.getAudioDecoderCounters().renderedOutputBufferCount > 0);
                    });
                    SystemClock.sleep(100);
                }
                assertTrue("Synthetic PCMU audio was not rendered", audio.get());
                scenario.onActivity(screen -> screen.findViewById(R.id.stop).performClick());
                SystemClock.sleep(3500);
                scenario.onActivity(screen -> {
                    assertNull(((ZoomPlayerView)screen.findViewById(R.id.video)).getPlayer());
                    assertEquals("Stopped.", ((TextView)screen.findViewById(R.id.status)).getText().toString());
                });
            }
            LocalCamera saved = new LocalCameraStore(context).load();
            assertEquals(PASSWORD, saved.password);
            String preferences = context.getSharedPreferences("local-camera-connection", Context.MODE_PRIVATE).getAll().toString();
            assertFalse(preferences.contains(PASSWORD)); assertFalse(preferences.contains(URL));
            assertNull(new Profile.Store(context).load());
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                waitForVideo(scenario, null);
                scenario.onActivity(screen -> {
                    screen.findViewById(R.id.stop).performClick();
                    screen.findViewById(R.id.start).performClick();
                    screen.findViewById(R.id.stop).performClick(); // Cancel an in-flight authentication probe.
                });
                SystemClock.sleep(2500);
                scenario.onActivity(screen -> assertNull(((ZoomPlayerView)screen.findViewById(R.id.video)).getPlayer()));
            }
        } finally {
            new LocalCameraStore(context).clear();
            context.getSharedPreferences("viewing-preferences", Context.MODE_PRIVATE).edit().remove("directCamera").commit();
        }
    }

    @Test public void protectedSettingsRejectTampering() throws Exception {
        LocalCameraStore store = new LocalCameraStore(context);
        try {
            store.save(new LocalCamera(URL, "fixture-viewer", PASSWORD));
            android.content.SharedPreferences preferences = context.getSharedPreferences("local-camera-connection", Context.MODE_PRIVATE);
            byte[] bytes = android.util.Base64.decode(preferences.getString("sealed", ""), android.util.Base64.DEFAULT);
            bytes[bytes.length - 1] ^= 1;
            preferences.edit().putString("sealed", android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)).commit();
            assertThrows(Exception.class, store::load);
        } finally { store.clear(); }
    }

    @Test public void anonymousStreamsAndChangedTargetsAreRefused() throws Exception {
        LocalCamera camera = new LocalCamera("rtsp://10.0.2.2:18554/anonymous", "fixture-viewer", PASSWORD);
        try (LocalCameraConnection connection = LocalCameraConnection.select(camera,
                (ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE))) {
            assertThrows(java.io.IOException.class, connection::requireAuthentication);
            assertThrows(java.io.IOException.class, () -> connection.createSocket("example.com", 18554));
            assertThrows(java.io.IOException.class, () -> connection.createSocket("10.0.2.2", 554));
            assertThrows(java.io.IOException.class, connection::createSocket);
            connection.close();
            assertThrows(java.io.IOException.class, () -> connection.createSocket("10.0.2.2", 18554));
        }
    }
}
