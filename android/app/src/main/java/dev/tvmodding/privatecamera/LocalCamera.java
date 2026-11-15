package dev.tvmodding.privatecamera;

import java.net.URI;

/** Explicit numeric LAN target. No DNS, embedded credentials, or public endpoints. */
final class LocalCamera {
    final String url, username, password, host;
    final int port;

    LocalCamera(String url, String username, String password) throws Exception {
        if (url == null || url.length() > 2048 || !url.matches("[!-~]+"))
            throw new IllegalArgumentException("Enter a credential-free RTSP URL.");
        URI uri = new URI(url);
        if (!"rtsp".equals(uri.getScheme()) || uri.getHost() == null || uri.getRawUserInfo() != null
                || uri.getRawFragment() != null || uri.getPort() == 0 || uri.getPort() < -1 || uri.getPort() > 65535
                || uri.getRawPath().isEmpty())
            throw new IllegalArgumentException("Use rtsp://camera-ip:554/stream-path without credentials.");
        host = uri.getHost();
        if (!privateIpv4(host)) throw new IllegalArgumentException("Use the camera’s private IPv4 address, not a hostname or internet address.");
        if (username == null || password == null || !username.matches("[!-~]{1,128}")
                || username.contains(":") || !password.matches("[ -~]{1,256}"))
            throw new IllegalArgumentException("Enter a camera username and password (printable ASCII; no colon in username).");
        this.url = url;
        this.username = username;
        this.password = password;
        port = uri.getPort() < 0 ? 554 : uri.getPort();
    }

    static boolean privateIpv4(String host) {
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) return false;
        int[] n = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!parts[i].matches("0|[1-9][0-9]{0,2}")) return false;
            n[i] = Integer.parseInt(parts[i]);
            if (n[i] > 255) return false;
        }
        return n[0] == 10 || n[0] == 172 && n[1] >= 16 && n[1] <= 31 || n[0] == 192 && n[1] == 168;
    }

    void requireTarget(String target, int targetPort) throws java.io.IOException {
        if (!host.equals(target) || port != targetPort) throw new java.io.IOException("Camera target changed");
    }
}
