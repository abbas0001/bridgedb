# -*- coding: utf-8 -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft 0xA3ADB67A2CDB8B35 <isis@torproject.org>
#           please also see AUTHORS file
# :copyright: (c) 2013-2017, Isis Lovecruft
#             (c) 2007-2017, The Tor Project, Inc.
#             (c) 2007-2017, all entities within the AUTHORS file
# :license: 3-Clause BSD, see LICENSE for licensing information

"""Unittests for :mod:`bridgedb.crypto`."""

from __future__ import print_function
from __future__ import unicode_literals

import base64
import binascii
import io
import logging
import math
import os
import shutil

import OpenSSL

from twisted import version as _twistedversion
from twisted.internet import defer
from twisted.python.versions import Version
from twisted.trial import unittest
from twisted.test.proto_helpers import StringTransport
from twisted.web.test import test_agent as txtagent

from bridgedb import crypto
from bridgedb import txrecaptcha
from bridgedb.persistent import Conf

from bridgedb.test.util import fileCheckDecorator
from bridgedb.test.email_helpers import _createConfig


logging.disable(50)

SEKRIT_KEY  = b'v\x16Xm\xfc\x1b}\x063\x85\xaa\xa5\xf9\xad\x18\xb2P\x93\xc6k\xf9'
SEKRIT_KEY += b'\x8bI\xd9\xb8xw\xf5\xec\x1b\x7f\xa8'


class DummyEndpoint(object):
    """An endpoint that uses a fake transport."""

    def connect(self, factory):
        """Returns a connection to a
        :api:`twisted.test.proto_helpers.StringTransport`.
        """
        protocol = factory.buildProtocol(None)
        protocol.makeConnection(StringTransport())
        return defer.succeed(protocol)


class GetKeyTests(unittest.TestCase):
    """Tests for :func:`bridgedb.crypto.getKey`."""

    def test_getKey_nokey(self):
        """Test retrieving the secret_key from an empty file."""
        filename = os.path.join(os.getcwd(), 'sekrit')
        key = crypto.getKey(filename)
        self.failUnlessIsInstance(key, bytes,
                                  "key isn't bytes! type=%r" % type(key))

    def test_getKey_tmpfile(self):
        """Test retrieving the secret_key from a new tmpfile."""
        filename = self.mktemp()
        key = crypto.getKey(filename)
        self.failUnlessIsInstance(key, bytes,
                                  "key isn't bytes! type=%r" % type(key))

    def test_getKey_keyexists(self):
        """Write the example key to a file and test reading it back."""
        filename = self.mktemp()
        with open(filename, 'wb') as fh:
            fh.write(SEKRIT_KEY)
            fh.flush()

        key = crypto.getKey(filename)
        self.failUnlessIsInstance(key, bytes,
                                  "key isn't bytes! type=%r" % type(key))
        self.assertEqual(SEKRIT_KEY, key,
                         """The example key and the one read from file differ!
                         key (in hex): %s
                         SEKRIT_KEY (in hex): %s"""
                         % (binascii.hexlify(key).decode('utf-8'), binascii.hexlify(SEKRIT_KEY).decode('utf-8')))


