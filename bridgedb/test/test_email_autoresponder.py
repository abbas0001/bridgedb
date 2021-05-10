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

"""Unittests for the :mod:`bridgedb.distributors.email.autoresponder` module."""

from __future__ import print_function

import io
import os
import shutil

from twisted.internet import defer
from twisted.mail.smtp import Address
from twisted.python.failure import Failure
from twisted.trial import unittest
from twisted.test import proto_helpers

from bridgedb.distributors.email import autoresponder
from bridgedb.distributors.email.server import SMTPMessage
from bridgedb.distributors.email.distributor import TooSoonEmail

from bridgedb.test.email_helpers import _createConfig
from bridgedb.test.email_helpers import _createMailServerContext
from bridgedb.test.email_helpers import DummyEmailDistributorWithState


class CreateResponseBodyTests(unittest.TestCase):
    """Tests for :func:`bridgedb.distributors.email.autoresponder.createResponseBody`."""

    def setUp(self):
        """Create fake email, distributor, and associated context data."""
        self.toAddress = "user@example.com"
        self.config = _createConfig()
        self.ctx = _createMailServerContext(self.config)
        self.distributor = self.ctx.distributor

    def _getIncomingLines(self, clientAddress="user@example.com"):
        """Generate the lines of an incoming email from **clientAddress**."""
        self.toAddress = Address(clientAddress)
        lines = [
            "From: %s" % clientAddress,
            "To: bridges@localhost",
            "Subject: testing",
            "",
            "get bridges",
        ]
        return lines

    def test_createResponseBody_bridges_invalid(self):
        """An invalid request for 'transport obfs3' should still return
        bridges."""
        lines = self._getIncomingLines("testing@localhost")
        lines[4] = "transport obfs3"
        ret, qrcode = autoresponder.createResponseBody(lines, self.ctx, self.toAddress)
        self.assertSubstring("Here are your bridges:", ret)
        self.assertIsNotNone(qrcode)

    def test_createResponseBody_bridges_obfs3(self):
        """A request for 'get transport obfs3' should receive a response."""
        lines = self._getIncomingLines("testing@localhost")
        lines[4] = "get transport obfs3"
        ret, qrcode = autoresponder.createResponseBody(lines, self.ctx, self.toAddress)
        self.assertSubstring("Here are your bridges", ret)
        self.assertSubstring("obfs3", ret)
        self.assertIsInstance(qrcode, bytes)

    def test_createResponseBody_bridges_obfsobfswebz(self):
        """We should only pay attention to the *last* in a crazy request."""
        lines = self._getIncomingLines("testing@localhost")
        lines[4] = "get unblocked webz"
        lines.append("get transport obfs2")
        lines.append("get transport obfs3")
        ret, qrcode = autoresponder.createResponseBody(lines, self.ctx, self.toAddress)
        self.assertSubstring("Here are your bridges", ret)
        self.assertSubstring("obfs3", ret)
        self.assertIsInstance(qrcode, bytes)

    def test_createResponseBody_bridges_obfsobfswebzipv6(self):
        """We should *still* only pay attention to the *last* request."""
        lines = self._getIncomingLines("testing@localhost")
        lines[4] = "transport obfs3"
        lines.append("get unblocked webz")
        lines.append("get ipv6")
        lines.append("get transport obfs2")
        ret, qrcode = autoresponder.createResponseBody(lines, self.ctx, self.toAddress)
        self.assertSubstring("Here are your bridges", ret)
        self.assertSubstring("obfs2", ret)
        self.assertIsInstance(qrcode, bytes)

    def test_createResponseBody_two_requests_TooSoonEmail(self):
        """The same client making two requests in a row should receive a
        rate-limit warning for the second response.
        """
        # Set up a mock distributor which keeps state:
        dist = DummyEmailDistributorWithState()
        ctx = _createMailServerContext(self.config, dist)

        lines = self._getIncomingLines("testing@localhost")
        first, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertSubstring("Here are your bridges", first)
        self.assertIsInstance(qrcode, bytes)
        second, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertSubstring("Please slow down", second)
        self.assertIsNone(qrcode)

    def test_createResponseBody_three_requests_TooSoonEmail(self):
        """Alice making a request, next Bob making a request, and then Alice again,
        should result in both of their first requests getting them bridges,
        and then Alice's second request gets her a rate-limit warning email.
        """
        # Set up a mock distributor which keeps state:
        dist = DummyEmailDistributorWithState()
        ctx = _createMailServerContext(self.config, dist)

        aliceLines = self._getIncomingLines("alice@localhost")
        aliceFirst, qrcode = autoresponder.createResponseBody(aliceLines, ctx,
                                                              self.toAddress)
        self.assertSubstring("Here are your bridges", aliceFirst)
        self.assertIsInstance(qrcode, bytes)

        bobLines = self._getIncomingLines("bob@localhost")
        bobFirst, qrcode = autoresponder.createResponseBody(bobLines, ctx,
                                                            self.toAddress)
        self.assertSubstring("Here are your bridges", bobFirst)
        self.assertIsInstance(qrcode, bytes)

        aliceSecond, qrcode  = autoresponder.createResponseBody(aliceLines, ctx,
                                                                self.toAddress)
        self.assertSubstring("Please slow down", aliceSecond)
        self.assertIsNone(qrcode)

    def test_createResponseBody_three_requests_IgnoreEmail(self):
        """The same client making three requests in a row should receive a
        rate-limit warning for the second response, and then nothing for every
        request thereafter.
        """
        # Set up a mock distributor which keeps state:
        dist = DummyEmailDistributorWithState()
        ctx = _createMailServerContext(self.config, dist)

        lines = self._getIncomingLines("testing@localhost")
        first, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertSubstring("Here are your bridges", first)
        self.assertIsInstance(qrcode, bytes)
        second, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertSubstring("Please slow down", second)
        self.assertIsNone(qrcode)
        third, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertIsNone(third)
        self.assertIsNone(qrcode)
        fourth, qrcode = autoresponder.createResponseBody(lines, ctx, self.toAddress)
        self.assertIsNone(fourth)
        self.assertIsNone(qrcode)


