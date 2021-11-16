# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_strings ; -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft 0xA3ADB67A2CDB8B35 <isis@torproject.org>
# :copyright: (c) 2007-2017, The Tor Project, Inc.
#             (c) 2013-2017, Isis Lovecruft
#             (c) 2007-2017, all entities within the AUTHORS file
# :license: 3-clause BSD, see included LICENSE for information

"""Commonly used string constants."""

from __future__ import unicode_literals

# This won't work on Python2.6, however
#     1) We don't use Python2.6, and
#     2) We don't care about supporting Python2.6, because Python 2.6 (and,
#        honestly, all of Python2) should die.
from collections import OrderedDict


def _(text):
    """This is necessary because strings are translated when they're imported.
    Otherwise this would make it impossible to switch languages more than
    once.

    :returns: The **text**.
    """
    return text


EMAIL_MISC_TEXT = {
    0: _("""\
[This is an automated email.]"""),
    1: _("""\
Here is your bridge:"""),
    2: _("""\
You have exceeded the rate limit. Please slow down! The minimum time between
emails is %s hours. All further emails during this time period will be ignored."""),
    3: _("""\
If these bridges are not what you need, reply to this email with one of
the following commands in the message body:"""),
    # TRANSLATORS: "it" here is a bridge, this sentence comes after giving a first bridge to provide a second one
    4: _("""\
If it doesn't work, you can try this other bridge:"""),
    5: _("""\
There are no bridges currently available, sorry."""),
}

WELCOME = {
    # TRANSLATORS: Please DO NOT translate "BridgeDB".
    # TRANSLATORS: Please DO NOT translate "Pluggable Transports".
    # TRANSLATORS: Please DO NOT translate "Tor".
    # TRANSLATORS: Please DO NOT translate "Tor Network".
    0: _("""\
BridgeDB can provide bridges with several %stypes of Pluggable Transports%s,
which can help obfuscate your connections to the Tor Network, making it more
difficult for anyone watching your internet traffic to determine that you are
using Tor.\n\n"""),

    # TRANSLATORS: Please DO NOT translate "Pluggable Transports".
    1: _("""\
Some bridges with IPv6 addresses are also available, though some Pluggable
Transports aren't IPv6 compatible.\n\n"""),

    # TRANSLATORS: Please DO NOT translate "BridgeDB".
    # TRANSLATORS: The phrase "plain-ol'-vanilla" means "plain, boring,
    # regular, or unexciting". Like vanilla ice cream. It refers to bridges
    # which do not have Pluggable Transports, and only speak the regular,
    # boring Tor protocol. Translate it as you see fit. Have fun with it.
    2: _("""\
Additionally, BridgeDB has plenty of plain-ol'-vanilla bridges %s without any
Pluggable Transports %s which maybe doesn't sound as cool, but they can still
help to circumvent internet censorship in many cases.\n\n"""),
}
"""These strings should go on the ``options.html`` template used by the
:mod:`~bridgedb.distributors.https.server`. They are used as an
introduction to explain what Tor bridges are, what bridges do, and why
someone might want to use bridges.
"""

FAQ = {
    0: _("What are bridges?"),
    1: _("""\
%s Bridges %s are Tor relays that help you circumvent censorship."""),
}

OTHER_DISTRIBUTORS = {
    0: _("I need an alternative way of getting bridges!"),
# TRANSLATORS: Please DO NOT translate "get transport obfs4".
    1: _("""\
Another way to get bridges is to send an email to %s. Leave the email subject
empty and write "get transport obfs4" in the email's message body. Please note
that you must send the email using an address from one of the following email
providers: %s or %s."""),
}

HELP = {
    0: _("My bridges don't work! I need help!"),
    # TRANSLATORS: Please DO NOT translate "Tor Browser".
    # TRANSLATORS: The two '%s' are substituted with "Tor Browser Manual" and
    # "Support Portal", respectively.
    1: _("""If your Tor Browser cannot connect, please take a look at the %s and our %s."""),
}

BRIDGES = {
    0: _("Here are your bridge lines:"),
    1: _("Get Bridges!"),
}

