# -*- coding: utf-8; test-case-name: bridgedb.test.test_email_autoresponder -*-
#_____________________________________________________________________________
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Nick Mathewson <nickm@torproject.org>
#           Isis Lovecruft <isis@torproject.org> 0xA3ADB67A2CDB8B35
#           Matthew Finkel <sysrqb@torproject.org>
#           please also see AUTHORS file
# :copyright: (c) 2007-2017, The Tor Project, Inc.
#             (c) 2013-2017, Isis Lovecruft
# :license: see LICENSE for licensing information
#_____________________________________________________________________________

"""
.. py:module:: bridgedb.distributors.email.autoresponder
    :synopsis: Functionality for autoresponding to incoming emails.

bridgedb.distributors.email.autoresponder
============================

Functionality for autoresponding to incoming emails.

.. inheritance-diagram:: SMTPAutoresponder
    :parts: 1

::

  bridgedb.distributors.email.autoresponder
   | |_ createResponseBody - Parse lines from an incoming email and determine
   | |                       how to respond.
   | |_ generateResponse - Create an email response.
   |
   |_ SMTPAutoresponder - An SMTP autoresponder for incoming mail.
..
"""

from __future__ import unicode_literals
from __future__ import print_function

import io
import logging
import time

from email.utils import parseaddr
from email.message import EmailMessage
from twisted.internet import defer
from twisted.internet import reactor
from twisted.mail import smtp
from twisted.python import failure

from bridgedb import strings
from bridgedb import metrics
from bridgedb import safelog
from bridgedb.distributors.email import dkim
from bridgedb.distributors.email import request
from bridgedb.distributors.email import templates
from bridgedb.distributors.email.distributor import TooSoonEmail
from bridgedb.distributors.email.distributor import IgnoreEmail
from bridgedb.parse import addr
from bridgedb.parse.addr import canonicalizeEmailDomain
from bridgedb.qrcodes import generateQR
from bridgedb.util import levenshteinDistance
from bridgedb import translations

# We use our metrics singletons to keep track of BridgeDB metrics such as
# "number of failed HTTPS bridge requests."
emailMetrix = metrics.EmailMetrics()
internalMetrix = metrics.InternalMetrics()


def createResponseBody(lines, context, client, lang='en'):
    """Parse the **lines** from an incoming email request and determine how to
    respond.

    :param list lines: The list of lines from the original request sent by the
        client.
    :type context: :class:`bridgedb.distributors.email.server.MailServerContext`
    :param context: The context which contains settings for the email server.
    :type client: :api:`twisted.mail.smtp.Address`
    :param client: The client's email address which should be in the
        ``'To:'`` header of the response email.
    :param str lang: The 2-5 character locale code to use for translating the
        email. This is obtained from a client sending a email to a valid plus
        address which includes the translation desired, i.e. by sending an
        email to `bridges+fa@torproject.org
        <mailto:bridges+fa@torproject.org>`__, the client should receive a
        response in Farsi.
    :rtype: (str, bytes)
    :returns: ``None`` if we shouldn't respond to the client (i.e., if they
        have already received a rate-limiting warning email). Otherwise,
        returns a string containing the (optionally translated) body for the
        email response which we should send out and the qrcode image of the
        bridges if we provide bridges.
    """
    translator = translations.installTranslations([lang])
    bridges = None
    try:
        bridgeRequest = request.determineBridgeRequestOptions(lines)
        bridgeRequest.client = str(client)

        # Otherwise they must have requested bridges:
        interval = context.schedule.intervalStart(time.time())
        bridges = context.distributor.getBridges(bridgeRequest, interval)
    except TooSoonEmail as error:
        logging.info("Got a mail too frequently: %s." % error)
        return templates.buildSpamWarning(translator, client), None
    except (IgnoreEmail, addr.BadEmail) as error:
        logging.info(error)
        # Don't generate a response if their email address is unparsable or
        # invalid, or if we've already warned them about rate-limiting:
        return None, None
    else:
        answer = "(no bridges currently available)\r\n"
        qrcode = None
        if bridges:
            transport = bridgeRequest.justOnePTType()
            bridgeLines = [b.getBridgeLine(bridgeRequest, context.includeFingerprints) for b in bridges]
            answer = "".join("  %s\r\n" % line for line in bridgeLines)
            qrcode = generateQR(bridgeLines)
            internalMetrix.recordHandoutsPerBridge(bridgeRequest, bridges)
        else:
            internalMetrix.recordEmptyEmailResponse()
        return templates.buildAnswerMessage(translator, client, answer), qrcode

