# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_translations -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft 0xA3ADB67A2CDB8B35 <isis@torproject.org>
# :copyright: (c) 2013-2017, Isis Lovecruft
#             (c) 2007-2017, The Tor Project, Inc.
# :license: 3-Clause BSD, see LICENSE for licensing information

import gettext
import logging
import os
import re

import babel.core

from bridgedb import _langs
from bridgedb import safelog
from bridgedb.parse import headers


TRANSLATIONS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'i18n')


def isLangOverridden(request):
    """
    Return True if the `lang' HTTP GET argument is set in the given request.

    :type request: :api:`twisted.web.server.Request`
    :param request: An incoming request from a client.
    :rtype: bool
    :returns: ``True`` if the given request has a `lang` argument and ``False``
        otherwise.
    """

    return request.args.get("lang", [None])[0] is not None

def getSupportedLangs():
    """Return all supported languages.

    :rtype: set
    :returns: A set of language locales, e.g.: set(['el', 'eo', ..., ]).
    """

    return _langs.get_langs()

def getFirstSupportedLang(langs):
    """Return the first language in **langs** that we support.

    :param list langs: All requested languages
    :rtype: str
    :returns: A country code for the client's preferred language.
    """
    lang = 'en-US'
    supported = _langs.get_langs()

    for l in langs:
        if l in supported:
            lang = l
            break

    # crop locales (like 'en-US') to just the language

    return lang.split('-')[0]

def getLocaleFromHTTPRequest(request):
    """Retrieve the languages from an HTTP ``Accept-Language:`` header.

    Parse the languages from the header, use them to install a
    ``gettext.translation`` chain via :func:`installTranslations`, and lastly
    return the requested languages.

    :type request: :api:`twisted.web.server.Request`
    :param request: An incoming request from a client.
    :rtype: list
    :returns: All requested languages.
    """
    header = request.getHeader('accept-language')
    if header is None:
        logging.debug("Client sent no 'Accept-Language' header. Using fallback.")
        header = 'en,en-US'

    langs = list(headers.parseAcceptLanguage(header))
    if not safelog.safe_logging:  # pragma: no cover
        logging.debug("Client Accept-Language (top 5): %s" % langs[:5])

    # Check if we got a ?lang=foo argument, and if we did, insert it first
    chosenLang = request.args.get("lang", [None,])[0]
    if chosenLang:
        logging.debug("Client requested language: %r" % chosenLang)
        langs.insert(0, chosenLang)

    # normalize languages to be unicode

    langs = list(map(lambda l: l if isinstance(l, str) else l.decode('utf-8'), langs))

    langs = filterRequestedLangs(langs)
    installTranslations(langs)
    return langs

def getLocaleFromPlusAddr(address):
    """See whether the user sent his email to a 'plus' address, for instance to
    bridges+fa@bridges.torproject.org. Plus addresses are the current
    mechanism to set the reply language.
    """
    replyLocale = "en"
    r = '.*(<)?(\w+\+(\w+)@\w+(?:\.\w+)+)(?(1)>)'
    match = re.match(r, address)
    if match:
        replyLocale = match.group(3)

    return replyLocale

def filterRequestedLangs(langs):
    """
    Filters user-requested languages to only include those supported
    by BridgeDB.
    :type list
    :param langs: A list of user-requested languages for translation
    :rtype: list
    :returns: All requested languages that are supported by BridgeDB
    """
    supported_langs = getSupportedLangs()
    valid_langs = list()
    for l in langs:
        if l in supported_langs:
            valid_langs.append(l)
    return valid_langs

def installTranslations(langs):
    """Create a ``gettext.translation`` chain for all **langs**.

    Attempt to install the first language in the **langs** list. If that
    fails, we receive a ``gettext.NullTranslation`` object, and if it worked
    then we have a ``gettext.GNUTranslation`` object. Whichever one we end up
    with, get the other languages and add them as fallbacks to the
    first. Lastly, install this chain of translations.

    :param list langs: A list of language codes.
    :returns: A ``gettext.NullTranslation`` or ``gettext.GNUTranslation`` with
        fallback languages set.
    """
    try:
        language = gettext.translation("bridgedb", localedir=TRANSLATIONS_DIR,
                                       languages=[langs[0]], fallback=True)
        for lang in langs[1:]:
            language.add_fallback(
                gettext.translation("bridgedb", localedir=TRANSLATIONS_DIR,
                                    languages=[lang], fallback=True))
    except IOError as error:
        logging.error(str(error))

    language.install()
    return language

def usingRTLLang(langs):
    """Check if we should translate the text into a RTL language.

    Choose the first language from the **langs** list that we support and
    return True if it is a RTL language, else return False.

    :param list langs: An incoming request.
    :rtype: bool
    :returns: ``True`` if the preferred language is right-to-left; ``False``
        otherwise.
    """

    lang = getFirstSupportedLang(langs)

    try:
        return babel.core.Locale.parse(lang).text_direction == "rtl"
    except ValueError as err:
        logging.warning("Couldn't parse locale %s: %s" % (lang, err))
        return False
