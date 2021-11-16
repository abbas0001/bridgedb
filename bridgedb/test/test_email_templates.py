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

"""Unittests for the :mod:`bridgedb.distributors.email.templates` module."""

from __future__ import print_function
from __future__ import unicode_literals

from io import StringIO
from gettext import NullTranslations

from twisted.mail.smtp import Address
from twisted.trial import unittest

from bridgedb.distributors.email import templates


class EmailTemplatesTests(unittest.TestCase):
    """Unittests for :func:`b.e.templates`."""

    def setUp(self):
        self.t = NullTranslations(StringIO('test'))
        self.client = Address('blackhole@torproject.org')
        self.bridgeLines = ['obfs3 1.1.1.1:1111', 'obfs3 2.2.2.2:2222']

    def shouldIncludeCommands(self, text):
        self.assertSubstring('commands', text)

    def shouldIncludeInstructions(self, text):
        self.assertSubstring('Tor Browser', text)

    def shouldIncludeBridges(self, text):
        for line in self.bridgeLines:
            self.assertSubstring(line, text)
        self.assertSubstring('Here is your bridge:', text)

    def shouldIncludeGreeting(self, text):
        self.assertSubstring('This is an automated email', text)

    def shouldIncludeAutomationNotice(self, text):
        self.assertSubstring('automated email', text)

    def test_templates_addCommands(self):
        text = templates.addCommands(self.t)
        self.shouldIncludeCommands(text)

    def test_templates_addGreeting(self):
        text = templates.addGreeting(self.t)
        self.shouldIncludeGreeting(text)

    def test_templates_addHowto(self):
        text = templates.addHowto(self.t)
        self.shouldIncludeInstructions(text)

    def test_templates_addBridgeAnswer(self):
        text = templates.addBridgeAnswer(self.t, self.bridgeLines)
        self.shouldIncludeBridges(text)

    def test_templates_buildAnswerMessage(self):
        text = templates.buildAnswerMessage(self.t, self.client, self.bridgeLines)
        self.shouldIncludeBridges(text)
        self.shouldIncludeAutomationNotice(text)
        self.shouldIncludeCommands(text)

    def test_templates_buildSpamWarning(self):
        text = templates.buildSpamWarning(self.t, self.client)
        self.shouldIncludeGreeting(text)
        self.shouldIncludeAutomationNotice(text)