BRIDGEDB_INFO = {
    0: _("Bridge distribution mechanisms"),
    # TRANSLATORS: Please DO NOT translate "BridgeDB", "HTTPS", and "Moat".
    1: _("""\
BridgeDB implements four mechanisms to distribute bridges: "HTTPS", "Moat",
"Email", and "Reserved".  Bridges that are not distributed over BridgeDB use
the pseudo-mechanism "None".  The following list briefly explains how these
mechanisms work and our %sBridgeDB metrics%s visualize how popular each of the
mechanisms is."""),
    2: _("""\
The "HTTPS" distribution mechanism hands out bridges over this website.  To get
bridges, go to %sbridges.torproject.org%s, select your preferred options, and
solve the subsequent CAPTCHA."""),
    3: _("""\
The "Moat" distribution mechanism is part of Tor Browser, allowing users to
request bridges from inside their Tor Browser settings.  To get bridges, go to
your Tor Browser's %sTor settings%s, click on "request a new bridge", solve the
subsequent CAPTCHA, and Tor Browser will automatically add your new
bridges."""),
    4: _("""\
Users can request bridges from the "Email" distribution mechanism by sending an
email to %sbridges@torproject.org%s and writing "get transport obfs4" in the
email body."""),
    5: _("Reserved"),
    6: _("""\
BridgeDB maintains a small number of bridges that are not distributed
automatically.  Instead, we reserve these bridges for manual distribution and
hand them out to NGOs and other organizations and individuals that need
bridges.  Bridges that are distributed over the "Reserved" mechanism may not
see users for a long time.  Note that the "Reserved" distribution mechanism is
called "Unallocated" in %sbridge pool assignment%s files."""),
    7: _("None"),
    8: _("""\
Bridges whose distribution mechanism is "None" are not distributed by BridgeDB.
It is the bridge operator's responsibility to distribute their bridges to
users.  Note that on Relay Search, a freshly set up bridge's distribution
mechanism says "None" for up to approximately one day.  Be a bit patient, and
it will then change to the bridge's actual distribution mechanism.
"""),
}

OPTIONS = {
    0: _("Please select options for bridge type:"),
    1: _("Do you need IPv6 addresses?"),
    2: _("Do you need a %s?"),
}

CAPTCHA = {
    0: _('Your browser is not displaying images properly.'),
    1: _('Enter the characters from the image above...'),
}

HOWTO_TBB = {
    0: _("""How to start using your bridges"""),
    # TRANSLATORS: Please DO NOT translate "Tor Browser".
    1: _("""\
 First, you need to %sdownload Tor Browser%s. Our Tor Browser User
 Manual explains how you can add your bridges to Tor Browser. If you are
 using Windows, Linux, or OS X, %sclick here%s to learn more. If you
 are using Android, %sclick here%s."""),
    # TRANSLATORS: Please DO NOT translate "Tor Browser" neither "Tails".
    2: _("""\
If you are using Tor Browser:

1. Choose "☰ ▸ Settings ▸ Tor" to open your Tor settings.

2. In the "Bridges" section, enter your bridge in the "Provide a bridge" field.

If you are using Tails, enter your bridge in the Tor Connection assistant."""),
}

EMAIL_COMMANDS = {
    "get bridges": _("(Request default Tor bridges.)"),
    "get ipv6": _("(Request IPv6 bridges.)"),
    "get transport obfs4": _("(Request obfs4 obfuscated bridges.)"),
    "get vanilla": _("(Request unobfuscated Tor bridges.)"),
    # TRANSLATORS: Please DO NOT translate "BridgeDB".
}

#-----------------------------------------------------------------------------
#           All of the following containers are untranslated!
#-----------------------------------------------------------------------------

#: SUPPORTED TRANSPORTS is dictionary mapping all Pluggable Transports
#: methodname to whether or not we actively distribute them. The ones which we
#: distribute SHOULD have the following properties:
#:
#:   1. The PT is in a widely accepted, usable state for most Tor users.
#:   2. The PT is currently publicly deployed *en masse*".
#:   3. The PT is included within the transports which Tor Browser offers in
#:      the stable releases.
#:
#: These will be sorted by methodname in alphabetical order.
#:
#: ***Don't change this setting here; change it in :file:`bridgedb.conf`.***
SUPPORTED_TRANSPORTS = {}

#: DEFAULT_TRANSPORT is a string. It should be the PT methodname of the
#: transport which is selected by default (e.g. in the webserver dropdown
#: menu).
#:
#: ***Don't change this setting here; change it in :file:`bridgedb.conf`.***
DEFAULT_TRANSPORT = ''

def _getSupportedTransports():
    """Get the list of currently supported transports.

    :rtype: list
    :returns: A list of strings, one for each supported Pluggable Transport
        methodname, sorted in alphabetical order.
    """
    supported = [name.lower() for name,w00t in SUPPORTED_TRANSPORTS.items() if w00t]
    supported.sort()
    return supported

