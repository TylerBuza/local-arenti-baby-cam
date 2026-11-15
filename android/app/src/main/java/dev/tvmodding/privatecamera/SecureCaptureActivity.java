package dev.tvmodding.privatecamera;

import android.os.Bundle;
import android.view.WindowManager;
import com.journeyapps.barcodescanner.CaptureActivity;

/** Protect the enrollment token from recent-task thumbnails/screenshots too. */
public final class SecureCaptureActivity extends CaptureActivity {
    @Override protected void onCreate(Bundle state) {
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        super.onCreate(state);
    }
}