class RemovePKCS1PaddingTests(unittest.TestCase):
    """Unittests for :func:`bridgedb.crypto.removePKCS1Padding`."""

    def setUp(self):
        """This blob *is* actually a correctly formed PKCS#1 padded signature
        on the descriptor::

        @purpose bridge
        router ExhalesPeppier 118.16.116.176 35665 0 0
        or-address [eef2:d52a:cf1b:552f:375d:f8d0:a72b:e794]:35664
        platform Tor 0.2.4.5-alpha on Linux
        protocols Link 1 2 Circuit 1
        published 2014-11-03 21:21:43
        fingerprint FA04 5CFF AB95 BA20 C994 FE28 9B23 583E F80F 34DA
        uptime 10327748
        bandwidth 2247108152 2540209215 1954007088
        extra-info-digest 571BF23D8F24F052483C1333EBAE9B91E4A6F422
        onion-key
        -----BEGIN RSA PUBLIC KEY-----
        MIGJAoGBAK7+a033aUqc97SWFVGFwR3ybQ0jG1HTPtsv2/fUfZPwCaf21ly4zIvH
        9uNhtkcPH2p55X+n5M7OUaQawOzbwL4tSR9SLy9bGuZdWLbhu2GHQWmDkAB7BtHp
        UC+uGTN3jvQXEG2xlzpb+lOVUVNXLhL5kFmAXxL+iwN4TeEv/iCnAgMBAAE=
        -----END RSA PUBLIC KEY-----
        signing-key
        -----BEGIN RSA PUBLIC KEY-----
        MIGJAoGBANxmgJ6S3rBAGcvQu2tWBaHByJxeJkdGbxID2b8cITPaNmcl72e3Kd44
        GGIkoKhkX0SAO+i2U+Q41u/DPEBWLxhpl9GAFJZ10dcT18lL36yaK6FRDOcF9jx9
        0A023/kwXd7QQDWqP7Fso+141bzit6ENvNmE1mvEeIoAR+EpJB1tAgMBAAE=
        -----END RSA PUBLIC KEY-----
        contact Somebody <somebody@example.com>
        ntor-onion-key 0Mfi/Af7zLmdNdrmJyPbZxPJe7TZU/hV4Z865g3g+k4
        reject *:*
        router-signature
        -----BEGIN SIGNATURE-----
        PsGGIP+V9ZXWIHjK943CMAPem3kFbO9kt9rvrPhd64u0f7ytB/qZGaOg1IEWki1I
        f6ZNjrthxicm3vnEUdhpRsyn7MUFiQmqLjBfqdzh0GyfrtU5HHr7CBV3tuhgVhik
        uY1kPNo1C8wkmuy31H3V7NXj+etZuzZN66qL3BiQwa8=
        -----END SIGNATURE-----

        However, for the blob to be valid it would need to be converted from
        base64-decoded bytes to a long, then raised by the power of the public
        exponent within the ASN.1 DER decoded signing-key (mod that key's
        public modulus), then re-converted back into bytes before attempting
        to remove the PKCS#1 padding. (See
        :meth:`bridedb.bridges.Bridge._verifyExtraInfoSignature`.)
        """
        blob = ('PsGGIP+V9ZXWIHjK943CMAPem3kFbO9kt9rvrPhd64u0f7ytB/qZGaOg1IEWk'
                'i1If6ZNjrthxicm3vnEUdhpRsyn7MUFiQmqLjBfqdzh0GyfrtU5HHr7CBV3tu'
                'hgVhikuY1kPNo1C8wkmuy31H3V7NXj+etZuzZN66qL3BiQwa8=')
        self.blob = base64.b64decode(blob)

    def test_crypto_removePKCS1Padding_bad_padding(self):
        """removePKCS1Padding() with a blob with a bad PKCS#1 identifier mark
        should raise PKCS1PaddingError.
        """
        self.assertRaises(crypto.PKCS1PaddingError,
                          crypto.removePKCS1Padding,
                          self.blob)

    def test_crypto_removePKCS1Padding_missing_padding(self):
        """removePKCS1Padding() with a blob with a missing PKCS#1 identifier
        mark should raise PKCS1PaddingError.
        """
        self.assertRaises(crypto.PKCS1PaddingError,
                          crypto.removePKCS1Padding,
                          b'\x99' + self.blob)


