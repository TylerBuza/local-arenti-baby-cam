"""Offline discovery, origin confinement and credential-write invariants."""
import unittest
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit

import camera_setup
import lan_tools
from onvif_local import local_url, authenticated_uri


class SetupTests(unittest.TestCase):
    def test_only_private_ipv4_camera_targets(self):
        for host in ('127.0.0.1','8.8.8.8','169.254.1.1','100.64.0.1','::1','camera.example'):
            with self.assertRaises(ValueError):
                lan_tools.private_ipv4(host)
        self.assertEqual(lan_tools.private_ipv4('192.168.50.20'),'192.168.50.20')

    def test_discovery_cannot_redirect_to_another_device_or_external_origin(self):
        packet=b'<r xmlns:d="urn:test"><d:XAddrs>http://192.168.50.20:8000/onvif http://192.168.50.21/onvif https://example.org/ http://u:p@192.168.50.20/</d:XAddrs></r>'
        self.assertEqual(lan_tools.discovery_urls(packet,'192.168.50.20'),['http://192.168.50.20:8000/onvif'])
        self.assertEqual(lan_tools.discovery_urls(b'<!DOCTYPE r><r/>','192.168.50.20'),[])
        self.assertEqual(lan_tools.discovery_urls(b'x'*8193,'192.168.50.20'),[])

    def test_native_discovery_is_only_a_candidate_not_a_saved_credential(self):
        self.assertEqual(lan_tools.discovery_urls(b'{"sn":"synthetic-device"}\x00','192.168.50.20'),
            ['http://192.168.50.20:8000/onvif/device_service'])
        self.assertEqual(lan_tools.discovery_urls(b'{}','192.168.50.20'),[])

    def test_camera_urls_are_pinned_and_credentials_are_encoded(self):
        with lan_tools.setup_host('192.168.50.20'):
            for bad in ('http://192.168.50.21/onvif','http://example.org/','http://u:p@192.168.50.20/',
                    'http://192.168.50.20:70000/','http://192.168.50.20/\r\n'):
                with self.assertRaises(ValueError):
                    local_url(bad)
            uri=authenticated_uri('rtsp://192.168.50.20:8554/live','a@b','x:y/z')
            self.assertEqual(urlsplit(uri).hostname,'192.168.50.20')
            self.assertIn('a%40b:x%3Ay%2Fz@',uri)
        self.assertIsNone(lan_tools._setup_host.get())

    def verification(self, anonymous=401, frames=20):
        client=MagicMock()
        client.stream_uri.return_value=('rtsp://192.168.50.20:8554/live',1)
        return client, patch.multiple(camera_setup, OnvifClient=MagicMock(return_value=client),
            device_identity=MagicMock(return_value='synthetic-hash'), anonymous_rtsp_status=MagicMock(return_value=anonymous),
            validate_stream=MagicMock(return_value={'videoFrames':frames}))

    def test_authenticated_setup_saves_only_after_video_and_anonymous_denial(self):
        client, mocks=self.verification()
        with mocks, patch.object(camera_setup.protected_store,'save') as save:
            camera_setup.verify_and_save('http://192.168.50.20:8000/onvif','admin','synthetic-password')
            save.assert_called_once()
            self.assertEqual(save.call_args.args[1]['state'],'verified')
            self.assertEqual(save.call_args.args[1]['cameraSha256'],'synthetic-hash')
            client.close.assert_called_once()

    def test_open_camera_or_missing_frames_never_overwrites_credentials(self):
        for status,frames in ((200,20),(None,20),(401,0)):
            client,mocks=self.verification(status,frames)
            with mocks, patch.object(camera_setup.protected_store,'save') as save:
                with self.assertRaises(ValueError):
                    camera_setup.verify_and_save('http://192.168.50.20:8000/onvif','admin','synthetic-password')
                save.assert_not_called()
                client.close.assert_called_once()
        self.assertIsNone(lan_tools._setup_host.get())

    def test_defaults_do_not_start_discovery_or_network_calls(self):
        with patch.object(lan_tools,'interfaces',return_value=[('192.168.50.10', __import__('ipaddress').ip_network('192.168.50.0/24'))]), \
                patch.object(lan_tools,'camera_host',return_value='192.168.50.20'), patch.object(lan_tools.socket,'socket') as sock:
            self.assertEqual(lan_tools.connector_origin(),'https://192.168.50.10:7443')
            sock.assert_not_called()


if __name__=='__main__': unittest.main()
