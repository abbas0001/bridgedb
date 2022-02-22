# -*- coding: utf-8 -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft 0xA3ADB67A2CDB8B35 <isis@torproject.org>
#           please also see AUTHORS file
# :copyright: (c) 2013, Isis Lovecruft
#             (c) 2007-2013, The Tor Project, Inc.
#             (c) 2007-2013, all entities within the AUTHORS file
# :license: 3-Clause BSD, see LICENSE for licensing information

"""Unittests for the :mod:`bridgedb.distributors.email.dkim` module."""

import email
import email.message
import io

from twisted.trial import unittest

from bridgedb.distributors.email import dkim


class CheckDKIMTests(unittest.TestCase):
    """Tests for :func:`email.server.checkDKIM`."""

    def setUp(self):
        """Create fake email, distributor, and associated context data."""
        self.goodMessage = ["""\
From: user@gmail.com
To: bridges@localhost
X-DKIM-Authentication-Results: pass
Subject: testing

get bridges
""",
"""\
From: user@gmail.com
To: bridges@localhost
Authentication-Results: gmail.com;
	dkim=pass (1024-bit key; secure) header.d=gmail.com header.i=@gmail.com header.a=rsa-sha256 header.s=squak header.b=ZFZSqaMU;
	dkim-atps=neutral
Subject: testing

get bridges
"""]
        self.badMessage = ["""\
From: user@gmail.com
To: bridges@localhost
Subject: testing

get bridges
""",
"""\
From: user@gmail.com
To: bridges@localhost
Subject: testing
Authentication-Results: gmail.com; dkim=none; dkim-atps=neutral

get bridges
""",
"""\
From: user@gmail.com
To: bridges@localhost
Subject: testing
Authentication-Results: gmail.com;
	dkim=pass (1024-bit key; secure) header.d=gmail.com header.i=@gmail.com header.a=rsa-sha256 header.s=squak header.b=ZFZSqaMU;
	dkim-atps=neutral
Authentication-Results: gmail.com; dkim=none; dkim-atps=neutral

get bridges
"""]
        self.domainRules = {
            'gmail.com': ["ignore_dots", "dkim"],
            'example.com': [],
            'localhost': [],
        }

    def _createMessage(self, messageString):
        """Create an ``email.message.Message`` from a string."""
        return email.message_from_string(messageString)

    def test_checkDKIM_good(self):
        for msg in self.goodMessage:
            message = self._createMessage(msg)
            result = dkim.checkDKIM(message,
                                    self.domainRules.get("gmail.com"))
            self.assertTrue(result, msg="not good dkim for: %s" % msg)
                                         

    def test_checkDKIM_bad(self):
        for msg in self.badMessage:
            message = self._createMessage(msg)
            result = dkim.checkDKIM(message,
                                    self.domainRules.get("gmail.com"))
            self.assertIs(result, False, msg="not expected good dkim for: %s" % msg)

    def test_checkDKIM_dunno(self):
        """A ``X-DKIM-Authentication-Results: dunno`` header should return
        False.
        """
        messageList = self.badMessage[0].split('\n')
        messageList[2] = "X-DKIM-Authentication-Results: dunno"
        message = self._createMessage('\n'.join(messageList))
        result = dkim.checkDKIM(message,
                                self.domainRules.get("gmail.com"))
        self.assertIs(result, False)

    def test_checkDKIM_good_dunno(self):
        """A good DKIM verification header, *plus* an
        ``X-DKIM-Authentication-Results: dunno`` header should return False.
        """
        messageList = self.badMessage[0].split('\n')
        messageList.insert(2, "X-DKIM-Authentication-Results: dunno")
        message = self._createMessage('\n'.join(messageList))
        result = dkim.checkDKIM(message,
                                self.domainRules.get("gmail.com"))
        self.assertIs(result, False)