class SMTPAutoresponderTests(unittest.TestCase):
    """Unittests for :class:`bridgedb.distributors.email.autoresponder.SMTPAutoresponder`."""

    timeout = 10

    def setUp(self):
        self.config = _createConfig()
        self.context = _createMailServerContext(self.config)
        self.message = SMTPMessage(self.context)

    def _getIncomingLines(self, clientAddress="user@example.com"):
        """Generate the lines of an incoming email from **clientAddress**."""
        lines = [
            "From: %s" % clientAddress,
            "To: bridges@localhost",
            "Subject: testing",
            "",
            "get bridges",
        ]
        self.message.lines = lines

    def _setUpResponder(self):
        """Set up the incoming message of our autoresponder.

        This is necessary because normally our SMTP server acts as a line
        protocol, waiting for an EOM which sets off a chain of deferreds
        resulting in the autoresponder sending out the response. This should
        be called after :meth:`_getIncomingLines` so that we can hook into the
        SMTP protocol without actually triggering all the deferreds.
        """
        self.message.message = self.message.getIncomingMessage()
        self.responder = self.message.responder
        # The following are needed to provide client disconnection methods for
        # the call to ``twisted.mail.smtp.SMTPClient.sendError`` in
        # ``bridgedb.distributors.email.autoresponder.SMTPAutoresponder.sendError``:
        #protocol = proto_helpers.AccumulatingProtocol()
        #transport = proto_helpers.StringTransportWithDisconnection()
        self.tr = proto_helpers.StringTransportWithDisconnection()
        # Set the transport's protocol, because
        # StringTransportWithDisconnection is a bit janky:
        self.tr.protocol = self.responder
        self.responder.makeConnection(self.tr)

    def test_SMTPAutoresponder_getMailFrom_notbridgedb_at_yikezors_dot_net(self):
        """SMTPAutoresponder.getMailFrom() for an incoming email sent to any email
        address other than the one we're listening for should return our
        configured address, not the one in the incoming email.
        """
        self._getIncomingLines()
        self.message.lines[1] = 'To: notbridgedb@yikezors.net'
        self._setUpResponder()
        recipient = str(self.responder.getMailFrom())
        self.assertEqual(recipient, self.context.fromAddr)

    def test_SMTPAutoresponder_getMailFrom_givemebridges_at_seriously(self):
        """SMTPAutoresponder.getMailFrom() for an incoming email sent to any email
        address other than the one we're listening for should return our
        configured address, not the one in the incoming email.
        """
        self._getIncomingLines()
        self.message.lines[1] = 'To: givemebridges@serious.ly'
        self._setUpResponder()
        recipient = str(self.responder.getMailFrom())
        self.assertEqual(recipient, self.context.fromAddr)

    def test_SMTPAutoresponder_getMailFrom_bad_address(self):
        """SMTPAutoresponder.getMailFrom() for an incoming email sent to a malformed
        email address should log an smtp.AddressError and then return our
        configured email address.
        """
        self._getIncomingLines()
        self.message.lines[1] = 'To: ><@><<<>>.foo'
        self._setUpResponder()
        recipient = str(self.responder.getMailFrom())
        self.assertEqual(recipient, self.context.fromAddr)

    def test_SMTPAutoresponder_getMailFrom_plus_address(self):
        """SMTPAutoresponder.getMailFrom() for an incoming email sent with a valid
        plus address should respond.
        """
        self._getIncomingLines()
        ours = Address(self.context.fromAddr)
        plus = '@'.join([ours.local.decode('utf-8') + '+zh_cn', ours.domain.decode('utf-8')])
        self.message.lines[1] = 'To: {0}'.format(plus)
        self._setUpResponder()
        recipient = str(self.responder.getMailFrom())
        self.assertEqual(recipient, plus)

    def test_SMTPAutoresponder_getMailFrom_getbridges_at_localhost(self):
        """SMTPAutoresponder.getMailFrom() for an incoming email sent with
        'getbridges+zh_cn@localhost' should be responded to from the default
        address.
        """
        self._getIncomingLines()
        ours = Address(self.context.fromAddr)
        plus = '@'.join(['get' + ours.local.decode('utf-8') + '+zh_cn', ours.domain.decode('utf-8')])
        self.message.lines[1] = 'To: {0}'.format(plus)
        self._setUpResponder()
        recipient = str(self.responder.getMailFrom())
        self.assertEqual(recipient, self.context.fromAddr)

    def test_SMTPAutoresponder_getMailTo_UnsupportedDomain(self):
        """getMailTo() should catch emails from UnsupportedDomains."""
        emailFrom = 'some.dude@un.support.ed'
        self._getIncomingLines(emailFrom)
        self._setUpResponder()
        clients = self.responder.getMailTo()
        self.assertIsInstance(clients, list, (
            "Returned value of SMTPAutoresponder.getMailTo() isn't a list! "
            "Type: %s" % type(clients)))
        self.assertTrue(emailFrom not in clients)
        # The client was from an unsupported domain; they shouldn't be in the
        # clients list:
        self.assertEqual(len(clients), 0,
                         "clients = %s" % repr(clients))

    def test_SMTPAutoresponder_reply_noFrom(self):
        """A received email without a "From:" or "Sender:" header shouldn't
        receive a response.
        """
        self._getIncomingLines()
        self.message.lines[0] = ""
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_badAddress(self):
        """Don't respond to RFC2822 malformed source addresses."""
        self._getIncomingLines("testing*.?\"@example.com")
        self._setUpResponder()
        ret = self.responder.reply()
        # This will call ``self.responder.reply()``:
        #ret = self.responder.incoming.eomReceived()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_anotherBadAddress(self):
        """Don't respond to RFC2822 malformed source addresses."""
        self._getIncomingLines("Mallory <>>@example.com")
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_invalidDomain(self):
        """Don't respond to RFC2822 malformed source addresses."""
        self._getIncomingLines("testing@exa#mple.com")
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_anotherInvalidDomain(self):
        """Don't respond to RFC2822 malformed source addresses."""
        self._getIncomingLines("testing@exam+ple.com")
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_DKIM_badDKIMheader(self):
        """An email with an 'X-DKIM-Authentication-Result:' header appended
        after the body should not receive a response.
        """
        self._getIncomingLines("testing@gmail.com")
        self.message.lines.append("X-DKIM-Authentication-Result: ")
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_goodDKIMheader(self):
        """An email with a good DKIM header should be responded to."""
        self._getIncomingLines("testing@gmail.com")
        self.message.lines.insert(3, "X-DKIM-Authentication-Result: pass")
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)

    def test_SMTPAutoresponder_reply_transport_invalid(self):
        """An invalid request for 'transport obfs3' should get help text."""
        #self.skip = True
        #raise unittest.SkipTest("We need to fake the reactor for this one")

        def cb(success):
            pass
        self._getIncomingLines("testing@example.com")
        self.message.lines[4] = "transport obfs3"
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)
        #self.assertSubstring("COMMANDs", ret)
        print(self.tr.value())
        return ret

    def test_SMTPAutoresponder_reply_transport_valid(self):
        """An valid request for 'get transport obfs3' should get obfs3."""
        #self.skip = True
        #raise unittest.SkipTest("We need to fake the reactor for this one")
    
        self._getIncomingLines("testing@example.com")
        self.message.lines[4] = "transport obfs3"
        self._setUpResponder()
        ret = self.responder.reply()
        self.assertIsInstance(ret, defer.Deferred)
        #self.assertSubstring("obfs3", ret)
        print(self.tr.value())
        return ret

    def test_SMTPAutoresponder_sentMail(self):
        """``SMTPAutoresponder.sendMail()`` should handle successes from an
        :api:`twisted.mail.smtp.SMTPSenderFactory`.
        """
        success = (1, [('me@myaddress.com', 250, 'OK',)])
        self._getIncomingLines()
        self._setUpResponder()
        self.responder.sentMail(success)

    def test_SMTPAutoresponder_sendError_fail(self):
        """``SMTPAutoresponder.sendError()`` should handle failures."""
        fail = Failure(ValueError('This failure was sent on purpose.'))
        self._getIncomingLines()
        self._setUpResponder()
        self.responder.sendError(fail)

    def test_SMTPAutoresponder_sendError_exception(self):
        """``SMTPAutoresponder.sendError()`` should handle exceptions."""
        error = ValueError('This error was sent on purpose.')
        self._getIncomingLines()
        self._setUpResponder()
        self.responder.sendError(error)

    def test_SMTPAutoresponder_runChecks_RCPTTO_From_mismatched_domain(self):
        """runChecks() should catch emails where the SMTP 'MAIL FROM:' command
        reported being from an email address at one supported domain and the
        email's 'From:' header reported another domain.
        """
        smtpFrom = 'not.an.evil.bot@riseup.net'
        emailFrom = Address('not.an.evil.bot@gmail.com')
        self._getIncomingLines(str(emailFrom))
        self._setUpResponder()
        self.responder.incoming.canonicalFromSMTP = smtpFrom
        self.assertFalse(self.responder.runChecks(emailFrom))

    def test_SMTPAutoresponder_runChecks_RCPTTO_From_mismatched_username(self):
        """runChecks() should catch emails where the SMTP 'MAIL FROM:' command
        reported being from an email address and the email's 'From:' header
        reported another email address, even if the only the username part is
        mismatched.
        """
        smtpFrom = 'feidanchaoren0001@gmail.com'
        emailFrom = Address('feidanchaoren0038@gmail.com')
        self._getIncomingLines(str(emailFrom))
        self._setUpResponder()
        self.responder.incoming.canonicalFromSMTP = smtpFrom
        self.assertFalse(self.responder.runChecks(emailFrom))

    def test_SMTPAutoresponder_runChecks_DKIM_dunno(self):
        """runChecks() should catch emails with bad DKIM headers
        (``"X-DKIM-Authentication-Results: dunno"``) for canonical domains
        which we're configured to check DKIM verification results for.
        """
        emailFrom = Address('dkimlikedunno@gmail.com')
        header = "X-DKIM-Authentication-Results: dunno"
        self._getIncomingLines(str(emailFrom))
        self.message.lines.insert(3, header)
        self._setUpResponder()
        self.assertFalse(self.responder.runChecks(emailFrom))

    def test_SMTPAutoresponder_runChecks_DKIM_bad(self):
        """runChecks() should catch emails with bad DKIM headers
        (``"X-DKIM-Authentication-Results: dunno"``) for canonical domains
        which we're configured to check DKIM verification results for.
        """
        emailFrom = Address('dkimlikewat@gmail.com')
        header = "X-DKIM-Authentication-Results: wowie zowie there's a sig here"
        self._getIncomingLines(str(emailFrom))
        self.message.lines.insert(3, header)
        self._setUpResponder()
        self.assertFalse(self.responder.runChecks(emailFrom))

    def test_SMTPAutoresponder_runChecks_blacklisted(self):
        """runChecks() on an blacklisted email address should return False."""
        emailFrom = Address('feidanchaoren0043@gmail.com')
        self._getIncomingLines(str(emailFrom))
        self._setUpResponder()
        self.assertFalse(self.responder.runChecks(emailFrom))
