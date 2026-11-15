package dev.tvmodding.privatecamera;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.net.URI;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class Profile {
    final String gatewayId, localUrl, caDer, clientCert, keyAlias;
    volatile String remoteUrl;
    Profile(JSONObject data) throws Exception {
        gatewayId = data.getString("gatewayId");
        if (!gatewayId.matches("[0-9a-f]{32}")) throw new IllegalArgumentException("Invalid gateway identity");
        localUrl = origin(data.getString("localUrl"), true);
        String remote = data.optString("remoteUrl", "");
        remoteUrl = remote.isEmpty() ? "" : origin(remote, false);
        caDer = data.getString("caDer");
        if (caDer.length() > 4096) throw new IllegalArgumentException("CA too large");
        X509Certificate ca = ca();
        ca.checkValidity();
        if (ca.getBasicConstraints() < 0) throw new IllegalArgumentException("Home CA required");
        clientCert = data.optString("clientCert", "");
        keyAlias = data.optString("keyAlias", "");
        if (!keyAlias.isEmpty() && !keyAlias.matches("private-camera-client-[0-9a-f]{32}"))
            throw new IllegalArgumentException("Invalid key alias");
    }
    X509Certificate ca() throws Exception {
        return (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(
            new ByteArrayInputStream(Base64.decode(caDer, Base64.DEFAULT)));
    }
    JSONObject json() throws Exception {
        return new JSONObject().put("gatewayId", gatewayId).put("localUrl", localUrl)
            .put("remoteUrl", remoteUrl).put("caDer", caDer).put("clientCert", clientCert).put("keyAlias", keyAlias);
    }
    static String origin(String text, boolean local) throws Exception {
        if (text.length() > 512 || !text.matches("[!-~]+")) throw new IllegalArgumentException("Invalid HTTPS origin");
        URI uri = new URI(text);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getRawUserInfo() != null
            || uri.getRawQuery() != null || uri.getRawFragment() != null
            || !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath()))
            || uri.getPort() == 0 || uri.getPort() < -1 || uri.getPort() > 65535)
            throw new IllegalArgumentException("Credential-free HTTPS origin required");
        if (local) {
            String[] parts = uri.getHost().split("\\.");
            if (parts.length != 4) throw new IllegalArgumentException("Local IPv4 origin required");
            int[] octets = new int[4];
            for (int i = 0; i < 4; i++) {
                if (!parts[i].matches("0|[1-9][0-9]{0,2}")) throw new IllegalArgumentException("Invalid address");
                octets[i] = Integer.parseInt(parts[i]);
                if (octets[i] > 255) throw new IllegalArgumentException("Invalid address");
            }
            if (!(octets[0] == 10 || octets[0] == 127 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
                || (octets[0] == 192 && octets[1] == 168))) throw new IllegalArgumentException("Private origin required");
        }
        return text.endsWith("/") ? text.substring(0, text.length() - 1) : text;
    }

    static final class Store {
        private static final String ALIAS = "private-camera-settings";
        private final Context context;
        Store(Context context) { this.context = context.getApplicationContext(); }
        private SecretKey key() throws Exception {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            if (!store.containsAlias(ALIAS)) {
                KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
                generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256).build());
                generator.generateKey();
            }
            return (SecretKey) store.getKey(ALIAS, null);
        }
        void save(Profile profile) throws Exception {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            cipher.updateAAD(new byte[]{1});
            byte[] plain = profile.json().toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
            byte[] sealed = cipher.doFinal(plain), iv = cipher.getIV();
            if (iv.length != 12) throw new IllegalStateException("Unexpected GCM IV");
            byte[] envelope = new byte[13 + sealed.length];
            envelope[0] = 1;
            System.arraycopy(iv, 0, envelope, 1, 12);
            System.arraycopy(sealed, 0, envelope, 13, sealed.length);
            context.getSharedPreferences("private-connection", Context.MODE_PRIVATE).edit()
                .putString("sealed", Base64.encodeToString(envelope, Base64.NO_WRAP)).apply();
        }
        Profile load() throws Exception {
            String saved = context.getSharedPreferences("private-connection", Context.MODE_PRIVATE).getString("sealed", null);
            if (saved == null) return null;
            byte[] envelope = Base64.decode(saved, Base64.DEFAULT);
            if (envelope.length < 30 || envelope.length > 16384 || envelope[0] != 1)
                throw new IllegalArgumentException("Invalid protected profile");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, envelope, 1, 12));
            cipher.updateAAD(new byte[]{1});
            return new Profile(new JSONObject(new String(cipher.doFinal(envelope, 13, envelope.length - 13),
                java.nio.charset.StandardCharsets.UTF_8)));
        }
    }
}
