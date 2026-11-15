package dev.tvmodding.privatecamera;

import android.content.Context;
import android.net.ConnectivityManager;
import android.os.SystemClock;
import android.view.View;
import android.widget.TextView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.json.JSONObject;
import java.io.InputStream;
import java.security.KeyStore;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class PublicSetupTest {
    @Test public void genericInstallPairsWithLocalConnectorAndAutoplays() throws Exception {
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("dev.tvmodding.localarentibabycam",context.getPackageName());
        assertNull(FamilyAccess.load(context));
        assertNull(new Profile.Store(context).load());
        try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
            scenario.onActivity(screen -> {
                assertEquals(View.VISIBLE,screen.findViewById(R.id.pair).getVisibility());
                ZoomPlayerView video=screen.findViewById(R.id.video);
                assertNull(video.getPlayer());
            });
        }
        String pairing;
        try(InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("pairing-fixture.json")) {
            pairing=new String(input.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8);
        }
        Profile profile=GatewayApi.enroll(pairing,(ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE));
        KeyStore keys=KeyStore.getInstance("AndroidKeyStore"); keys.load(null);
        assertNull(keys.getKey(profile.keyAlias,null).getEncoded());
        new Profile.Store(context).save(profile);
        GatewayApi api=new GatewayApi(profile,true);
        GatewayApi.Route route=new GatewayApi.Route(profile.remoteUrl,null,"Synthetic");
        try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
            AtomicBoolean ready=new AtomicBoolean();
            long deadline=SystemClock.elapsedRealtime()+30000;
            while(!ready.get() && SystemClock.elapsedRealtime()<deadline) {
                scenario.onActivity(screen -> {
                    ZoomPlayerView video=screen.findViewById(R.id.video);
                    ready.set(video.getPlayer()!=null && video.getPlayer().isPlaying() && video.getPlayer().getCurrentPosition()>1000);
                });
                SystemClock.sleep(100);
            }
            assertTrue("Generic paired viewer did not autoplay synthetic video",ready.get());
            scenario.onActivity(screen -> {
                assertEquals(View.GONE,screen.findViewById(R.id.clock).getVisibility());
                assertEquals(View.GONE,screen.findViewById(R.id.sock_readings).getVisibility());
                screen.findViewById(R.id.stop).performClick();
                assertEquals("Stopped.",((TextView)screen.findViewById(R.id.status)).getText().toString());
            });
        } finally {
            api.json(route,"/v1/fixture/finish",new JSONObject(),3000);
        }
    }
}
