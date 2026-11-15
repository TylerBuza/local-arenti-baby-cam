package dev.tvmodding.privatecamera;

import org.junit.Test;
import static org.junit.Assert.*;

public class ProfileOriginTest {
    @Test public void acceptsEnrolledHttpsOrigins() throws Exception {
        assertEquals("https://192.168.50.10:7443", Profile.origin("https://192.168.50.10:7443/", true));
        assertEquals("https://camera.example.org:8443", Profile.origin("https://camera.example.org:8443", false));
    }
    @Test public void rejectsCleartextCredentialsPathsAndQueries() {
        for (String value : new String[]{"http://192.168.50.10", "https://user:secret@192.168.50.10",
                "https://192.168.50.10/path", "https://192.168.50.10?token=x", "https://192.168.50.10\n",
                "https://192.168.50.10:70000", "https://192.168.50.10#fragment"}) {
            try { Profile.origin(value, false); fail("Unsafe origin accepted"); } catch (Exception expected) { }
        }
    }
    @Test public void localRouteCannotUsePublicOrDnsHost() {
        for (String value : new String[]{"https://203.0.113.2", "https://camera.example.org", "https://192.168.999.1"}) {
            try { Profile.origin(value, true); fail("Non-local origin accepted"); } catch (Exception expected) { }
        }
    }
}
