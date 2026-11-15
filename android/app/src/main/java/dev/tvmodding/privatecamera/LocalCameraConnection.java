package dev.tvmodding.privatecamera;

import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import javax.net.SocketFactory;

/** Every socket is bound to an on-link Wi-Fi/Ethernet network and one camera endpoint. */
final class LocalCameraConnection extends SocketFactory implements AutoCloseable {
    private final LocalCamera camera;
    private final Network network;
    private final Set<Socket> sockets = new HashSet<>();
    private boolean closed;

    LocalCameraConnection(LocalCamera camera, Network network) { this.camera = camera; this.network = network; }

    static LocalCameraConnection select(LocalCamera camera, ConnectivityManager manager) throws IOException {
        byte[] target = InetAddress.getByName(camera.host).getAddress(); // validated numeric IPv4 only
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(network);
            LinkProperties links = manager.getLinkProperties(network);
            if (caps == null || links == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    || !(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) continue;
            for (LinkAddress address : links.getLinkAddresses()) {
                byte[] local = address.getAddress().getAddress();
                if (onLink(local, target, address.getPrefixLength())) return new LocalCameraConnection(camera, network);
            }
        }
        throw new IOException("Connect to the camera’s Wi-Fi or Ethernet subnet");
    }

    static boolean onLink(byte[] local, byte[] target, int prefix) {
        if (local.length != 4 || target.length != 4 || prefix < 1 || prefix > 32) return false;
        for (int bit = 0; bit < prefix; bit++) {
            int mask = 1 << (7 - bit % 8);
            if ((local[bit / 8] & mask) != (target[bit / 8] & mask)) return false;
        }
        return true;
    }

    Uri playbackUri() {
        Uri plain = Uri.parse(camera.url);
        return plain.buildUpon().encodedAuthority(Uri.encode(camera.username) + ":" + Uri.encode(camera.password)
            + "@" + plain.getEncodedAuthority()).build();
    }

    /** Refuse anonymous streams before handing credentials to the RTSP player. */
    void requireAuthentication() throws IOException {
        try (Socket socket = createSocket(camera.host, camera.port)) {
            socket.setSoTimeout(1500);
            socket.getOutputStream().write(("DESCRIBE " + camera.url + " RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            long deadline = android.os.SystemClock.elapsedRealtime() + 5000;
            int tail = 0;
            while (bytes.size() < 16384 && android.os.SystemClock.elapsedRealtime() < deadline) {
                int value = socket.getInputStream().read();
                if (value < 0) break;
                bytes.write(value); tail = (tail << 8) | value;
                if (tail == 0x0d0a0d0a) {
                    String header = bytes.toString(StandardCharsets.US_ASCII.name());
                    if (header.startsWith("RTSP/1.0 401 ") && java.util.regex.Pattern.compile(
                            "(?im)^WWW-Authenticate: *(Basic|Digest) +").matcher(header).find()) return;
                    break;
                }
            }
            throw new IOException("A password-protected RTSP stream is required");
        }
    }

    @Override public Socket createSocket(String host, int port) throws IOException {
        camera.requireTarget(host, port);
        Socket socket = new Socket();
        synchronized (this) {
            if (closed) { socket.close(); throw new IOException("Stopped"); }
            sockets.add(socket);
        }
        try {
            network.bindSocket(socket);
            socket.connect(new InetSocketAddress(InetAddress.getByName(camera.host), camera.port), 4000);
            return socket;
        } catch (IOException error) { socket.close(); throw error; }
    }
    @Override public Socket createSocket(InetAddress host, int port) throws IOException { return createSocket(host.getHostAddress(), port); }
    @Override public Socket createSocket(String host, int port, InetAddress local, int localPort) throws IOException { throw new IOException("Explicit local binding unsupported"); }
    @Override public Socket createSocket(InetAddress host, int port, InetAddress local, int localPort) throws IOException { throw new IOException("Explicit local binding unsupported"); }
    @Override public Socket createSocket() throws IOException { throw new IOException("Unconfined socket refused"); }
    @Override public synchronized void close() {
        closed = true;
        for (Socket socket : sockets) { try { socket.close(); } catch (IOException ignored) { } }
        sockets.clear();
    }
}