class SSLVerifyingContextFactoryTests(unittest.TestCase,
                                      txtagent.FakeReactorAndConnectMixin):
    """Tests for :class:`bridgedb.crypto.SSLVerifyingContextFactory`."""

    _certificateText = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIEdjCCA16gAwIBAgIITcyHZlE/AhQwDQYJKoZIhvcNAQEFBQAwSTELMAkGA1UE\n"
        "BhMCVVMxEzARBgNVBAoTCkdvb2dsZSBJbmMxJTAjBgNVBAMTHEdvb2dsZSBJbnRl\n"
        "cm5ldCBBdXRob3JpdHkgRzIwHhcNMTQwMjEyMTUxMTE2WhcNMTQwNjEyMDAwMDAw\n"
        "WjBoMQswCQYDVQQGEwJVUzETMBEGA1UECAwKQ2FsaWZvcm5pYTEWMBQGA1UEBwwN\n"
        "TW91bnRhaW4gVmlldzETMBEGA1UECgwKR29vZ2xlIEluYzEXMBUGA1UEAwwOd3d3\n"
        "Lmdvb2dsZS5jb20wggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCt3TOf\n"
        "VOf4vfy4IROcEyiFzAJA+B3xkMccwA4anaD6VyGSFglRn5Oht3t+G0Mnu/LMuGba\n"
        "EE6NEBEUEbH8KMlAcVRj58LoFIzulaRCdkVX7JK9R+kU05sggvIl1Q2quaWSjiMQ\n"
        "SpyvKz1I2cmU5Gm4MfW/66M5ZJO323VrV19ydrgAtdbNnvVj85asrSyzwEBNxzNC\n"
        "N6OQtOmTt4I7KLXqkROtTmTFvhAGBsvhG0hJZWhoP1aVsFO+KcE2OaIIxWQ4ckW7\n"
        "BJEgYaXfgHo01LdR55aevGUqLfsdyT+GMZrG9k7eqAw4cq3ML2Y6RiyzskqoQL30\n"
        "3OdYjKTIcU+i3BoFAgMBAAGjggFBMIIBPTAdBgNVHSUEFjAUBggrBgEFBQcDAQYI\n"
        "KwYBBQUHAwIwGQYDVR0RBBIwEIIOd3d3Lmdvb2dsZS5jb20waAYIKwYBBQUHAQEE\n"
        "XDBaMCsGCCsGAQUFBzAChh9odHRwOi8vcGtpLmdvb2dsZS5jb20vR0lBRzIuY3J0\n"
        "MCsGCCsGAQUFBzABhh9odHRwOi8vY2xpZW50czEuZ29vZ2xlLmNvbS9vY3NwMB0G\n"
        "A1UdDgQWBBQN7uQBzGDjvKRna111g9iPPtaXVTAMBgNVHRMBAf8EAjAAMB8GA1Ud\n"
        "IwQYMBaAFErdBhYbvPZotXb1gba7Yhq6WoEvMBcGA1UdIAQQMA4wDAYKKwYBBAHW\n"
        "eQIFATAwBgNVHR8EKTAnMCWgI6Ahhh9odHRwOi8vcGtpLmdvb2dsZS5jb20vR0lB\n"
        "RzIuY3JsMA0GCSqGSIb3DQEBBQUAA4IBAQBrVp/xys2ABQvWPxpVrYaXiaoBXdxu\n"
        "RVVXp5Lyu8IipKqFJli81hOX9eqPG7biYeph9HiKnW31xsXebaVlWWL3NXOh5X83\n"
        "wpzozL0AkxskTMHQknrbIGLtmG67H71aKYyCthHEjawLmYjjvkcF6f9fKdYENM4C\n"
        "skz/yjtlPBQFAuT6J9w0b3qtc42sHNlpgIOdIRQc2YCD0p6jAo+wKjoRuRu3ILKj\n"
        "oCVrOPbDMPN4a2gSmK8Ur0aHuEpcNghg6HJsVSANokIIwQ/r4niqL5yotsangP/5\n"
        "rR97EIYKFz7C6LMy/PIe8xFTIyKMtM59IcpUDIwCLlM9JtNdwN4VpyKy\n"
        "-----END CERTIFICATE-----\n")

    _wildcardcertificate = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIExjCCA66gAwIBAgIRAKy0RV8bAucyAgAAAABnMzYwDQYJKoZIhvcNAQELBQAw\n"
        "QjELMAkGA1UEBhMCVVMxHjAcBgNVBAoTFUdvb2dsZSBUcnVzdCBTZXJ2aWNlczET\n"
        "MBEGA1UEAxMKR1RTIENBIDFPMTAeFw0yMDA1MDUwODMyMjlaFw0yMDA3MjgwODMy\n"
        "MjlaMGUxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpDYWxpZm9ybmlhMRYwFAYDVQQH\n"
        "Ew1Nb3VudGFpbiBWaWV3MRMwEQYDVQQKEwpHb29nbGUgTExDMRQwEgYDVQQDDAsq\n"
        "Lmdvb2dsZS5hdDBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABDuwtcBm/Zy4gj3P\n"
        "aaBHMOHIypfqB7KBhbzJSux7ZQpuWhM4R4anNzOzjjrCyi4R+u2mkjeB/Bw6xzt8\n"
        "ObtSEmWjggJdMIICWTAOBgNVHQ8BAf8EBAMCB4AwEwYDVR0lBAwwCgYIKwYBBQUH\n"
        "AwEwDAYDVR0TAQH/BAIwADAdBgNVHQ4EFgQUOCAX+hzjxyujW0S+sRabV0fAspkw\n"
        "HwYDVR0jBBgwFoAUmNH4bhDrz5vsYJ8YkBug630J/SswZAYIKwYBBQUHAQEEWDBW\n"
        "MCcGCCsGAQUFBzABhhtodHRwOi8vb2NzcC5wa2kuZ29vZy9ndHMxbzEwKwYIKwYB\n"
        "BQUHMAKGH2h0dHA6Ly9wa2kuZ29vZy9nc3IyL0dUUzFPMS5jcnQwIQYDVR0RBBow\n"
        "GIILKi5nb29nbGUuYXSCCWdvb2dsZS5hdDAhBgNVHSAEGjAYMAgGBmeBDAECAjAM\n"
        "BgorBgEEAdZ5AgUDMC8GA1UdHwQoMCYwJKAioCCGHmh0dHA6Ly9jcmwucGtpLmdv\n"
        "b2cvR1RTMU8xLmNybDCCAQUGCisGAQQB1nkCBAIEgfYEgfMA8QB3ALIeBcyLos2K\n"
        "IE6HZvkruYolIGdr2vpw57JJUy3vi5BeAAABceQt+OQAAAQDAEgwRgIhANY9slOG\n"
        "xYI8TgPHZ4QdaJAOkOUf+0OMQb47glFk/MAJAiEAlmNt5gNHqEKOhZAU5FB/PF9v\n"
        "P6sD8oLupRxHiEMTtLIAdgBep3P531bA57U2SH3QSeAyepGaDIShEhKEGHWWgXFF\n"
        "WAAAAXHkLfiiAAAEAwBHMEUCIQDcvmEUZcDHDCvX2cikDl8xTfGcWvKETvQL02qt\n"
        "L/hVlQIgfvzN2SEzT2+VwuzgGbmC8CljAjVwxKMRc+BzlB0yFh4wDQYJKoZIhvcN\n"
        "AQELBQADggEBAHsyJM0yEViV/Vo09mwmP3LAyNhK4hGl3+ueZRr7PE3zkbXA3Sb3\n"
        "JAbdpaQJIGUb5PeiTCB5e/Zem8N6qh4WZOi09LVoVUvkOcCUTSFayRzkyTZK1gT5\n"
        "9d/RWL4BfptK++pdkHWT24SnXw/OfeZBd537LWwhGo4X60RRmPIfvxenUcXlUull\n"
        "ZKYMh5B/GkcHAUf6m79lsR5K82ryplDwHr7IpfPSlJyLZn7Y4n3G9HdFeqUVEw4y\n"
        "J7GslEdOd8C5IaO0xLiw+A7kkUJ16LOYKncmaMnIdhL7ibpQltd8x9VHwM7tEw3g\n"
        "52qHhy4vviLHs2SClrxmkINyRvqd9c/GHcI=\n"
        "-----END CERTIFICATE-----\n")

    _wildcardmultidnscertificate = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIJTzCCCDegAwIBAgIQGoaLDa+bxzQIAAAAAD69lzANBgkqhkiG9w0BAQsFADBC\n"
        "MQswCQYDVQQGEwJVUzEeMBwGA1UEChMVR29vZ2xlIFRydXN0IFNlcnZpY2VzMRMw\n"
        "EQYDVQQDEwpHVFMgQ0EgMU8xMB4XDTIwMDUwNTA4MjIzNVoXDTIwMDcyODA4MjIz\n"
        "NVowZjELMAkGA1UEBhMCVVMxEzARBgNVBAgTCkNhbGlmb3JuaWExFjAUBgNVBAcT\n"
        "DU1vdW50YWluIFZpZXcxEzARBgNVBAoTCkdvb2dsZSBMTEMxFTATBgNVBAMMDCou\n"
        "Z29vZ2xlLmNvbTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABMExBCpkvy8VuVVJ\n"
        "gyrY19Q/NvVPzb+yNS371xwzVriYLDhyEyGXNf45D/qj7qIe9EqkpOylAUvF5+4x\n"
        "NYr5INujggbmMIIG4jAOBgNVHQ8BAf8EBAMCB4AwEwYDVR0lBAwwCgYIKwYBBQUH\n"
        "AwEwDAYDVR0TAQH/BAIwADAdBgNVHQ4EFgQUn9s6xwZqJNPn2S7GBvgewUShDNcw\n"
        "HwYDVR0jBBgwFoAUmNH4bhDrz5vsYJ8YkBug630J/SswZAYIKwYBBQUHAQEEWDBW\n"
        "MCcGCCsGAQUFBzABhhtodHRwOi8vb2NzcC5wa2kuZ29vZy9ndHMxbzEwKwYIKwYB\n"
        "BQUHMAKGH2h0dHA6Ly9wa2kuZ29vZy9nc3IyL0dUUzFPMS5jcnQwggSoBgNVHREE\n"
        "ggSfMIIEm4IMKi5nb29nbGUuY29tgg0qLmFuZHJvaWQuY29tghYqLmFwcGVuZ2lu\n"
        "ZS5nb29nbGUuY29tggkqLmJkbi5kZXaCEiouY2xvdWQuZ29vZ2xlLmNvbYIYKi5j\n"
        "cm93ZHNvdXJjZS5nb29nbGUuY29tggYqLmcuY2+CDiouZ2NwLmd2dDIuY29tghEq\n"
        "LmdjcGNkbi5ndnQxLmNvbYIKKi5nZ3BodC5jboIOKi5na2VjbmFwcHMuY26CFiou\n"
        "Z29vZ2xlLWFuYWx5dGljcy5jb22CCyouZ29vZ2xlLmNhggsqLmdvb2dsZS5jbIIO\n"
        "Ki5nb29nbGUuY28uaW6CDiouZ29vZ2xlLmNvLmpwgg4qLmdvb2dsZS5jby51a4IP\n"
        "Ki5nb29nbGUuY29tLmFygg8qLmdvb2dsZS5jb20uYXWCDyouZ29vZ2xlLmNvbS5i\n"
        "coIPKi5nb29nbGUuY29tLmNvgg8qLmdvb2dsZS5jb20ubXiCDyouZ29vZ2xlLmNv\n"
        "bS50coIPKi5nb29nbGUuY29tLnZuggsqLmdvb2dsZS5kZYILKi5nb29nbGUuZXOC\n"
        "CyouZ29vZ2xlLmZyggsqLmdvb2dsZS5odYILKi5nb29nbGUuaXSCCyouZ29vZ2xl\n"
        "Lm5sggsqLmdvb2dsZS5wbIILKi5nb29nbGUucHSCEiouZ29vZ2xlYWRhcGlzLmNv\n"
        "bYIPKi5nb29nbGVhcGlzLmNughEqLmdvb2dsZWNuYXBwcy5jboIUKi5nb29nbGVj\n"
        "b21tZXJjZS5jb22CESouZ29vZ2xldmlkZW8uY29tggwqLmdzdGF0aWMuY26CDSou\n"
        "Z3N0YXRpYy5jb22CEiouZ3N0YXRpY2NuYXBwcy5jboIKKi5ndnQxLmNvbYIKKi5n\n"
        "dnQyLmNvbYIUKi5tZXRyaWMuZ3N0YXRpYy5jb22CDCoudXJjaGluLmNvbYIQKi51\n"
        "cmwuZ29vZ2xlLmNvbYITKi53ZWFyLmdrZWNuYXBwcy5jboIWKi55b3V0dWJlLW5v\n"
        "Y29va2llLmNvbYINKi55b3V0dWJlLmNvbYIWKi55b3V0dWJlZWR1Y2F0aW9uLmNv\n"
        "bYIRKi55b3V0dWJla2lkcy5jb22CByoueXQuYmWCCyoueXRpbWcuY29tghphbmRy\n"
        "b2lkLmNsaWVudHMuZ29vZ2xlLmNvbYILYW5kcm9pZC5jb22CG2RldmVsb3Blci5h\n"
        "bmRyb2lkLmdvb2dsZS5jboIcZGV2ZWxvcGVycy5hbmRyb2lkLmdvb2dsZS5jboIE\n"
        "Zy5jb4IIZ2dwaHQuY26CDGdrZWNuYXBwcy5jboIGZ29vLmdsghRnb29nbGUtYW5h\n"
        "bHl0aWNzLmNvbYIKZ29vZ2xlLmNvbYIPZ29vZ2xlY25hcHBzLmNughJnb29nbGVj\n"
        "b21tZXJjZS5jb22CGHNvdXJjZS5hbmRyb2lkLmdvb2dsZS5jboIKdXJjaGluLmNv\n"
        "bYIKd3d3Lmdvby5nbIIIeW91dHUuYmWCC3lvdXR1YmUuY29tghR5b3V0dWJlZWR1\n"
        "Y2F0aW9uLmNvbYIPeW91dHViZWtpZHMuY29tggV5dC5iZTAhBgNVHSAEGjAYMAgG\n"
        "BmeBDAECAjAMBgorBgEEAdZ5AgUDMC8GA1UdHwQoMCYwJKAioCCGHmh0dHA6Ly9j\n"
        "cmwucGtpLmdvb2cvR1RTMU8xLmNybDCCAQUGCisGAQQB1nkCBAIEgfYEgfMA8QB3\n"
        "ALIeBcyLos2KIE6HZvkruYolIGdr2vpw57JJUy3vi5BeAAABceQk7HEAAAQDAEgw\n"
        "RgIhAMeaPA4eH4PQ6P80cE7pV9+cA+JzHsfEHXybUpJYTUUqAiEAtt6UgO7o9Bo5\n"
        "T83b8KngOaFoAMEitVL5ckcpZHNtOaQAdgBep3P531bA57U2SH3QSeAyepGaDISh\n"
        "EhKEGHWWgXFFWAAAAXHkJOyPAAAEAwBHMEUCIQDLXewTtt5w1Pykw4iJLVEBNWsU\n"
        "8LazX+MIkUmuOpwJQwIgIXySwReiZv9FbIJ/73ytICnrPGNkD43+PVMge9cJ0gQw\n"
        "DQYJKoZIhvcNAQELBQADggEBAM/x3sGLTHpoan0gd9XdVCG6HsQynQEzLL25kTvN\n"
        "q14pRburIrsdFWhtEETqNJo7IG19PozR6IdJkQqEBYjw6D9rRniikWeski5MhcFD\n"
        "fU3ycQ2I2ISlAd3HelYNbVDJ8zJWerEGk7dyi2O/UmRreXHhi+dry8IgTiSssYNA\n"
        "fht9x4GVvFTT6gap4E1Qy5UFMBGMqXmhXNoH9bYX1oY6N+8xr5tkTSQfV0T1kXKJ\n"
        "WUhnDkwMUnvwuMUEP7elcvp7OgOslSQ5zwjnR3zSDozO072YR+L9SGMICCZz1ha1\n"
        "DMCTA95gzVKezFCaUidRU9UyHOFzltfYDt7HRlp7MwWoPLM=\n"
        "-----END CERTIFICATE-----\n")

    def setUp(self):
        """Create a fake reactor for these tests."""
        self.reactor = self.createReactor()
        self.url = 'https://www.example.com/someresource.html#andatag'

    def test_getHostnameFromURL(self):
        """``getHostnameFromURL()`` should return a hostname from a URI."""
        if _twistedversion >= Version('twisted', 14, 0, 0):
            raise unittest.SkipTest(
                ("The SSLVerifyingContextFactory is no longer necessary in "
                 "Twisted>=14.0.0, because the way in which TLS certificates "
                 "are checked now includes certificate pinning, and the "
                 "SSLVerifyingContextFactory only implemented strict hostname "
                 "checking."))

        agent = txrecaptcha._getAgent(self.reactor, self.url)
        contextFactory = agent._contextFactory
        self.assertRegexpMatches(contextFactory.hostname,
                                 '.*www\.example\.com')

    def test_verifyHostname_mismatching(self):
        """Check that ``verifyHostname()`` returns ``False`` when the
        ``SSLVerifyingContextFactory.hostname`` does not match the one found
        in the level 0 certificate subject CN.
        """
        if _twistedversion >= Version('twisted', 14, 0, 0):
            raise unittest.SkipTest(
                ("The SSLVerifyingContextFactory is no longer necessary in "
                 "Twisted>=14.0.0, because the way in which TLS certificates "
                 "are checked now includes certificate pinning, and the "
                 "SSLVerifyingContextFactory only implemented strict hostname "
                 "checking."))

        agent = txrecaptcha._getAgent(self.reactor, self.url)
        contextFactory = agent._contextFactory
        x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                               self._certificateText)
        conn = DummyEndpoint()
        result = contextFactory.verifyHostname(conn, x509, 0, 0, True)
        self.assertIs(result, False)

    def test_verifyHostname_matching(self):
        """Check that ``verifyHostname()`` returns ``True`` when the
        ``SSLVerifyingContextFactory.hostname`` matches the one found in the
        level 0 certificate subject CN.
        """
        hostname = 'www.google.com'
        url = 'https://' + hostname + '/recaptcha'
        contextFactory = crypto.SSLVerifyingContextFactory(url)
        self.assertEqual(contextFactory.hostname, hostname)

        x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                               self._certificateText)
        conn = DummyEndpoint()
        result = contextFactory.verifyHostname(conn, x509, 0, 0, True)
        self.assertTrue(result)

    def test_verifyHostname_matching_wildcard(self):
        """Check that ``verifyHostname()`` returns ``True`` when the
        ``SSLVerifyingContextFactory.hostname`` matches the one found in the
        level 0 certificate subject SAN. The certificate includes a wildcard
        for the common name & SAN.
        """
        hostname = 'www.google.at'
        url = 'https://' + hostname + '/recaptcha'
        contextFactory = crypto.SSLVerifyingContextFactory(url)
        self.assertEqual(contextFactory.hostname, hostname)

        x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                                self._wildcardcertificate)
        conn = DummyEndpoint()
        result = contextFactory.verifyHostname(conn, x509, 0, 0, True)
        self.assertTrue(result)

    def test_verifyHostname_matching_wildcard_multidns(self):
        """Check that ``verifyHostname()`` returns ``True`` when the
        ``SSLVerifyingContextFactory.hostname`` matches the one found in the
        level 0 certificate subject CN. This specific google.com certificate
        inlcudes numerous SAN's.
        """
        hostname = 'www.google.com'
        url = 'https://' + hostname + '/recaptcha'
        contextFactory = crypto.SSLVerifyingContextFactory(url)
        self.assertEqual(contextFactory.hostname, hostname)

        x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                                self._wildcardmultidnscertificate)
        conn = DummyEndpoint()
        result = contextFactory.verifyHostname(conn, x509, 0, 0, True)
        self.assertTrue(result)

    def test_getContext(self):
        """The context factory's ``getContext()`` method should produce an
        ``OpenSSL.SSL.Context`` object.
        """
        contextFactory = crypto.SSLVerifyingContextFactory(self.url)
        self.assertIsInstance(contextFactory.getContext(),
                              OpenSSL.SSL.Context)
