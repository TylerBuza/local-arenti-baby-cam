package dev.tvmodding.privatecamera;

import android.content.Context;
import org.json.JSONObject;

final class LocalCameraStore {
    private final Profile.Store sealed;
    LocalCameraStore(Context context) { sealed = new Profile.Store(context, "local-camera-settings", "local-camera-connection"); }
    void save(LocalCamera camera) throws Exception {
        sealed.saveJson(new JSONObject().put("schema", 1).put("url", camera.url)
            .put("username", camera.username).put("password", camera.password));
    }
    LocalCamera load() throws Exception {
        JSONObject json = sealed.loadJson();
        if (json == null) return null;
        if (json.getInt("schema") != 1) throw new IllegalArgumentException("Unknown camera settings");
        return new LocalCamera(json.getString("url"), json.getString("username"), json.getString("password"));
    }
    void clear() { sealed.clear(); }
}
