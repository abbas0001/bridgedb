# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_email_templates -*-
#_____________________________________________________________________________
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft <isis@torproject.org> 0xA3ADB67A2CDB8B35
#           please also see AUTHORS file
# :copyright: (c) 2007-2017, The Tor Project, Inc.
#             (c) 2013-2017, Isis Lovecruft
# :license: see LICENSE for licensing information
#_____________________________________________________________________________

"""
.. py:module:: bridgedb.distributors.email.templates
    :synopsis: Templates for formatting emails sent out by the email
               distributor.

bridgedb.distributors.email.templates
========================

Templates for formatting emails sent out by the email distributor.
"""

import logging
import os

from datetime import datetime

from bridgedb import strings
from bridgedb.distributors.email.distributor import MAX_EMAIL_RATE


def addCommands(template):
    """Add text telling a client about supported email command.

    :type template: ``gettext.NullTranslation`` or ``gettext.GNUTranslation``
    :param template: A gettext translations instance, optionally with fallback
        languages set.
    :rtype: str
    :returns: A string explaining email commands.
    """
    # Tell them about the various email commands:
    cmdlist = []
    cmdlist.append(template.gettext(strings.EMAIL_MISC_TEXT.get(3)) + "\n")
    for cmd, desc in strings.EMAIL_COMMANDS.items():
        command  = '  '
        command += cmd
        while not len(command) >= 25:  # Align the command descriptions
            command += ' '
        command += template.gettext(desc)
        cmdlist.append(command)

    return "\n".join(cmdlist) + "\n\n"

def addGreeting(template):
    """Our "greeting" clarifies that this is an automated email response.

    :type template: ``gettext.NullTranslation`` or ``gettext.GNUTranslation``
    :param template: A gettext translations instance, optionally with fallback
        languages set.
    :rtype: str
    :returns: A string containing our "greeting".
    """

    greeting = template.gettext(strings.EMAIL_MISC_TEXT[0])
    greeting += u"\n\n"

    return greeting

def addBridgeAnswer(template, bridgeLines):
    # Give the user their bridges, i.e. the `answer`:
    bridgeText = u""
    bridgeText += template.gettext(strings.EMAIL_MISC_TEXT[1])
    bridgeText += u"\n\n"
    bridgeText += "%s\r\n" % bridgeLines[0]
    bridgeText += u"\n"
    for line in bridgeLines[1:]:
        bridgeText += template.gettext(strings.EMAIL_MISC_TEXT[4])
        bridgeText += u"\n\n"
        bridgeText += "%s\r\n" % line
        bridgeText += u"\n"

    return bridgeText

def addHowto(template):
    """Add help text on how to add bridges to Tor Browser.

    :type template: ``gettext.NullTranslation`` or ``gettext.GNUTranslation``
    :param template: A gettext translations instance, optionally with fallback
        languages set.
    """
    return template.gettext(strings.HOWTO_TBB[2])

def buildAnswerMessage(template, clientAddress=None, bridgeLines=None):
    try:
        message  = addGreeting(template)
        if bridgeLines:
            message += addBridgeAnswer(template, bridgeLines)
            message += addHowto(template)
        else:
            message += template.gettext(strings.EMAIL_MISC_TEXT[5])
        message += u'\n\n'
        message += addCommands(template)
    except Exception as error:  # pragma: no cover
        logging.error("Error while formatting email message template:")
        logging.exception(error)

    return message

def buildSpamWarning(template, clientAddress=None):
    message = addGreeting(template)

    try:
        message += template.gettext(strings.EMAIL_MISC_TEXT[2]) \
                   % str(MAX_EMAIL_RATE / 3600)
    except Exception as error:  # pragma: no cover
        logging.error("Error while formatting email spam template:")
        logging.exception(error)

    return message