def _setDefaultTransport(transport):
    global DEFAULT_TRANSPORT
    DEFAULT_TRANSPORT = transport

def _getDefaultTransport():
    return DEFAULT_TRANSPORT

def _setSupportedTransports(transports):
    """Set the list of currently supported transports.

    .. note: You shouldn't need to touch this. This is used by the config file
        parser. You should change the SUPPORTED_TRANSPORTS dictionary in
        :file:`bridgedb.conf`.

    :param dict transports: A mapping of Pluggable Transport methodnames
        (strings) to booleans.  If the boolean is ``True``, then the Pluggable
        Transport is one which we will (more easily) distribute to clients.
        If ``False``, then we (sort of) don't distribute it.
    """
    global SUPPORTED_TRANSPORTS
    SUPPORTED_TRANSPORTS = transports

def _getSupportedAndDefaultTransports():
    """Get a dictionary of currently supported transports, along with a boolean
    marking which transport is the default.

    It is returned as a :class:`collections.OrderedDict`, because if it is a
    regular dict, then the dropdown menu would populated in random order each
    time the page is rendered.  It is sorted in alphabetical order.

    :rtype: :class:`collections.OrderedDict`
    :returns: An :class:`~collections.OrderedDict` of the Pluggable Transport
        methodnames from :data:`SUPPORTED_TRANSPORTS` whose value in
        ``SUPPORTED_TRANSPORTS`` is ``True``.  If :data:`DEFAULT_TRANSPORT` is
        set, then the PT methodname in the ``DEFAULT_TRANSPORT`` setting is
        added to the :class:`~collections.OrderedDict`, with the value
        ``True``. Every other transport in the returned ``OrderedDict`` has
        its value set to ``False``, so that only the one which should be the
        default PT is ``True``.
    """
    supported = _getSupportedTransports()
    transports = OrderedDict(zip(supported, [False for _ in range(len(supported))]))

    if DEFAULT_TRANSPORT:
        transports[DEFAULT_TRANSPORT] = True

    return transports

EMAIL_SPRINTF = {
    # Goes into the "%s types of Pluggable Transports %s" part of ``WELCOME[0]``
    "WELCOME0": ("", "[0]"),
    # Goes into the "%s without Pluggable Transport %s" part of ``WELCOME[2]``
    "WELCOME2": ("-", "-"),
    # For the "%s Tor Browser download page %s" part of ``HOWTO_TBB[1]``
    "HOWTO_TBB1": ("", "[0]", "", "[1]", "", "[2]"),
    # For the "you should email %s" in ``HELP[0]``
    "HELP0": ("frontdesk@torproject.org"),
}
"""``EMAIL_SPRINTF`` is a dictionary that maps translated strings which
contain format specifiers (i.e. ``%s``) to what those format specifiers should
be replaced with in a given template system.

For example, a string which needs a pair of HTML ``("<a href=''">, "</a>")``
tags (for the templates used by :mod:`bridgedb.distributors.https.server`) would need some
alternative replacements for the :mod:`EmailServer`, because the latter uses
templates with a ``text/plain`` mimetype instead of HTML. For the
``EmailServer``, the format strings specifiers are replaced with an empty
string where the opening ``<a>`` tags would go, and a numbered Markdown link
specifier where the closing ``</a>`` tags would go.

The keys in this dictionary are the Python variable names of the corresponding
strings which are being formatted, i.e. ``WELCOME0`` would be the string
replacements for ``strings.WELCOME.get(0)``.


For example, the ``0`` string in :data:`WELCOME` above has the substring::

    "%s without Pluggable Transport %s"

and so to replace the two ``%s`` format specifiers, you would use this mapping
like so::

>>> from bridgedb import strings
>>> welcome = strings.WELCOME[0] % strings.EMAIL_SPRINTF["WELCOME0"]
>>> print welcome.split('\n')[0]
BridgeDB can provide bridges with several types of Pluggable Transports[0],

"""

EMAIL_REFERENCE_LINKS = {
    "WELCOME0": "[0]: https://www.torproject.org/docs/pluggable-transports.html",
    "HOWTO_TBB1": "[0]: https://www.torproject.org/download/",
    "HOWTO_TBB2": "[1]: https://tb-manual.torproject.org/bridges/#entering-bridge-addresses",
    "HOWTO_TBB3": "[2]: https://tb-manual.torproject.org/mobile-tor/#circumvention",
}
