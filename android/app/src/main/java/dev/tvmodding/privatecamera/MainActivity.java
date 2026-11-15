package dev.tvmodding.privatecamera;

import android.app.AppOpsManager;
import android.app.PendingIntent;
import android.app.PictureInPictureParams;
import android.app.PictureInPictureUiState;
import android.app.RemoteAction;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Rect;
import android.graphics.drawable.Icon;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Rational;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.EditText;
import android.widget.ScrollView;
import android.widget.Toast;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.appcompat.app.AppCompatDelegate;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.core.content.ContextCompat;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.PlaybackParameters;
import androidx.media3.common.Player;
import androidx.media3.common.util.Log;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.ProgressiveMediaSource;
import androidx.media3.exoplayer.rtsp.RtspMediaSource;
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.Arrays;
import java.time.ZoneId;

@androidx.annotation.OptIn(markerClass = androidx.media3.common.util.UnstableApi.class)
public final class MainActivity extends AppCompatActivity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newFixedThreadPool(2);
    private final ExecutorService control = Executors.newSingleThreadExecutor();
    private final ExecutorService healthIo = Executors.newSingleThreadExecutor();
    private final AtomicBoolean healthPending = new AtomicBoolean();
    private long nextHealthPoll;
    private SockReadings sockReadings;
    private final HoldGuard hold = new HoldGuard();
    private final PlaybackWatchdog watchdog = new PlaybackWatchdog();
    private final LivePlaybackPolicy livePolicy = new LivePlaybackPolicy();
    private final AdaptiveQualityPolicy adaptiveQuality = new AdaptiveQualityPolicy();
    private float playbackSpeed = 1f;
    private Profile profile;
    private LocalCamera localCamera;
    private LocalCameraStore localStore;
    private LocalCameraConnection localConnection;
    private boolean directMode;
    private FamilyAccess family;
    private boolean familyInFlight;
    private Profile.Store store;
    private ConnectivityManager connectivity;
    private ZoomPlayerView video;
    private TextView status, routeLabel, clock, sockLabel;
    private ExoPlayer player;
    private volatile Viewing viewing;
    private Button[] arrows;
    private View pressed;
    private volatile String direction;
    private boolean active, userStopped, muted = true, ptzReady;
    private boolean fullscreen, enteringPip, controlsVisible = true, movementExpanded, pipSupported;
    private boolean pipReceiverRegistered;
    private static final String PIP_STOP = "dev.tvmodding.privatecamera.PIP_STOP";
    private static final String PIP_MUTE = "dev.tvmodding.privatecamera.PIP_MUTE";
    private volatile int generation;
    private int retryCount;
    private volatile long lastVideoFrame;
    private volatile long lastStatusFrame;
    private volatile long lastStreamRead;
    private long bufferingSince;
    private boolean audioUnavailable, audioInterrupted;
    private volatile Network lastDefault;
    private final Set<Network> knownWifi = Collections.synchronizedSet(new HashSet<>());
    private final Runnable hideControls = () -> {
        if (fullscreen && !isInPictureInPictureMode() && freshVideo() && !movementExpanded) {
            controlsVisible = false;
            applyPresentation();
        }
    };
    private final Runnable displayTick = new Runnable() {
        @Override public void run() {
            if (!active) return;
            ensureVideoPlaying();
            adaptQuality();
            refreshDisplay();
            main.postDelayed(this, 500);
        }
    };
    private final BroadcastReceiver pipReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (!isInPictureInPictureMode()) return;
            if (PIP_STOP.equals(intent.getAction())) stopByUser();
            else if (PIP_MUTE.equals(intent.getAction())) toggleMute();
        }
    };

    private static final class Viewing {
        final GatewayApi api;
        final GatewayApi.Route route;
        final String id;
        final AtomicInteger sequence = new AtomicInteger();
        final AtomicBoolean movePending = new AtomicBoolean();
        Viewing(GatewayApi api, GatewayApi.Route route, String id) { this.api = api; this.route = route; this.id = id; }
    }
    private boolean configured() { return directMode ? localCamera != null : profile != null || family != null; }
    private final Runnable reconnect = () -> { if (active && !userStopped && configured()) connect(); };
    private final ConnectivityManager.NetworkCallback networks = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) {
            if (!network.equals(lastDefault)) { lastDefault = network; scheduleNetworkCheck(); }
        }
        @Override public void onLost(Network network) {
            if (network.equals(lastDefault)) { lastDefault = null; scheduleNetworkCheck(); }
        }
    };
    private final ConnectivityManager.NetworkCallback wifiNetworks = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) { if (knownWifi.add(network)) scheduleNetworkCheck(); }
        @Override public void onLost(Network network) { if (knownWifi.remove(network)) scheduleNetworkCheck(); }
    };
    private final Runnable renew = new Runnable() {
        @Override public void run() {
            if (direction == null) return;
            if (!hold.canRenew(SystemClock.elapsedRealtime(), lastVideoFrame, player != null && player.isPlaying())) {
                releaseMovement(false);
                return;
            }
            Viewing current = viewing;
            String action = direction;
            if (current != null && current.movePending.compareAndSet(false, true)) {
                int sequence = current.sequence.incrementAndGet();
                control.execute(() -> {
                    try {
                        if (direction != null && viewing == current)
                            current.api.json(current.route, "/v1/ptz", new JSONObject().put("view", current.id)
                                .put("action", action).put("sequence", sequence), 1000);
                    } catch (Exception ignored) {
                        main.post(() -> { if (viewing == current) releaseMovement(false); });
                    } finally { current.movePending.set(false); }
                });
            }
            main.postDelayed(this, 200);
        }
    };
    private final ActivityResultLauncher<ScanOptions> scan = registerForActivityResult(new ScanContract(), result -> {
        if (result.getContents() == null) { status.setText("Pairing cancelled."); return; }
        status.setText("Pairing…");
        final int expected = ++generation;
        io.execute(() -> {
            try {
                Profile paired = GatewayApi.enroll(result.getContents(), connectivity);
                store.save(paired);
                main.post(() -> {
                    if (isFinishing() || expected != generation) return;
                    profile = paired;
                    selectDirect(false);
                    userStopped = false;
                    status.setText("Connecting…");
                    connect();
                });
            } catch (Exception ignored) {
                main.post(() -> { if (expected == generation) status.setText("Pairing failed. Use a fresh code on your home Wi-Fi."); });
            }
        });
    });

    @Override protected void onCreate(Bundle state) {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES);
        super.onCreate(state);
        Log.setLogLevel(Log.LOG_LEVEL_OFF);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        setContentView(R.layout.activity_main);
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.root), (view, insets) -> {
            boolean pip = isInPictureInPictureMode() || enteringPip;
            androidx.core.graphics.Insets bars = pip ? androidx.core.graphics.Insets.NONE :
                insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            int pad = pip || fullscreen ? 0 : dp(16);
            view.setPadding(bars.left + pad, bars.top + pad, bars.right + pad, bars.bottom + pad);
            return insets;
        });
        userStopped = state != null && state.getBoolean("stopped", false);
        String mode = state != null ? state.getString("qualityMode", "auto")
            : getSharedPreferences("viewing-preferences", MODE_PRIVATE).getString("qualityMode", "auto");
        adaptiveQuality.restore(mode, state != null ? state.getString("quality", "high") : "high",
            state != null ? state.getLong("qualityProbeDelay", AdaptiveQualityPolicy.STABLE_MS) : AdaptiveQualityPolicy.STABLE_MS,
            SystemClock.elapsedRealtime());
        fullscreen = state != null && state.getBoolean("fullscreen", false);
        connectivity = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        store = new Profile.Store(this);
        localStore = new LocalCameraStore(this);
        directMode = getSharedPreferences("viewing-preferences", MODE_PRIVATE).getBoolean("directCamera", false);
        video = findViewById(R.id.video);
        findViewById(R.id.video_frame).addOnLayoutChangeListener((v, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> sizeOverlayControls());
        status = findViewById(R.id.status);
        routeLabel = findViewById(R.id.route);
        clock = findViewById(R.id.clock);
        sockLabel = findViewById(R.id.sock_readings);
        if (state != null) video.restoreZoom(state.getFloat("zoom", 1), state.getFloat("zoomX", 0), state.getFloat("zoomY", 0));
        video.setOnVideoTap(() -> {
            controlsVisible = !fullscreen || !controlsVisible;
            if (!controlsVisible) setMovementExpanded(false);
            applyPresentation(); scheduleControlsHide();
        });
        video.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> { positionClock(); updatePipParams(); });
        pipSupported = getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE);
        findViewById(R.id.pip).setVisibility(pipSupported ? View.VISIBLE : View.GONE);
        findViewById(R.id.pip).setOnClickListener(view -> enterPip());
        findViewById(R.id.fullscreen).setOnClickListener(view -> setFullscreen(!fullscreen));
        findViewById(R.id.move_toggle).setOnClickListener(view -> setMovementExpanded(!movementExpanded));
        IntentFilter pipFilter = new IntentFilter(PIP_STOP); pipFilter.addAction(PIP_MUTE);
        ContextCompat.registerReceiver(this, pipReceiver, pipFilter, ContextCompat.RECEIVER_NOT_EXPORTED);
        pipReceiverRegistered = true;
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                if (movementExpanded) setMovementExpanded(false);
                else if (fullscreen) setFullscreen(false);
                else { setEnabled(false); getOnBackPressedDispatcher().onBackPressed(); }
            }
        });
        arrows = new Button[]{findViewById(R.id.up), findViewById(R.id.left), findViewById(R.id.right), findViewById(R.id.down)};
        String[] directions = {"up", "left", "right", "down"};
        for (int i = 0; i < arrows.length; i++) {
            final String action = directions[i];
            Button button = arrows[i];
            button.setOnTouchListener((view, event) -> {
                if (event.getActionMasked() == MotionEvent.ACTION_DOWN) pressMovement(action, view);
                else if (event.getActionMasked() == MotionEvent.ACTION_UP || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                    releaseMovement(false);
                    if (event.getActionMasked() == MotionEvent.ACTION_UP) view.performClick();
                }
                return true;
            });
            button.setOnKeyListener((view, key, event) -> {
                if (key != KeyEvent.KEYCODE_ENTER && key != KeyEvent.KEYCODE_DPAD_CENTER && key != KeyEvent.KEYCODE_SPACE) return false;
                if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0) pressMovement(action, view);
                else if (event.getAction() == KeyEvent.ACTION_UP) releaseMovement(false);
                return true;
            });
            button.setOnFocusChangeListener((view, focused) -> { if (!focused && pressed == view) releaseMovement(false); });
        }
        enablePtz(false);
        findViewById(R.id.ptz_stop).setOnClickListener(view -> releaseMovement(true));
        findViewById(R.id.start).setOnClickListener(view -> { retryCount = 0; watchdog.reset(); audioUnavailable = audioInterrupted = false; userStopped = false; if (configured()) connect(); else connectionMenu(); });
        findViewById(R.id.stop).setOnClickListener(view -> stopByUser());
        findViewById(R.id.mute).setOnClickListener(view -> toggleMute());
        findViewById(R.id.pair).setOnClickListener(view -> connectionMenu());
        Spinner selector = findViewById(R.id.quality);
        ArrayAdapter<String> qualityItems = new ArrayAdapter<>(this, R.layout.quality_item, new String[]{"Auto", "High", "Low"});
        qualityItems.setDropDownViewResource(R.layout.quality_dropdown);
        selector.setAdapter(qualityItems);
        selector.setSelection(adaptiveQuality.mode().equals("auto") ? 0 : adaptiveQuality.mode().equals("high") ? 1 : 2);
        selector.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(AdapterView<?> parent) { }
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                String selected = position == 2 ? "low" : position == 1 ? "high" : "auto";
                if (!adaptiveQuality.mode().equals(selected)) {
                    adaptiveQuality.select(selected, SystemClock.elapsedRealtime());
                    getSharedPreferences("viewing-preferences", MODE_PRIVATE).edit().putString("qualityMode", selected).apply();
                    watchdog.reset();
                    if (configured() && active && !userStopped) connect();
                }
            }
        });
        try { profile = store.load(); } catch (Exception ignored) { status.setText("Saved connection unavailable."); }
        try { localCamera = localStore.load(); } catch (Exception ignored) { status.setText("Saved local camera unavailable. Open Connection to set it up again."); }
        try { family = FamilyAccess.load(this); } catch (Exception ignored) { status.setText("App access unavailable. Get an updated APK."); }
        if (family != null) findViewById(R.id.pair).setVisibility(View.GONE);
        if (configured() && userStopped) status.setText("Stopped.");
        updateConnectionControls();
        applyPresentation();
    }
    private boolean freshVideo() {
        return active && !userStopped && VideoDisplay.fresh(player != null && player.isPlaying(),
            SystemClock.elapsedRealtime(), lastVideoFrame);
    }
    private void stopByUser() {
        userStopped = true;
        stopPlayback();
        setMovementExpanded(false);
        controlsVisible = true;
        status.setText("Stopped.");
        applyPresentation(); refreshDisplay();
    }
    private void toggleMute() {
        boolean wasSound = !muted && !audioUnavailable && !audioInterrupted;
        if (audioUnavailable || audioInterrupted) { audioUnavailable = audioInterrupted = false; muted = false; }
        else muted = !muted;
        if (!wasSound && !muted && player != null && active && !userStopped) {
            // Enabling a previously discarded track in non-seekable MP4 may
            // otherwise make ExoPlayer seek/retry an obsolete byte position.
            retryCount = 0; watchdog.reset(); connect();
        } else applySound();
        updatePipParams(); scheduleControlsHide();
    }
    private void applySound() {
        boolean sound = !muted && !audioUnavailable && !audioInterrupted;
        ((Button) findViewById(R.id.mute)).setText(audioUnavailable ? "Video only" : audioInterrupted ? "Resume sound" : muted ? "Muted" : "Sound on");
        if (player != null) {
            player.setVolume(sound ? 1 : 0);
            player.setAudioAttributes(new AudioAttributes.Builder().setUsage(C.USAGE_MEDIA)
                .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), sound);
            // Volume zero alone still lets a missing audio track stall the video.
            player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, !sound).build());
        }
    }
    private boolean wantsVideo() { return active && !userStopped && configured(); }
    private void adaptQuality() {
        if (directMode || !wantsVideo() || player == null) return;
        // Audio focus interruptions are handled separately; do not mistake a
        // deliberate audio yield for insufficient video bandwidth.
        if (player.getPlayWhenReady() && player.getPlaybackSuppressionReason() == Player.PLAYBACK_SUPPRESSION_REASON_NONE
                && adaptiveQuality.sample(SystemClock.elapsedRealtime(), freshVideo(),
                    player.getPlaybackState() == Player.STATE_BUFFERING, player.getTotalBufferedDuration())) {
            connect();
        }
    }
    private void ensureVideoPlaying() {
        if (!wantsVideo() || player == null) return;
        // Audio focus loss (including a rejected request while in PiP) can clear
        // playWhenReady or suppress playback without producing a player error.
        // Yield sound rather than allowing that state to pause video forever.
        if (!player.getPlayWhenReady() || player.getPlaybackSuppressionReason() != Player.PLAYBACK_SUPPRESSION_REASON_NONE) {
            if (!muted && !audioUnavailable && !audioInterrupted) {
                audioInterrupted = true;
                applySound();
            }
            player.play();
        }
    }
    private void setMovementExpanded(boolean expanded) {
        movementExpanded = expanded && !isInPictureInPictureMode() && !enteringPip;
        if (!movementExpanded) releaseMovement(false);
        findViewById(R.id.move_panel).setVisibility(movementExpanded ? View.VISIBLE : View.GONE);
        ((Button) findViewById(R.id.move_toggle)).setText(movementExpanded ? "Hide move" : "Move");
        findViewById(R.id.move_toggle).setContentDescription(movementExpanded ? "Hide movement controls" : "Show movement controls");
        scheduleControlsHide();
    }
    private void setFullscreen(boolean enabled) {
        releaseMovement(false); setMovementExpanded(false);
        fullscreen = enabled; controlsVisible = true;
        setRequestedOrientation(enabled ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE : ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        applyPresentation(); scheduleControlsHide();
    }
    private void applyPresentation() {
        boolean pip = isInPictureInPictureMode() || enteringPip;
        boolean immersive = fullscreen || pip;
        LinearLayout controls = findViewById(R.id.video_controls);
        LinearLayout dock = findViewById(R.id.control_dock);
        FrameLayout frame = findViewById(R.id.video_frame);
        ViewGroup host = immersive ? frame : dock;
        if (controls.getParent() != host) {
            ((ViewGroup) controls.getParent()).removeView(controls);
            host.addView(controls);
        }
        if (immersive) {
            FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                dp(Math.min(560, getResources().getConfiguration().screenWidthDp - 24)), ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
            params.setMargins(dp(12), 0, dp(12), dp(12));
            controls.setLayoutParams(params);
        } else controls.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        dock.setVisibility(immersive ? View.GONE : View.VISIBLE);
        findViewById(R.id.control_scroll).setVisibility(immersive ? View.GONE : View.VISIBLE);
        frame.setClipToOutline(!immersive);
        if (immersive) frame.setBackgroundColor(android.graphics.Color.BLACK);
        else frame.setBackgroundResource(R.drawable.bg_video);
        controls.setOrientation(immersive ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        View movePanel = findViewById(R.id.move_panel);
        int movementIndex = immersive ? 1 : 2;
        if (controls.indexOfChild(movePanel) != movementIndex) {
            controls.removeView(movePanel);
            controls.addView(movePanel, movementIndex);
        }
        LinearLayout.LayoutParams moveParams = new LinearLayout.LayoutParams(immersive ? dp(212) : ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        moveParams.setMarginEnd(immersive ? dp(12) : 0);
        findViewById(R.id.move_panel).setLayoutParams(moveParams);
        for (int id : new int[]{R.id.up, R.id.left, R.id.ptz_stop, R.id.right, R.id.down}) {
            View direction = findViewById(id);
            ViewGroup.LayoutParams params = direction.getLayoutParams();
            params.width = dp(immersive ? 64 : 76);
            direction.setLayoutParams(params);
        }
        findViewById(R.id.playback_panel).setLayoutParams(new LinearLayout.LayoutParams(
            immersive ? 0 : ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, immersive ? 1 : 0));
        findViewById(R.id.control_heading).setVisibility(View.GONE);
        findViewById(R.id.gesture_hint).setVisibility(View.GONE);
        findViewById(R.id.header).setVisibility(fullscreen || pip ? View.GONE : View.VISIBLE);
        controls.setVisibility(pip || fullscreen && !controlsVisible ? View.GONE : View.VISIBLE);
        ((Button) findViewById(R.id.fullscreen)).setText(fullscreen ? "Exit full" : "Full screen");
        boolean shortScreen = getResources().getConfiguration().screenHeightDp < 650;
        findViewById(R.id.camera_heading).setVisibility(shortScreen ? View.GONE : View.VISIBLE);
        LinearLayout.LayoutParams routeParams = (LinearLayout.LayoutParams) findViewById(R.id.route).getLayoutParams();
        routeParams.topMargin = dp(shortScreen ? 12 : 4);
        findViewById(R.id.route).setLayoutParams(routeParams);
        clock.setTextSize(pip ? 9 : 12);
        clock.setText(VideoDisplay.clock(System.currentTimeMillis(), ZoneId.systemDefault(), pip));
        status.setTextSize(pip ? 11 : 14);
        int statusPadding = dp(pip ? 6 : 8);
        status.setPadding(statusPadding, statusPadding, statusPadding, statusPadding);
        WindowInsetsControllerCompat bars = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        bars.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        if (fullscreen || pip) bars.hide(WindowInsetsCompat.Type.systemBars());
        else bars.show(WindowInsetsCompat.Type.systemBars());
        ViewCompat.requestApplyInsets(findViewById(R.id.root));
        positionClock();
        sizeOverlayControls();
        clock.setVisibility(View.GONE); // Keep only the timestamp embedded in the camera picture.
        updateSockDisplay();
    }
    private void sizeOverlayControls() {
        View controls = findViewById(R.id.video_controls);
        FrameLayout frame = findViewById(R.id.video_frame);
        if (controls.getParent() != frame || frame.getWidth() <= 0) return;
        FrameLayout.LayoutParams params = (FrameLayout.LayoutParams) controls.getLayoutParams();
        int width = Math.min(dp(560), Math.max(1, frame.getWidth() - dp(24)));
        if (params.width != width) { params.width = width; controls.setLayoutParams(params); }
        updateSockDisplay();
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void positionClock() {
        if (video == null || clock == null) return;
        View surface = video.getVideoSurfaceView();
        int inset = (int) (6 * getResources().getDisplayMetrics().density);
        // Anchor inside the aspect-fitted original image, not inside the zoom crop.
        clock.setX(inset + Math.max(0, (video.getWidth() - (surface != null ? surface.getWidth() : 0)) / 2));
        clock.setY(inset + Math.max(0, (video.getHeight() - (surface != null ? surface.getHeight() : 0)) / 2));
    }
    private void pollSockReadings() {
        Viewing current = viewing;
        long now = SystemClock.elapsedRealtime();
        if (!active || userStopped || current == null || !freshVideo() || now < nextHealthPoll
                || !healthPending.compareAndSet(false, true)) return;
        nextHealthPoll = now + 5000;
        healthIo.execute(() -> {
            SockReadings result = null;
            try {
                JSONObject value = current.api.json(current.route, "/v1/owlet?view=" + current.id, null, 2000);
                if (value.optInt("schema") == 1) result = new SockReadings("live".equals(value.optString("availability")),
                    value.optInt("oxygen", -1), value.optInt("heart_rate", -1), value.optString("sleep", "Unknown"),
                    value.optDouble("sourceAgeSeconds", Double.NaN), now);
            } catch (Exception ignored) { /* Older connectors/errors hide readings, never interrupt video. */ }
            finally { healthPending.set(false); }
            final SockReadings received = result;
            main.post(() -> {
                if (active && !userStopped && viewing == current) { sockReadings = received; updateSockDisplay(); }
            });
        });
    }
    private void updateSockDisplay() {
        if (sockLabel == null) return;
        boolean pip = isInPictureInPictureMode() || enteringPip;
        String text = !pip && sockReadings != null && freshVideo() && !livePolicy.tooFarBehind()
            ? sockReadings.display(SystemClock.elapsedRealtime()) : "";
        sockLabel.setText(text);
        sockLabel.setContentDescription("Owlet sock reference readings. " + text);
        sockLabel.setTextSize(pip ? 9 : 12);
        sockLabel.setVisibility(text.isEmpty() ? View.GONE : View.VISIBLE);
        View controls = findViewById(R.id.video_controls);
        FrameLayout.LayoutParams params = (FrameLayout.LayoutParams) sockLabel.getLayoutParams();
        int bottom = fullscreen && !pip && controls.getVisibility() == View.VISIBLE ? controls.getHeight() + dp(20) : dp(pip ? 4 : 8);
        if (params.bottomMargin != bottom) { params.bottomMargin = bottom; sockLabel.setLayoutParams(params); }
    }
    private void refreshDisplay() {
        if (player != null && AudioRecoveryPolicy.shouldFallback(!muted && !audioInterrupted && player.getPlayWhenReady()
                    && player.getPlaybackSuppressionReason() == Player.PLAYBACK_SUPPRESSION_REASON_NONE,
                audioUnavailable, player.getCurrentTracks().containsType(C.TRACK_TYPE_AUDIO), SystemClock.elapsedRealtime(),
                bufferingSince, lastStreamRead, lastVideoFrame)) {
            audioUnavailable = true; applySound(); updatePipParams();
            Toast.makeText(this, "Trying video only. Tap Video only to retry sound.", Toast.LENGTH_LONG).show();
            recoverPlayback("Trying video only.");
        }
        if (player != null && active && !userStopped && watchdog.stalled(SystemClock.elapsedRealtime(),
                lastVideoFrame, player.isPlaying(), wantsVideo())) {
            recoverPlayback("Video stalled.");
        }
        boolean fresh = freshVideo();
        if (player != null && !directMode) {
            LivePlaybackPolicy.Action action = livePolicy.sample(SystemClock.elapsedRealtime(), player.getCurrentPosition(),
                player.getTotalBufferedDuration(), fresh);
            if (action == LivePlaybackPolicy.Action.RECONNECT) {
                recoverPlayback("Reconnecting to live video."); fresh = false;
            } else {
                float speed = action == LivePlaybackPolicy.Action.CATCH_UP ? LivePlaybackPolicy.CATCH_UP_SPEED : 1f;
                if (playbackSpeed != speed) { playbackSpeed = speed; player.setPlaybackParameters(new PlaybackParameters(speed, 1f)); }
            }
        }
        if (watchdog.attempts() == 0 && fresh) retryCount = 0;
        clock.setVisibility(View.GONE);
        // Public camera builds do not use a personal health service.
        updateSockDisplay();
        if (fresh) {
            clock.setText(VideoDisplay.clock(System.currentTimeMillis(), ZoneId.systemDefault(), isInPictureInPictureMode() || enteringPip));
            status.setText("Live");
        } else if ("Live".contentEquals(status.getText()) || "Catching up…".contentEquals(status.getText())
                || "Delayed video…".contentEquals(status.getText())) status.setText("Waiting for video…");
        // Loader/catch-up state can change while frames still reach the screen.
        // Keep routine recovery messages quiet until frame progress has actually
        // stopped for three seconds, including across a short reconnect.
        boolean recentFrames = lastStatusFrame > 0 && SystemClock.elapsedRealtime() - lastStatusFrame < 3000;
        status.setVisibility(wantsVideo() && recentFrames ? View.GONE : View.VISIBLE);
        if (livePolicy.tooFarBehind()) releaseMovement(false);
        findViewById(R.id.pip).setEnabled(wantsVideo());
        positionClock(); updatePipParams();
    }
    private void scheduleControlsHide() {
        main.removeCallbacks(hideControls);
        if (fullscreen && !movementExpanded && controlsVisible && freshVideo()) main.postDelayed(hideControls, 3500);
    }
    private PictureInPictureParams pipParams() {
        PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9));
        Rect bounds = new Rect();
        View surface = video != null ? video.getVideoSurfaceView() : null;
        if (surface != null && surface.getGlobalVisibleRect(bounds) && !bounds.isEmpty()) builder.setSourceRectHint(bounds);
        PendingIntent stop = PendingIntent.getBroadcast(this, 1, new Intent(PIP_STOP).setPackage(getPackageName()),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent sound = PendingIntent.getBroadcast(this, 2, new Intent(PIP_MUTE).setPackage(getPackageName()),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        builder.setActions(Arrays.asList(
            new RemoteAction(Icon.createWithResource(this, muted || audioUnavailable || audioInterrupted ? android.R.drawable.ic_lock_silent_mode_off : android.R.drawable.ic_lock_silent_mode),
                audioUnavailable || audioInterrupted ? "Try sound" : muted ? "Sound on" : "Mute", audioUnavailable || audioInterrupted ? "Try sound" : muted ? "Sound on" : "Mute", sound),
            new RemoteAction(Icon.createWithResource(this, android.R.drawable.ic_menu_close_clear_cancel), "Stop", "Stop video", stop)));
        if (Build.VERSION.SDK_INT >= 31) builder.setAutoEnterEnabled(wantsVideo() && !isInPictureInPictureMode() && !enteringPip);
        return builder.build();
    }
    private void updatePipParams() {
        if (!pipSupported || video == null || isFinishing()) return;
        try { setPictureInPictureParams(pipParams()); } catch (IllegalArgumentException | IllegalStateException ignored) { }
    }
    private boolean enterPip() {
        if (!pipSupported || !wantsVideo() || isInPictureInPictureMode()) return false;
        AppOpsManager operations = (AppOpsManager) getSystemService(APP_OPS_SERVICE);
        if (operations.checkOpNoThrow(AppOpsManager.OPSTR_PICTURE_IN_PICTURE, android.os.Process.myUid(), getPackageName()) != AppOpsManager.MODE_ALLOWED) {
            Toast.makeText(this, "Allow picture-in-picture for Baby Cam in Android settings.", Toast.LENGTH_LONG).show();
            return false;
        }
        releaseMovement(false); setMovementExpanded(false);
        enteringPip = true; main.removeCallbacks(hideControls);
        applyPresentation();
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        boolean entered = false;
        try { entered = enterPictureInPictureMode(pipParams()); } catch (IllegalArgumentException | IllegalStateException ignored) { }
        if (!entered) { enteringPip = false; applyPresentation(); }
        return entered;
    }
    @Override protected void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (Build.VERSION.SDK_INT < 31) enterPip();
        else if (pipSupported && wantsVideo()) { releaseMovement(false); setMovementExpanded(false); }
    }
    @Override public boolean onPictureInPictureRequested() { return enterPip(); }
    @androidx.annotation.RequiresApi(31)
    @Override public void onPictureInPictureUiStateChanged(PictureInPictureUiState state) {
        super.onPictureInPictureUiStateChanged(state);
        if (Build.VERSION.SDK_INT >= 35 && state.isTransitioningToPip()) {
            setMovementExpanded(false); enteringPip = true; applyPresentation();
        }
    }
    @Override public void onPictureInPictureModeChanged(boolean pip, Configuration configuration) {
        super.onPictureInPictureModeChanged(pip, configuration);
        enteringPip = false; setMovementExpanded(false); controlsVisible = true;
        applyPresentation(); refreshDisplay();
        // Enter/expand reuses the same player and live connection. No reconnect.
    }
    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration); applyPresentation(); updatePipParams();
    }
    private void beginPairing() {
        userStopped = true;
        stopPlayback();
        scan.launch(new ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setCaptureActivity(SecureCaptureActivity.class)
            .setPrompt("Scan the code shown by your Home Connector")
            .setOrientationLocked(false).setBeepEnabled(false).setBarcodeImageEnabled(false));
    }
    private void selectDirect(boolean direct) {
        directMode = direct;
        getSharedPreferences("viewing-preferences", MODE_PRIVATE).edit().putBoolean("directCamera", direct).apply();
        updateConnectionControls();
    }
    private void updateConnectionControls() {
        findViewById(R.id.quality).setEnabled(!directMode);
        findViewById(R.id.move_toggle).setVisibility(directMode ? View.GONE : View.VISIBLE);
        setMovementExpanded(false);
    }
    private void connectionMenu() {
        String[] options = localCamera == null
            ? new String[]{"Set up local camera (no PC)", "Use Windows connector", "Pair Windows connector"}
            : new String[]{"Use saved local camera (no PC)", "Edit local camera", "Use Windows connector", "Pair Windows connector", "Forget local camera"};
        new AlertDialog.Builder(this).setTitle("Connection").setItems(options, (dialog, which) -> {
            String option = options[which];
            if (option.startsWith("Set up") || option.startsWith("Edit")) { localSetup(); return; }
            if (option.startsWith("Pair")) { beginPairing(); return; }
            if (option.startsWith("Forget")) {
                stopByUser(); localStore.clear(); localCamera = null; selectDirect(false); return;
            }
            stopPlayback();
            selectDirect(option.contains("local camera"));
            userStopped = false; retryCount = 0; watchdog.reset();
            if (configured()) connect(); else beginPairing();
        }).setNegativeButton("Cancel", null).show();
    }
    private EditText cameraField(LinearLayout form, String hint, String value, boolean secret) {
        EditText input = new EditText(this);
        input.setHint(hint); input.setSingleLine(true);
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT | (secret
            ? android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD : android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS));
        input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        input.setSaveEnabled(false); input.setText(value);
        form.addView(input); return input;
    }
    private void localSetup() {
        stopByUser();
        LinearLayout form = new LinearLayout(this); form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(20), dp(8), dp(20), dp(8));
        TextView help = new TextView(this);
        help.setText("Use an already-enabled, password-protected RTSP stream on the same Wi-Fi/Ethernet subnet. Enter the stream URL from your camera documentation or ONVIF viewer. No PC is needed after setup. RTSP is not encrypted: use a trusted LAN. Sound depends on the camera codec. Movement and quality selection require the Windows connector.");
        form.addView(help);
        EditText url = cameraField(form, "rtsp://192.168.1.20:554/stream-path", localCamera == null ? "" : localCamera.url, false);
        EditText username = cameraField(form, "Camera username", localCamera == null ? "" : localCamera.username, false);
        EditText password = cameraField(form, "Camera password", localCamera == null ? "" : localCamera.password, true);
        ScrollView scroll = new ScrollView(this); scroll.addView(form);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Local camera · no PC").setView(scroll)
            .setNegativeButton("Cancel", null).setPositiveButton("Save and connect", null).create();
        dialog.setOnShowListener(ignored -> {
            dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
                try {
                    LocalCamera candidate = new LocalCamera(url.getText().toString().trim(), username.getText().toString(), password.getText().toString());
                    localStore.save(candidate); localCamera = candidate; selectDirect(true);
                    userStopped = false; retryCount = 0; watchdog.reset(); audioUnavailable = audioInterrupted = false;
                    dialog.dismiss(); connect();
                } catch (Exception error) {
                    help.setText("Could not save. Use rtsp://PRIVATE-IP:PORT/path without embedded credentials, a username and password. Hostnames and public addresses are not supported.");
                }
            });
        });
        dialog.show();
    }
    private void scheduleNetworkCheck() {
        main.post(() -> {
            main.removeCallbacks(networkCheck);
            if (active && !userStopped) main.postDelayed(networkCheck, 500);
        });
    }
    private final Runnable networkCheck = () -> {
        if (!active || userStopped) return;
        // Default/Wi-Fi notifications also arrive for unrelated routes. Keep
        // the working player (and its buffer) while decoded frames are fresh.
        // Actual loss still uses the existing watchdog/error recovery path.
        if (VideoDisplay.fresh(player != null && player.isPlaying(), SystemClock.elapsedRealtime(), lastVideoFrame)) return;
        reconnect.run();
    };
    private void connect() {
        if (!active || userStopped) return;
        if (directMode) { connectLocal(); return; }
        if (profile == null) { if (family != null) connectFamily(); return; }
        stopPlayback();
        final int expected = generation;
        final Profile selected = profile;
        status.setText("Connecting…");
        routeLabel.setText("Connecting");
        io.execute(() -> {
            boolean identityReady = false;
            try {
                GatewayApi api = new GatewayApi(selected, true);
                identityReady = true;
                GatewayApi.Route route = api.chooseRoute(connectivity);
                store.save(selected);
                main.post(() -> { if (active && expected == generation && !userStopped) play(api, route, expected); });
            } catch (Exception ignored) {
                final boolean renew = !identityReady && family != null && family.gatewayId.equals(selected.gatewayId);
                main.post(() -> {
                    if (active && !userStopped && expected == generation) {
                        if (renew) { profile = null; connectFamily(); return; }
                        routeLabel.setText("Not connected");
                        retryConnection(selected.remoteUrl.isEmpty() ? "Home PC unavailable. Remote access is not configured."
                            : "Home PC unavailable.");
                    }
                });
            }
        });
    }
    private void connectLocal() {
        if (localCamera == null) { status.setText("Open Connection to set up a local camera."); return; }
        stopPlayback();
        final int expected = generation;
        status.setText("Connecting directly to camera…");
        final LocalCameraConnection connection;
        try {
            connection = LocalCameraConnection.select(localCamera, connectivity);
            localConnection = connection;
        } catch (Exception ignored) {
            retryConnection("Join the camera’s Wi-Fi/Ethernet subnet."); return;
        }
        io.execute(() -> {
            try {
                connection.requireAuthentication();
                main.post(() -> {
                    if (!active || userStopped || expected != generation) { connection.close(); return; }
                    routeLabel.setText("Direct local camera · no PC");
                    createPlayer(expected);
                    player.setMediaSource(new RtspMediaSource.Factory().setSocketFactory(connection)
                        .setForceUseRtpTcp(true).setTimeoutMs(8000).setDebugLoggingEnabled(false)
                        .createMediaSource(MediaItem.fromUri(connection.playbackUri())));
                    startPlayer();
                });
            } catch (Exception ignored) {
                connection.close();
                main.post(() -> {
                    if (active && !userStopped && expected == generation)
                        retryConnection("Camera unavailable or RTSP authentication not required. Check Connection settings.");
                });
            }
        });
    }
    private void connectFamily() {
        if (!active || userStopped || family == null || familyInFlight) return;
        stopPlayback();
        familyInFlight = true;
        final FamilyAccess selected = family;
        status.setText("Connecting…");
        routeLabel.setText("Connecting");
        io.execute(() -> {
            try {
                Profile paired = selected.activate(connectivity);
                store.save(paired);
                main.post(() -> {
                    familyInFlight = false;
                    if (active && !userStopped && family == selected && profile == null) {
                        profile = paired;
                        retryCount = 0;
                        connect();
                    }
                });
            } catch (Exception ignored) {
                main.post(() -> {
                    familyInFlight = false;
                    if (active && !userStopped && family == selected && profile == null) {
                        routeLabel.setText("Not connected");
                        retryConnection("App access unavailable. Check the home PC.");
                    }
                });
            }
        });
    }
    private void play(GatewayApi api, GatewayApi.Route route, int expected) {
        routeLabel.setText(route.label + " · " + adaptiveQuality.label());
        createPlayer(expected);
        ProgressiveMediaSource source = gatewaySource(api, route, expected);
        player.setMediaSource(source);
        startPlayer();
    }
    private void createPlayer(int expected) {
        status.setText("Starting live video…");
        player = new ExoPlayer.Builder(this).setLoadControl(new DefaultLoadControl.Builder()
            .setBufferDurationsMs(1250, 2000, 750, 1250).setPrioritizeTimeOverSizeThresholds(true).build()).build();
        watchdog.begin(SystemClock.elapsedRealtime());
        adaptiveQuality.begin(SystemClock.elapsedRealtime());
        livePolicy.begin(SystemClock.elapsedRealtime()); playbackSpeed = 1f;
        bufferingSince = lastStreamRead = 0; applySound();
        player.setVideoFrameMetadataListener((presentation, release, format, mediaFormat) -> {
            if (expected == generation) lastStatusFrame = lastVideoFrame = SystemClock.elapsedRealtime();
        });
        player.addListener(new Player.Listener() {
            @Override public void onPlaybackStateChanged(int state) {
                if (expected != generation) return;
                if (state == Player.STATE_READY) { bufferingSince = 0; status.setText("Live"); scheduleControlsHide(); }
                else if (state == Player.STATE_BUFFERING) {
                    if (bufferingSince == 0) bufferingSince = SystemClock.elapsedRealtime();
                    status.setText("Buffering…"); releaseMovement(false);
                }
                else if (state == Player.STATE_ENDED) main.post(() -> { if (expected == generation) recoverPlayback("Stream ended."); });
            }
            @Override public void onIsPlayingChanged(boolean playing) { if (expected == generation) refreshDisplay(); }
            @Override public void onRenderedFirstFrame() { if (expected == generation) { refreshDisplay(); scheduleControlsHide(); } }
            @Override public void onPlayerError(PlaybackException error) {
                if (expected == generation) main.post(() -> { if (expected == generation) recoverPlayback("Live connection failed."); });
            }
        });
        video.setPlayer(player);
    }
    private ProgressiveMediaSource gatewaySource(GatewayApi api, GatewayApi.Route route, int expected) {
        return new ProgressiveMediaSource.Factory(() -> new GatewayDataSource(api, route, id -> main.post(() -> {
            if (expected != generation) return;
            Viewing current = new Viewing(api, route, id);
            viewing = current;
            io.execute(() -> {
                try {
                    JSONObject capabilities = api.json(route, "/v1/capabilities?view=" + id, null, 5000);
                    boolean supported = capabilities.optBoolean("supported", false);
                    main.post(() -> { if (viewing == current && expected == generation) enablePtz(supported); });
                } catch (Exception ignored) { main.post(() -> { if (viewing == current) enablePtz(false); }); }
            });
        }), (age, receivedAt) -> main.post(() -> {
            if (expected == generation) livePolicy.receiveClock(age, receivedAt);
        }), () -> { if (expected == generation) lastStreamRead = SystemClock.elapsedRealtime(); }))
            .setLoadErrorHandlingPolicy(new DefaultLoadErrorHandlingPolicy(0))
            .createMediaSource(MediaItem.fromUri(route.origin + "/v1/live.mp4?quality=" + adaptiveQuality.quality()));
    }
    private void startPlayer() {
        player.prepare();
        player.play();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
    private void enablePtz(boolean enabled) {
        ptzReady = enabled;
        for (Button button : arrows) button.setEnabled(enabled);
        findViewById(R.id.ptz_stop).setEnabled(enabled);
    }
    private void retryConnection(String message) {
        if (!active || userStopped) return;
        main.removeCallbacks(reconnect);
        int delay = Math.min(30, 2 << retryCount);
        retryCount = Math.min(retryCount + 1, 4);
        status.setText(message + " Retrying in " + delay + "s; Stop cancels.");
        main.postDelayed(reconnect, delay * 1000L);
    }
    private void recoverPlayback(String message) {
        if (!active || userStopped) return;
        if (!"Trying video only.".equals(message)) adaptiveQuality.failure(SystemClock.elapsedRealtime());
        watchdog.recover();
        stopPlayback();
        setMovementExpanded(false);
        retryConnection(message);
    }
    private void pressMovement(String action, View button) {
        boolean live = movementExpanded && !livePolicy.tooFarBehind() && !isInPictureInPictureMode() && !enteringPip && active && ptzReady && viewing != null && player != null && player.isPlaying()
            && SystemClock.elapsedRealtime() - lastVideoFrame <= 1200;
        if (!hold.press(SystemClock.elapsedRealtime(), live)) return;
        direction = action;
        pressed = button;
        button.setPressed(true);
        main.post(renew);
    }
    private void releaseMovement(boolean explicit) {
        boolean wasHeld = hold.release();
        direction = null;
        main.removeCallbacks(renew);
        if (pressed != null) pressed.setPressed(false);
        pressed = null;
        Viewing current = viewing;
        if ((wasHeld || explicit) && current != null) {
            int sequence = current.sequence.incrementAndGet();
            control.execute(() -> {
                try { current.api.json(current.route, "/v1/ptz", new JSONObject().put("view", current.id)
                    .put("action", "stop").put("sequence", sequence), 1000); } catch (Exception ignored) { }
            });
        }
    }
    private void stopPlayback() {
        if (localConnection != null) { localConnection.close(); localConnection = null; }
        sockReadings = null;
        nextHealthPoll = 0;
        sockLabel.setVisibility(View.GONE);
        main.removeCallbacks(networkCheck);
        generation++;
        watchdog.stop();
        livePolicy.begin(SystemClock.elapsedRealtime()); playbackSpeed = 1f;
        main.removeCallbacks(reconnect);
        releaseMovement(false);
        enablePtz(false);
        Viewing old = viewing;
        viewing = null;
        if (old != null) control.execute(() -> {
            try { old.api.json(old.route, "/v1/stop", new JSONObject().put("view", old.id), 1000); } catch (Exception ignored) { }
        });
        if (player != null) { video.setPlayer(null); player.release(); player = null; }
        lastVideoFrame = 0;
        bufferingSince = lastStreamRead = 0;
        clock.setVisibility(View.GONE);
        findViewById(R.id.pip).setEnabled(wantsVideo());
        main.removeCallbacks(hideControls);
        routeLabel.setText("Not connected");
        // A buffering/outage retry is still an open viewer. Keep the full-screen
        // phone awake through recovery, not just while a player happens to exist.
        if (wantsVideo()) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        updatePipParams();
    }
    @Override protected void onStart() {
        super.onStart(); active = true;
        lastStatusFrame = 0;
        main.removeCallbacks(displayTick); main.post(displayTick);
        if (profile == null) { try { profile = store.load(); } catch (Exception ignored) { } }
        lastDefault = connectivity.getActiveNetwork();
        knownWifi.clear();
        for (Network network : connectivity.getAllNetworks()) {
            NetworkCapabilities caps = connectivity.getNetworkCapabilities(network);
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) knownWifi.add(network);
        }
        connectivity.registerDefaultNetworkCallback(networks);
        connectivity.registerNetworkCallback(new NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET).build(), wifiNetworks);
        if (configured() && !userStopped) connect();
    }
    @Override protected void onPause() { releaseMovement(false); super.onPause(); }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // Inspect this activation without replacing the task's original launch
        // intent (PiP expansion can deliver an otherwise empty intent).
        // singleTask launcher taps reuse this Activity. Treat a fresh launch as
        // Start, but preserve an explicit PiP Stop when simply expanding PiP.
        if (Intent.ACTION_MAIN.equals(intent.getAction()) && intent.hasCategory(Intent.CATEGORY_LAUNCHER)
                && !isInPictureInPictureMode() && !enteringPip && userStopped) {
            userStopped = false; retryCount = 0; watchdog.reset(); audioUnavailable = audioInterrupted = false;
            if (active && configured()) connect();
        }
    }
    @Override protected void onResume() {
        super.onResume();
        if (!isInPictureInPictureMode()) {
            enteringPip = false; controlsVisible = true;
            if (fullscreen) setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
            applyPresentation(); scheduleControlsHide();
        }
    }
    @Override protected void onStop() {
        active = false;
        main.removeCallbacks(displayTick); main.removeCallbacks(hideControls);
        setMovementExpanded(false);
        try { connectivity.unregisterNetworkCallback(networks); } catch (Exception ignored) { }
        try { connectivity.unregisterNetworkCallback(wifiNetworks); } catch (Exception ignored) { }
        stopPlayback(); super.onStop();
    }
    @Override public void onWindowFocusChanged(boolean focused) { super.onWindowFocusChanged(focused); if (!focused) releaseMovement(false); }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putBoolean("stopped", userStopped); state.putString("quality", adaptiveQuality.quality()); state.putBoolean("fullscreen", fullscreen);
        state.putString("qualityMode", adaptiveQuality.mode()); state.putLong("qualityProbeDelay", adaptiveQuality.probeDelay());
        state.putFloat("zoom", video.getZoom()); state.putFloat("zoomX", video.getZoomX()); state.putFloat("zoomY", video.getZoomY());
        super.onSaveInstanceState(state);
    }
    @Override protected void onDestroy() {
        if (pipReceiverRegistered) unregisterReceiver(pipReceiver);
        main.removeCallbacksAndMessages(null); io.shutdownNow(); healthIo.shutdownNow(); control.shutdown(); super.onDestroy();
    }
}
