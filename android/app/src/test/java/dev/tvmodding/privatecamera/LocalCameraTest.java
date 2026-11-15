package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class LocalCameraTest {
    @Test public void onlyExplicitPrivateCameraTargetsAreAccepted() throws Exception {
        for (String host : new String[]{"10.0.2.2", "172.16.1.20", "172.31.1.20", "192.168.1.20"}) {
            LocalCamera c = new LocalCamera("rtsp://" + host + "/live?stream=0", "viewer", "p:@% word");
            assertEquals(554, c.port);
            c.requireTarget(host, 554);
            assertThrows(java.io.IOException.class, () -> c.requireTarget("10.0.2.3", 554));
            assertThrows(java.io.IOException.class, () -> c.requireTarget(host, 8554));
        }
        for (String url : new String[]{"rtsp://example.com/live", "rtsp://8.8.8.8/live", "rtsp://127.0.0.1/live",
                "rtsp://169.254.1.2/live", "rtsp://172.32.1.2/live", "rtsp://192.168.001.2/live",
                "rtsp://192.168.1.256/live", "rtsp://10.1/live", "rtsp://[::1]/live", "http://10.0.2.2/live",
                "rtsp://u:p@10.0.2.2/live", "rtsp://10.0.2.2:0/live", "rtsp://10.0.2.2:65536/live",
                "rtsp://10.0.2.2", "rtsp://10.0.2.2/live#secret", "rtsp://10.0.2.2/live\r\nAuthorization:x"}) {
            assertThrows(url, Exception.class, () -> new LocalCamera(url, "viewer", "password"));
        }
    }
    @Test public void credentialsAreRequiredAndCannotInjectHeaders() {
        for (String user : new String[]{"", "user:name", "u\r\nx", "ü"})
            assertThrows(Exception.class, () -> new LocalCamera("rtsp://10.0.2.2/live", user, "password"));
        for (String pass : new String[]{"", "p\r\nx", "密碼"})
            assertThrows(Exception.class, () -> new LocalCamera("rtsp://10.0.2.2/live", "viewer", pass));
    }
    @Test public void routeMustBeOnTheSameIpv4Subnet() {
        byte[] local = {10, 0, 2, 15};
        assertTrue(LocalCameraConnection.onLink(local, new byte[]{10, 0, 2, 2}, 24));
        assertFalse(LocalCameraConnection.onLink(local, new byte[]{10, 0, 3, 2}, 24));
        assertFalse(LocalCameraConnection.onLink(local, new byte[]{10, 0, 2, 2}, 32));
        assertFalse(LocalCameraConnection.onLink(local, new byte[]{10, 0, 2, 2}, 0));
        assertFalse(LocalCameraConnection.onLink(new byte[16], new byte[16], 64));
    }
}
