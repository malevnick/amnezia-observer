import unittest
from unittest.mock import patch, MagicMock
from detect_ip import detect


class DetectionTests(unittest.TestCase):
    def test_public_interface_needs_no_external_lookup(self):
        with patch('detect_ip.socket.socket') as sock, patch('detect_ip.urllib.request.urlopen') as lookup:
            sock.return_value.__enter__.return_value.getsockname.return_value = ('8.8.4.4', 123)
            self.assertEqual(detect(), '8.8.4.4')
            lookup.assert_not_called()

    def test_nat_uses_https_lookup(self):
        with patch('detect_ip.socket.socket') as sock, patch('detect_ip.urllib.request.urlopen') as lookup:
            sock.return_value.__enter__.return_value.getsockname.return_value = ('10.0.0.2', 123)
            lookup.return_value.__enter__.return_value.read.return_value = b'8.8.4.4\n'
            self.assertEqual(detect(), '8.8.4.4')

    def test_bad_lookup_requests_manual_override(self):
        with patch('detect_ip.socket.socket', side_effect=OSError()), patch('detect_ip.urllib.request.urlopen') as lookup:
            lookup.return_value.__enter__.return_value.read.return_value = b'127.0.0.1'
            with self.assertRaisesRegex(RuntimeError, 'PANEL_IP'):
                detect()
