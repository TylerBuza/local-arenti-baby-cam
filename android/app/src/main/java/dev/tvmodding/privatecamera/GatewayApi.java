package dev.tvmodding.privatecamera;

import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder;
import org.bouncycastle.pkcs.jcajce.JcaPKCS10CertificationRequestBuilder;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.Principal;
import java.security.PrivateKey;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.spec.ECGenParameterSpec;
import java.util.Arrays;
import java.util.UUID;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.KeyManager;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509KeyManager;
import javax.security.auth.x500.X500Principal;

final class GatewayApi {
    static final class Route {
        final String origin, label;
        final Network network;
        Route(String origin, Network network, String label) { this.origin = origin; this.network = network; this.label = label; }
    }
    final Profile profile;
    private final SSLContext tls;
    GatewayApi(Profile profile, boolean authenticated) throws Exception {
        this.profile = profile;
        KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
        trust.load(null);
        trust.setCertificateEntry("home", profile.ca());
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init(trust); // Deliberately no system/public CA trust for camera traffic.
        tls = SSLContext.getInstance("TLS");
        tls.init(authenticated ? new KeyManager[]{clientKeys(profile)} : new KeyManager[0], factory.getTrustManagers(), null);
    }
    private static X509KeyManager clientKeys(Profile profile) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        PrivateKey key = (PrivateKey) store.getKey(profile.keyAlias, null);
        if (key == null || profile.clientCert.length() > 4096) throw new IllegalStateException("Pairing key unavailable");
        X509Certificate cert = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(
            new ByteArrayInputStream(profile.clientCert.getBytes(StandardCharsets.US_ASCII)));
        cert.checkValidity();
        cert.verify(profile.ca().getPublicKey());
        X509Certificate[] chain = new X509Certificate[]{cert, profile.ca()};
        return new X509KeyManager() {
            public String[] getClientAliases(String type, Principal[] issuers) { return type.startsWith("EC") ? new String[]{"phone"} : null; }
            public String chooseClientAlias(String[] types, Principal[] issuers, Socket socket) {
                for (String type : types) if (type.startsWith("EC")) return "phone";
                return null;
            }
            public String[] getServerAliases(String type, Principal[] issuers) { return null; }
            public String chooseServerAlias(String type, Principal[] issuers, Socket socket) { return null; }
            public X509Certificate[] getCertificateChain(String alias) { return "phone".equals(alias) ? chain.clone() : null; }
            public PrivateKey getPrivateKey(String alias) { return "phone".equals(alias) ? key : null; }
        };
    }
    HttpsURLConnection open(Route route, String path, int connectTimeout, int readTimeout) throws Exception {
        if (!path.startsWith("/v1/") || path.contains("#") || path.contains("\r") || path.contains("\n"))
            throw new IllegalArgumentException("Invalid API path");
        URL url = new URL(route.origin + path);
        HttpsURLConnection connection = (HttpsURLConnection) (route.network == null ? url.openConnection() : route.network.openConnection(url));
        connection.setSSLSocketFactory(tls.getSocketFactory());
        // Keep platform hostname/SAN verification enabled. Never accept-all.
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(connectTimeout);
        connection.setReadTimeout(readTimeout);
        connection.setUseCaches(false);
        connection.setRequestProperty("X-Arenti-Client", "android-v1");
        connection.setRequestProperty("Cache-Control", "no-store");
        return connection;
    }
    JSONObject json(Route route, String path, JSONObject body, int timeout) throws Exception {
        HttpsURLConnection connection = open(route, path, timeout, timeout);
        try {
            if (body != null) {
                byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
                if (data.length > 8192) throw new IllegalArgumentException("Body too large");
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(data.length);
                try (java.io.OutputStream output = connection.getOutputStream()) { output.write(data); }
            }
            int code = connection.getResponseCode();
            if (code != 200) throw new java.io.IOException("Gateway request rejected: " + code);
            try (InputStream input = connection.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[2048];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    if (bytes.size() + read > 16384) throw new java.io.IOException("Response too large");
                    bytes.write(buffer, 0, read);
                }
                return new JSONObject(bytes.toString("UTF-8"));
            }
        } finally { connection.disconnect(); }
    }
    Route chooseRoute(ConnectivityManager manager) throws Exception {
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities capabilities = manager.getNetworkCapabilities(network);
            if (capabilities == null || !capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue;
            Route route = new Route(profile.localUrl, network, "Home Wi-Fi");
            try {
                JSONObject status = json(route, "/v1/status", null, 1500);
                verifyGateway(status);
                updateRemote(status);
                return route;
            } catch (Exception ignored) { /* Only try the enrolled remote endpoint next. */ }
        }
        if (profile.remoteUrl.isEmpty()) throw new java.io.IOException("Remote access is not configured");
        Route route = new Route(profile.remoteUrl, null, "Remote");
        JSONObject status = json(route, "/v1/status", null, 5000);
        verifyGateway(status);
        return route;
    }
    private void verifyGateway(JSONObject status) throws Exception {
        if (status.getInt("protocol") != 1 || !profile.gatewayId.equals(status.getString("gatewayId")))
            throw new java.security.GeneralSecurityException("Wrong home connector");
    }
    private void updateRemote(JSONObject status) throws Exception {
        String remote = status.optString("remoteUrl", "");
        profile.remoteUrl = remote.isEmpty() ? "" : Profile.origin(remote, false);
    }
    static Profile enroll(String qr, ConnectivityManager manager) throws Exception {
        if (qr == null || qr.length() > 8192) throw new IllegalArgumentException("Invalid pairing QR");
        JSONObject pairing = new JSONObject(qr);
        if (pairing.getInt("version") != 1) throw new IllegalArgumentException("Unsupported pairing version");
        String token = pairing.getString("pairToken");
        if (!token.matches("[A-Za-z0-9_-]{32,128}")) throw new IllegalArgumentException("Invalid enrollment token");
        Profile provisional = new Profile(pairing);
        Network wifi = null;
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(network);
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) { wifi = network; break; }
        }
        if (wifi == null) throw new java.io.IOException("Pair on your home Wi-Fi");
        GatewayApi api = new GatewayApi(provisional, false);
        return issuePhone(provisional, api, new Route(provisional.localUrl, wifi, "Pairing"), "/v1/pair", token);
    }
    static Profile activateFamily(Profile bootstrap, ConnectivityManager manager) throws Exception {
        GatewayApi api = new GatewayApi(bootstrap, true);
        Route route = api.chooseRoute(manager);
        return issuePhone(bootstrap, api, route, "/v1/family/activate", null);
    }
    private static Profile issuePhone(Profile provisional, GatewayApi api, Route route, String path, String token) throws Exception {
        String alias = "private-camera-client-" + UUID.randomUUID().toString().replace("-", "");
        boolean retained = false;
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
            generator.initialize(new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN | KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                // Conscrypt hashes TLS CertificateVerify before requesting raw
                // ECDSA from AndroidKeyStore. NONE authorizes that operation;
                // SHA256 remains the CSR signing algorithm. Key stays opaque.
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_NONE).build());
            KeyPair key = generator.generateKeyPair();
            byte[] request = new JcaPKCS10CertificationRequestBuilder(new X500Principal("CN=Android phone"), key.getPublic())
                .build(new JcaContentSignerBuilder("SHA256withECDSA").build(key.getPrivate())).getEncoded();
            String pem = "-----BEGIN CERTIFICATE REQUEST-----\n" + Base64.encodeToString(request, Base64.NO_WRAP)
                + "\n-----END CERTIFICATE REQUEST-----\n";
            JSONObject body = new JSONObject().put("csr", pem).put("name", android.os.Build.MODEL);
            if (token != null) body.put("token", token);
            JSONObject response = api.json(route, path, body, 8000);
            if (!provisional.gatewayId.equals(response.getString("gatewayId"))) throw new java.security.GeneralSecurityException("Wrong home identity");
            X509Certificate cert = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(
                new ByteArrayInputStream(response.getString("clientCert").getBytes(StandardCharsets.US_ASCII)));
            cert.verify(provisional.ca().getPublicKey());
            cert.checkValidity();
            if (!Arrays.equals(cert.getPublicKey().getEncoded(), key.getPublic().getEncoded()) || cert.getBasicConstraints() >= 0)
                throw new java.security.GeneralSecurityException("Wrong phone certificate");
            JSONObject saved = provisional.json().put("clientCert", response.getString("clientCert")).put("keyAlias", alias);
            Profile profile = new Profile(saved);
            retained = true;
            return profile;
        } finally {
            if (!retained) {
                KeyStore store = KeyStore.getInstance("AndroidKeyStore");
                store.load(null);
                store.deleteEntry(alias);
            }
        }
    }
}
