package dev.tvmodding.privatecamera;

import android.content.Context;
import android.net.ConnectivityManager;
import android.security.keystore.KeyProperties;
import android.security.keystore.KeyProtection;
import android.util.Base64;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileNotFoundException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.UUID;

/** Optional, owner-personalized APK credential. Never a camera or CA private key. */
final class FamilyAccess {
    final String gatewayId;
    private final String encoded;

    FamilyAccess(String data) throws Exception {
        if (data == null || data.length() > 16384) throw new IllegalArgumentException("Invalid family profile");
        JSONObject value = new JSONObject(data);
        if (value.getInt("version") != 1 || !"family-v1".equals(value.getString("mode")) || value.length() != 8)
            throw new IllegalArgumentException("Unsupported family profile");
        Profile profile = new Profile(value);
        if (profile.clientCert.isEmpty() || profile.clientCert.length() > 4096
                || value.getString("clientKey").length() > 2048)
            throw new IllegalArgumentException("Family credential missing");
        gatewayId = profile.gatewayId;
        encoded = data;
    }

    static FamilyAccess load(Context context) throws Exception {
        return null; // Public APKs always pair individually; never import a shared family credential.
    }

    Profile activate(ConnectivityManager manager) throws Exception {
        JSONObject value = new JSONObject(encoded);
        Profile provisional = new Profile(value);
        X509Certificate cert = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(
            new ByteArrayInputStream(provisional.clientCert.getBytes(StandardCharsets.US_ASCII)));
        cert.checkValidity();
        cert.verify(provisional.ca().getPublicKey());
        if (cert.getBasicConstraints() >= 0) throw new java.security.GeneralSecurityException("Client certificate required");
        String pem = value.getString("clientKey");
        if (!pem.startsWith("-----BEGIN PRIVATE KEY-----") || !pem.trim().endsWith("-----END PRIVATE KEY-----"))
            throw new IllegalArgumentException("PKCS8 family key required");
        byte[] keyBytes = Base64.decode(pem.replace("-----BEGIN PRIVATE KEY-----", "")
            .replace("-----END PRIVATE KEY-----", "").replaceAll("\\s", ""), Base64.DEFAULT);
        PrivateKey key;
        try { key = KeyFactory.getInstance("EC").generatePrivate(new PKCS8EncodedKeySpec(keyBytes)); }
        finally { java.util.Arrays.fill(keyBytes, (byte) 0); }
        String alias = "private-camera-client-" + UUID.randomUUID().toString().replace("-", "");
        KeyStore keys = KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        try {
            keys.setEntry(alias, new KeyStore.PrivateKeyEntry(key,
                new java.security.cert.Certificate[]{cert, provisional.ca()}),
                new KeyProtection.Builder(KeyProperties.PURPOSE_SIGN | KeyProperties.PURPOSE_VERIFY)
                    .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_NONE).build());
            Profile bootstrap = new Profile(provisional.json().put("keyAlias", alias));
            // Status/activation use the existing pinned CA and real mTLS, on LAN
            // or the configured remote route. The family key cannot start media.
            return GatewayApi.activateFamily(bootstrap, manager);
        } finally {
            keys.deleteEntry(alias);
        }
    }
}
