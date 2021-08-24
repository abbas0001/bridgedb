# -*- coding: utf-8; test-case-name: bridgedb.test.test_email_request; -*-
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
.. py:module:: bridgedb.distributors.email.request
    :synopsis: Classes for parsing and storing information about requests for
               bridges which are sent to the email distributor.

bridgedb.distributors.email.request
======================

Classes for parsing and storing information about requests for bridges
which are sent to the email distributor.

.. inheritance-diagram:: EmailBridgeRequest
    :parts: 1

::

  bridgedb.distributors.email.request
   | |_ determineBridgeRequestOptions - Figure out which filters to apply, or
   |                                    offer help.
   |_ EmailBridgeRequest - A request for bridges which was received through
                           the email distributor.

..
"""

from __future__ import print_function
from __future__ import unicode_literals

import logging
import re

from bridgedb import strings
from bridgedb import bridgerequest


#: A regular expression for matching the Pluggable Transport method TYPE in
#: emailed requests for Pluggable Transports.
TRANSPORT_REGEXP = ".*transport ([a-z][_a-z0-9]*)"
TRANSPORT_PATTERN = re.compile(TRANSPORT_REGEXP)

#: A regular expression that matches country codes in requests for unblocked
#: bridges.
UNBLOCKED_REGEXP = ".*unblocked ([a-z]{2,4})"
UNBLOCKED_PATTERN = re.compile(UNBLOCKED_REGEXP)

#: Regular expressions that we use to match for email commands.  Any command is
#: valid as long as it wasn't quoted, i.e., the line didn't start with a '>'
#: character.
GET_LINE       = re.compile("([^>].*)?get")
IPV6_LINE      = re.compile("([^>].*)?ipv6")
TRANSPORT_LINE = re.compile("([^>].*)?transport")
UNBLOCKED_LINE = re.compile("([^>].*)?unblocked")
VANILLA_LINE = re.compile("([^>].*)?vanilla")


def determineBridgeRequestOptions(lines):
    """Figure out which :mod:`~bridgedb.filters` to apply.

    .. note:: If any ``'transport TYPE'`` was requested, or bridges not
        blocked in a specific CC (``'unblocked CC'``), then the ``TYPE``
        and/or ``CC`` will *always* be stored as a *lowercase* string.

    :param list lines: A list of lines from an email, including the headers.
    :rtype: :class:`EmailBridgeRequest`
    :returns: A :class:`~bridgerequest.BridgeRequest` with all of the requested
        parameters set. The returned ``BridgeRequest`` will have already had
        its filters generated via :meth:`~EmailBridgeRequest.generateFilters`.
    """
    request = EmailBridgeRequest()
    skippedHeaders = False
    requested_transport = False

    for line in lines:
        line = line.strip().lower()
        # Ignore all lines before the first empty line:
        if not line: skippedHeaders = True
        if not skippedHeaders: continue

        if GET_LINE.match(line) is not None:
            request.isValid(True)
            logging.debug("Email request was valid.")
        if IPV6_LINE.match(line) is not None:
            request.withIPv6()
        if TRANSPORT_LINE.match(line) is not None:
            requested_transport = True
            request.withPluggableTransportType(line)
        if UNBLOCKED_LINE.match(line) is not None:
            request.withoutBlockInCountry(line)
        if VANILLA_LINE.match(line) is not None:
            requested_transport = True

    # If not transport requested we will respond with our default transport protocol.
    if not requested_transport:
        # Note that this variable must satisfy TRANSPORT_PATTERN.
        default_transport = "transport %s" % strings._getDefaultTransport()
        request.withPluggableTransportType(default_transport)

    # We cannot expect all users to understand BridgeDB's commands, so we will
    # return bridges even if the request was invalid.
    if not request.isValid():
        logging.debug("Email request was invalid.")
        request.isValid(True)

    logging.debug("Generating hashring filters for request.")
    request.generateFilters()
    return request


class EmailBridgeRequest(bridgerequest.BridgeRequestBase):
    """We received a request for bridges through the email distributor."""

    def __init__(self):
        """Process a new bridge request received through the
        :class:`~bridgedb.distributors.email.distributor.EmailDistributor`.
        """
        super(EmailBridgeRequest, self).__init__()

    def withoutBlockInCountry(self, line):
        """This request was for bridges not blocked in **country**.

        Add any country code found in the **line** to the list of
        ``notBlockedIn``. Currently, a request for a transport is recognized
        if the email line contains the ``'unblocked'`` command.

        :param str country: The line from the email wherein the client
            requested some type of Pluggable Transport.
        """
        unblocked = None

        logging.debug("Parsing 'unblocked' line: %r" % line)
        try:
            unblocked = UNBLOCKED_PATTERN.match(line).group(1)
        except (TypeError, AttributeError):
            pass

        if unblocked:
            self.notBlockedIn.append(unblocked)
            logging.info("Email requested bridges not blocked in: %r"
                         % unblocked)

    def withPluggableTransportType(self, line):
        """This request included a specific Pluggable Transport identifier.

        Add any Pluggable Transport method TYPE found in the **line** to the
        list of ``transports``. Currently, a request for a transport is
        recognized if the email line contains the ``'transport'`` command.

        :param str line: The line from the email wherein the client
            requested some type of Pluggable Transport.
        """
        transport = None
        logging.debug("Parsing 'transport' line: %r" % line)

        try:
            transport = TRANSPORT_PATTERN.match(line).group(1)
        except (TypeError, AttributeError):
            pass

        if transport:
            self.transports.append(transport)
            logging.info("Email requested transport type: %r" % transport)
