package dev.tvmodding.privatecamera;

import android.net.Uri;
import android.os.SystemClock;
import androidx.media3.common.C;
import androidx.media3.datasource.BaseDataSource;
import androidx.media3.datasource.DataSpec;
import java.io.IOException;
import java.io.InputStream;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import javax.net.ssl.HttpsURLConnection;

@androidx.annotation.OptIn(markerClass = androidx.media3.common.util.UnstableApi.class)
final class GatewayDataSource extends BaseDataSource {
    interface SessionListener { void onSession(String id); }
    interface TimingListener { void onClock(String ageMillis, long receivedAt); }
    private final GatewayApi api;
    private final GatewayApi.Route route;
    private final SessionListener listener;
    private final TimingListener timing;
    private final Runnable progress;
    private HttpsURLConnection connection;
    private InputStream input;
    private Uri uri;
    private boolean opened;
    GatewayDataSource(GatewayApi api, GatewayApi.Route route, SessionListener listener) {
        this(api, route, listener, (age, receivedAt) -> { });
    }
    GatewayDataSource(GatewayApi api, GatewayApi.Route route, SessionListener listener, TimingListener timing) {
        this(api, route, listener, timing, () -> { });
    }
    GatewayDataSource(GatewayApi api, GatewayApi.Route route, SessionListener listener, TimingListener timing, Runnable progress) {
        super(true);
        this.api = api; this.route = route; this.listener = listener;
        this.timing = timing;
        this.progress = progress;
    }
    @Override public long open(DataSpec spec) throws IOException {
        transferInitializing(spec);
        try {
            if (spec.position != 0 || !(spec.uri.toString().equals(route.origin + "/v1/live.mp4?quality=high")
                    || spec.uri.toString().equals(route.origin + "/v1/live.mp4?quality=low")))
                throw new IOException("Live stream is not seekable");
            uri = spec.uri;
            connection = api.open(route, "/v1/live.mp4?quality=" + (uri.getQueryParameter("quality").equals("low") ? "low" : "high"), 5000, 15000);
            if (connection.getResponseCode() != 200) throw new IOException("Live stream unavailable");
            long receivedAt = SystemClock.elapsedRealtime();
            String clockAge = connection.getHeaderField("X-Stream-Clock-Age-Ms");
            String id = connection.getHeaderField("X-View-Session");
            if (id == null || !id.matches("[0-9a-f]{32}")) throw new IOException("Missing viewing session");
            input = connection.getInputStream();
            opened = true;
            transferStarted(spec);
            listener.onSession(id);
            timing.onClock(clockAge, receivedAt); // Optional; older connectors remain compatible.
            return C.LENGTH_UNSET;
        } catch (Exception ignored) {
            close();
            throw new IOException("Could not open verified home stream");
        }
    }
    @Override public int read(byte[] buffer, int offset, int length) throws IOException {
        if (length == 0) return 0;
        InputStream current = input;
        if (current == null) throw new IOException("Stream closed");
        int count = current.read(buffer, offset, length);
        if (count == -1) return C.RESULT_END_OF_INPUT;
        if (count > 0) progress.run();
        bytesTransferred(count);
        return count;
    }
    @Override public Uri getUri() { return uri; }
    @Override public Map<String, List<String>> getResponseHeaders() {
        return connection == null ? Collections.emptyMap() : connection.getHeaderFields();
    }
    @Override public void close() {
        try { if (input != null) input.close(); } catch (IOException ignored) { }
        if (connection != null) connection.disconnect();
        input = null; connection = null; uri = null;
        if (opened) { opened = false; transferEnded(); }
    }
}