def generateResponse(fromAddress, client, body, subject=None,
                     messageID=None, qrcode=None):
    """Create an :class:`email.message.EmailMessage`

    :param str fromAddress: The :rfc:`2821` email address which should be in
        the ``'From:'`` header.
    :type client: :api:`twisted.mail.smtp.Address`
    :param client: The client's email address which should be in the
        ``'To:'`` header of the response email.
    :param str subject: The string to write to the ``'Subject:'`` header.
    :param str body: The body of the email.
    :type messageID: ``None`` or :any:`str`
    :param messageID: The :rfc:`2822` specifier for the ``'Message-ID:'``
        header, if including one is desirable.
    :returns: An :class:`email.message.EmailMessage` which contains the entire
        email. To obtain the contents of the email, including all headers,
        simply use :meth:`EmailMessage.as_string`.
    """
    response = EmailMessage()
    response["From"] = fromAddress
    response["To"] = str(client)

    if not subject:
        response["Subject"] = '[no subject]'
    else:
        response["Subject"] = subject
    if messageID:
        response.add_header("In-Reply-To", messageID)
    
    response.add_header("Date", smtp.rfc822date().decode("utf-8"))
    response.set_content(body)  

    if qrcode:
        response.add_attachment(qrcode, maintype="image", subtype="jpeg", filename="qrcode.jpg")

    # Only log the email text (including all headers) if SAFE_LOGGING is
    # disabled:
    if not safelog.safe_logging:
        logging.debug("Email contents:\n%s" % response.as_string())
    else:
        logging.debug("Email text for %r created." % str(client))

    return response


class SMTPAutoresponder(smtp.SMTPClient):
    """An :api:`twisted.mail.smtp.SMTPClient` for responding to incoming mail.

    The main worker in this class is the :meth:`reply` method, which functions
    to dissect an incoming email from an incoming :class:`SMTPMessage` and
    create a :class:`EmailMessage` email message in reply to it, and then,
    finally, send it out.

    :vartype log: :api:`twisted.python.util.LineLog`
    :ivar log: A cache of debug log messages.
    :vartype debug: bool
    :ivar debug: If ``True``, enable logging (accessible via :attr:`log`).
    :ivar str identity: Our FQDN which will be sent during client ``HELO``.
    :vartype incoming: :api:`Message <twisted.mail.smtp.rfc822.Message>`
    :ivar incoming: An incoming message, i.e. as returned from
        :meth:`SMTPMessage.getIncomingMessage`.

    :vartype deferred: :api:`twisted.internet.defer.Deferred`

    :ivar deferred: A :api:`Deferred <twisted.internet.defer.Deferred>` with
       registered callbacks, :meth:`sentMail` and :meth:`sendError`, which
       will be given to the reactor in order to process the sending of the
       outgoing response email.
    """
    debug = True
    identity = smtp.DNSNAME

    def __init__(self):
        """Handle responding (or not) to an incoming email."""
        smtp.SMTPClient.__init__(self, self.identity)
        self.incoming = None
        self.deferred = defer.Deferred()
        self.deferred.addCallback(self.sentMail)
        self.deferred.addErrback(self.sendError)

    def getMailData(self):
        """Gather all the data for building the response to the client.

        This method must return a file-like object containing the data of the
        message to be sent. Lines in the file should be delimited by ``\\n``.

        :rtype: ``None`` or :class:`EmailMessage`
        :returns: An ``EmailMessage``, if we have a response to send in reply
            to the incoming email, otherwise, returns ``None``.
        """
        clients = self.getMailTo()
        if not clients: return
        client = clients[0]  # There should have been only one anyway

        # Log the email address that this message came from if SAFELOGGING is
        # not enabled:
        if not safelog.safe_logging:
            logging.debug("Incoming email was from %s ..." % client)

        if not self.runChecks(client): return

        recipient = self.getMailFrom()
        # Look up the locale part in the 'To:' address, if there is one, and
        # get the appropriate Translation object:
        lang = translations.getLocaleFromPlusAddr(recipient)
        logging.info("Client requested email translation: %s" % lang)

        body, qrcode = createResponseBody(self.incoming.lines,
                                          self.incoming.context,
                                          client, lang)

        # The string EMAIL_MISC_TEXT[1] shows up in an email if BridgeDB
        # responds with bridges.  Everything else we count as an invalid
        # request.
        translator = translations.installTranslations([lang])
        if body is not None and translator.gettext(strings.EMAIL_MISC_TEXT[1]) in body:
            emailMetrix.recordValidEmailRequest(self)
        else:
            emailMetrix.recordInvalidEmailRequest(self)

        if not body: return  # The client was already warned.

        messageID = self.incoming.message.get("Message-ID", None)
        subject = self.incoming.message.get("Subject", None)
        response = generateResponse(recipient, client, body, subject, messageID, qrcode)
        return response

    def getMailTo(self):
        """Attempt to get the client's email address from an incoming email.

        :rtype: list
        :returns: A list containing the client's
            :func:`normalized <bridgedb.parse.addr.normalizeEmail>` email
            :api:`Address <twisted.mail.smtp.Address>`, if it originated from
            a domain that we accept and the address was well-formed. Otherwise,
            returns ``None``. Even though we're likely to respond to only one
            client at a time, the return value of this method must be a list
            in order to hook into the rest of
            :api:`twisted.mail.smtp.SMTPClient` correctly.
        """
        clients = []
        addrHeader = None
        try: fromAddr = parseaddr(self.incoming.message.get("From"))[1]
        except (IndexError, TypeError, AttributeError): pass
        else: addrHeader = fromAddr

        if not addrHeader:
            logging.warn("No From header on incoming mail.")
            try: senderHeader = parseaddr(self.incoming.message.get("Sender"))[1]
            except (IndexError, TypeError, AttributeError): pass
            else: addrHeader = senderHeader
        if not addrHeader:
            logging.warn("No Sender header on incoming mail.")
            return clients

        client = None
        try:
            if addrHeader in self.incoming.context.whitelist.keys():
                logging.debug("Email address was whitelisted: %s."
                              % addrHeader)
                client = smtp.Address(addrHeader)
            else:
                normalized = addr.normalizeEmail(
                    addrHeader,
                    self.incoming.context.domainMap,
                    self.incoming.context.domainRules)
                client = smtp.Address(normalized)
        except (addr.UnsupportedDomain) as error:
            logging.warn(error)
        except (addr.BadEmail, smtp.AddressError) as error:
            logging.warn(error)

        if client:
            clients.append(client)

        return clients

    def getMailFrom(self):
        """Find our address in the recipients list of the **incoming** message.

        :rtype: str
        :return: Our address from the recipients list. If we can't find it
            return our default ``EMAIL_FROM_ADDRESS`` from the config file.
        """
        logging.debug("Searching for our email address in 'To:' header...")

        ours = None

        try:
            ourAddress = smtp.Address(self.incoming.context.fromAddr)
            allRecipients = self.incoming.message.get_all("To")

            for address in allRecipients:
                recipient = smtp.Address(address)
                if not ourAddress.domain in recipient.domain:
                    logging.debug(("Not our domain (%s) or subdomain, skipping"
                                   " email address: %s")
                                  % (ourAddress.domain, str(recipient)))
                    continue
                # The recipient's username should at least start with ours,
                # but it still might be a '+' address.
                if not recipient.local.startswith(ourAddress.local):
                    logging.debug(("Username doesn't begin with ours, skipping"
                                   " email address: %s") % str(recipient))
                    continue
                # Only check the username before the first '+':
                beforePlus = recipient.local.split(b'+', 1)[0]
                if beforePlus == ourAddress.local:
                    ours = str(recipient)
            if not ours:
                raise addr.BadEmail('No email address accepted, please see log', allRecipients)

        except Exception as error:
            logging.error(("Couldn't find our email address in incoming email "
                           "headers: %r" % error))
            # Just return the email address that we're configured to use:
            ours = self.incoming.context.fromAddr

        logging.debug("Found our email address: %s." % ours)
        return ours

    def sentMail(self, success):
        """Callback for a :api:`twisted.mail.smtp.SMTPSenderFactory`,
        called when an attempt to send an email is completed.

        If some addresses were accepted, code and resp are the response
        to the DATA command. If no addresses were accepted, code is -1
        and resp is an informative message.

        :param int code: The code returned by the SMTP Server.
        :param str resp: The string response returned from the SMTP Server.
        :param int numOK: The number of addresses accepted by the remote host.
        :param list addresses: A list of tuples (address, code, resp) listing
            the response to each ``RCPT TO`` command.
        :param log: The SMTP session log. (We don't use this, but it is sent
            by :api:`twisted.mail.smtp.SMTPSenderFactory` nonetheless.)
        """
        numOk, addresses = success

        for (address, code, resp) in addresses:
            logging.info("Sent reply to %s" % address)
            logging.debug("SMTP server response: %d %s" % (code, resp))

        if self.debug:
            for line in self.log.log:
                if line:
                    logging.debug(line)

    def sendError(self, fail):
        """Errback for a :api:`twisted.mail.smtp.SMTPSenderFactory`.

        :type fail: :api:`twisted.python.failure.Failure` or
            :api:`twisted.mail.smtp.SMTPClientError`
        :param fail: An exception which occurred during the transaction to
            send the outgoing email.
        """
        logging.debug("called with %r" % fail)

        if isinstance(fail, failure.Failure):
            error = fail.getTraceback() or "Unknown"
        elif isinstance(fail, Exception):
            error = fail
        logging.error(error)

        try:
            # This handles QUIT commands, disconnecting, and closing the
            # transport:
            smtp.SMTPClient.sendError(self, fail)
        # We might not have `transport` and `protocol` attributes, depending
        # on when and where the error occurred, so just catch and log it:
        except Exception as error:
            logging.error(error)

    def reply(self):
        """Reply to an incoming email. Maybe.

        If nothing is returned from either :func:`createResponseBody` or
        :func:`generateResponse`, then the incoming email will not be
        responded to at all. This can happen for several reasons, for example:
        if the DKIM signature was invalid or missing, or if the incoming email
        came from an unacceptable domain, or if there have been too many
        emails from this client in the allotted time period.

        :rtype: :api:`twisted.internet.defer.Deferred`
        :returns: A ``Deferred`` which will callback when the response has
            been successfully sent, or errback if an error occurred while
            sending the email.
        """
        logging.info("Got an email; deciding whether to reply.")

        response = self.getMailData()
        if not response:
            return self.deferred

        return self.send(response)

    def runChecks(self, client):
        """Run checks on the incoming message, and only reply if they pass.

        1. Check if the client's address is whitelisted.

        2. If it's not whitelisted, check that the domain names, taken from
        the SMTP ``MAIL FROM:`` command and the email ``'From:'`` header, can
        be :func:`canonicalized <addr.canonicalizeEmailDomain>`.

        3. Check that those canonical domains match.

        4. If the incoming message is from a domain which supports DKIM
        signing, then run :func:`bridgedb.distributors.email.dkim.checkDKIM` as well.

        .. note:: Calling this method sets the
            :attr:`incoming.canonicalFromEmail` and
            :attr:`incoming.canonicalDomainRules` attributes of the
            :attr:`incoming` message.

        :type client: :api:`twisted.mail.smtp.Address`
        :param client: The client's email address, extracted from the
            ``'From:'`` header from the incoming email.
        :rtype: bool
        :returns: ``False`` if the checks didn't pass, ``True`` otherwise.
        """
        # If the SMTP ``RCPT TO:`` domain name couldn't be canonicalized, then
        # we *should* have bailed at the SMTP layer, but we'll reject this
        # email again nonetheless:
        if not self.incoming.canonicalFromSMTP:
            logging.warn(("SMTP 'MAIL FROM' wasn't from a canonical domain "
                          "for email from %s") % str(client))
            return False

        # Allow whitelisted addresses through the canonicalization check:
        if str(client) in self.incoming.context.whitelist.keys():
            self.incoming.canonicalFromEmail = client.domain
            logging.info("'From:' header contained whitelisted address: %s"
                         % str(client))
        # Straight up reject addresses in the EMAIL_BLACKLIST config option:
        elif str(client) in self.incoming.context.blacklist:
            logging.info("'From:' header contained blacklisted address: %s")
            return False
        else:
            logging.debug("Canonicalizing client email domain...")
            try:
                # The client's address was already checked to see if it came
                # from a supported domain and is a valid email address in
                # :meth:`getMailTo`, so we should just be able to re-extract
                # the canonical domain safely here:
                self.incoming.canonicalFromEmail = canonicalizeEmailDomain(
                    client.domain, self.incoming.canon)
                logging.debug("Canonical email domain: %s"
                              % self.incoming.canonicalFromEmail)
            except addr.UnsupportedDomain as error:
                logging.info("Domain couldn't be canonicalized: %s"
                             % safelog.logSafely(client.domain))
                return False

        # The canonical domains from the SMTP ``MAIL FROM:`` and the email
        # ``From:`` header should match:
        if self.incoming.canonicalFromSMTP != self.incoming.canonicalFromEmail:
            logging.error("SMTP/Email canonical domain mismatch!")
            logging.debug("Canonical domain mismatch: %s != %s"
                          % (self.incoming.canonicalFromSMTP,
                             self.incoming.canonicalFromEmail))
            #return False

        self.incoming.domainRules = self.incoming.context.domainRules.get(
            self.incoming.canonicalFromEmail, list())

        # If the domain's ``domainRules`` say to check DKIM verification
        # results, and those results look bad, reject this email:
        if not dkim.checkDKIM(self.incoming.message, self.incoming.domainRules):
            return False

        # If fuzzy matching is enabled via the EMAIL_FUZZY_MATCH setting, then
        # calculate the Levenshtein String Distance (see
        # :func:`~bridgedb.util.levenshteinDistance`):
        if self.incoming.context.fuzzyMatch != 0:
            for blacklistedAddress in self.incoming.context.blacklist:
                distance = levenshteinDistance(str(client), blacklistedAddress)
                if distance <= self.incoming.context.fuzzyMatch:
                    logging.info("Fuzzy-matched %s to blacklisted address %s!"
                                 % (self.incoming.canonicalFromEmail,
                                    blacklistedAddress))
                    return False

        return True

    def send(self, response, retries=0, timeout=30, reaktor=reactor):
        """Send our **response** in reply to :data:`incoming`.

        :type client: :api:`twisted.mail.smtp.Address`
        :param client: The email address of the client.
        :param response: A :class:`EmailMessage`.
        :param int retries: Try resending this many times. (default: ``0``)
        :param int timeout: Timeout after this many seconds. (default: ``30``)
        :rtype: :api:`Deferred <twisted.internet.defer.Deferred>`
        :returns: Our :data:`deferred`.
        """
        logging.info("Sending reply to %s ..." % str(response["To"]))

        factory = smtp.SMTPSenderFactory(self.incoming.context.smtpFromAddr,
                                         response["To"],
                                         io.BytesIO(response.as_bytes()),
                                         self.deferred,
                                         retries=retries,
                                         timeout=timeout)
        factory.domain = smtp.DNSNAME
        reaktor.connectTCP(self.incoming.context.smtpServerIP,
                           self.incoming.context.smtpServerPort,
                           factory)
        return self.deferred
