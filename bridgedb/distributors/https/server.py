# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_https_server -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: please see included AUTHORS file
# :copyright: (c) 2007-2017, The Tor Project, Inc.
#             (c) 2013-2017, Isis Lovecruft
# :license: see LICENSE for licensing information

"""
.. py:module:: bridgedb.distributors.https.server
    :synopsis: Servers which interface with clients and distribute bridges
               over HTTP(S).

bridgedb.distributors.https.server
=====================

Servers which interface with clients and distribute bridges over HTTP(S).

.. inheritance-diagram:: TranslatedTemplateResource IndexResource OptionsResource HowtoResource CaptchaProtectedResource GimpCaptchaProtectedResource ReCaptchaProtectedResource BridgesResource
    :parts: 1
"""

import base64
import gettext
import logging
import random
import re
import time
import os
import operator

from functools import partial

from ipaddr import IPv4Address

import mako.exceptions
from mako.template import Template
from mako.lookup import TemplateLookup

import babel.core

from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet import task
from twisted.internet.error import CannotListenError
from twisted.web import resource
from twisted.web import static
from twisted.web.server import NOT_DONE_YET
from twisted.web.server import Site
from twisted.web.util import redirectTo

from bridgedb import captcha
from bridgedb import crypto
from bridgedb import strings
from bridgedb import translations
from bridgedb import txrecaptcha
from bridgedb import metrics
from bridgedb import antibot
from bridgedb.distributors.common.http import setFQDN
from bridgedb.distributors.common.http import getFQDN
from bridgedb.distributors.common.http import getClientIP
from bridgedb.distributors.https.request import HTTPSBridgeRequest
from bridgedb.parse import headers
from bridgedb.parse.addr import isIPAddress
from bridgedb.qrcodes import generateQR
from bridgedb.safelog import logSafely
from bridgedb.schedule import Unscheduled
from bridgedb.schedule import ScheduledInterval
from bridgedb.util import replaceControlChars


#: The path to the HTTPS distributor's web templates.  (Should be the
#: "templates" directory in the same directory as this file.)
TEMPLATE_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')

# Setting `filesystem_checks` to False is recommended for production servers,
# due to potential speed increases. This means that the atimes of the Mako
# template files aren't rechecked every time the template is requested
# (otherwise, if they are checked, and the atime is newer, the template is
# recompiled). `collection_size` sets the number of compiled templates which
# are cached before the least recently used ones are removed. See:
# http://docs.makotemplates.org/en/latest/usage.html#using-templatelookup
lookup = TemplateLookup(directories=[TEMPLATE_DIR],
                        output_encoding='utf-8',
                        filesystem_checks=False,
                        collection_size=500)
logging.debug("Set template root to %s" % TEMPLATE_DIR)

#: A list of supported language tuples. Use getSortedLangList() to read this variable.
supported_langs = []

# We use our metrics singletons to keep track of BridgeDB metrics such as
# "number of failed HTTPS bridge requests."
httpsMetrix = metrics.HTTPSMetrics()
internalMetrix = metrics.InternalMetrics()


def stringifyRequestArgs(args):
    """Turn the given HTTP request arguments from bytes to str.

    :param dict args: A dictionary of request arguments.
    :rtype: dict
    :returns: A dictionary of request arguments.
    """

    # Convert all key/value pairs from bytes to str.
    str_args = {}
    for arg, values in args.items():
        arg = arg if isinstance(arg, str) else arg.decode("utf-8")
        values = [value.decode("utf-8") if isinstance(value, bytes)
                  else value for value in values]
        str_args[arg] = values

    return str_args


def replaceErrorPage(request, error, template_name=None, html=True):
    """Create a general error page for displaying in place of tracebacks.

    Log the error to BridgeDB's logger, and then display a very plain "Sorry!
    Something went wrong!" page to the client.

    :type request: :api:`twisted.web.http.Request`
    :param request: A ``Request`` object containing the HTTP method, full
        URI, and any URL/POST arguments and headers present.
    :type error: :exc:`Exception`
    :param error: Any exeption which has occurred while attempting to retrieve
        a template, render a page, or retrieve a resource.
    :param str template_name: A string describing which template/page/resource
        was being used when the exception occurred, i.e. ``'index.html'``.
    :param bool html: If ``True``, return one of two HTML error pages.  First,
        we attempt to render a fancier error page.  If that rendering failed,
        or if **html** is ``False``, then we return a very simple HTML page
        (without CSS, Javascript, images, etc.)  which simply says
        ``"Sorry! Something went wrong with your request."``
    :rtype: bytes
    :returns: A bytes object containing some content to serve to the client
        (rather than serving a Twisted traceback).
    """
    logging.error("Error while attempting to render %s: %s"
                  % (template_name or 'template',
                     mako.exceptions.text_error_template().render()))

    # TRANSLATORS: Please DO NOT translate the following words and/or phrases in
    # any string (regardless of capitalization and/or punctuation):
    #
    # "BridgeDB"
    # "pluggable transport"
    # "pluggable transports"
    # "obfs4"
    # "Tor"
    # "Tor Browser"
    #
    errorMessage = _("Sorry! Something went wrong with your request.")

    if not html:
        return errorMessage.encode("utf-8")

    try:
        rendered = resource500.render(request)
    except Exception as err:
        logging.exception(err)
        rendered = errorMessage.encode("utf-8")

    return rendered


def redirectMaliciousRequest(request):
    '''Setting the reponse code to 400 (Bad Request)'''
    logging.debug("Setting response code to 400 for %s" % getClientIP(request))
    request.setResponseCode(400)
    request.finish()
    return request


def getSortedLangList(rebuild=False):
    """
    Build and return a list of tuples that contains all of BridgeDB's supported
    languages, e.g.: [("az", "Az??rbaycan"), ("ca", "Catal??"), ..., ].

    :param rebuild bool: Force a rebuild of ``supported_langs`` if the argument
        is set to ``True``.  The default is ``False``.
    :rtype: list
    :returns: A list of tuples of the form (language-locale, language). The
        list is sorted alphabetically by language.  We use this list to
        provide a language switcher in BridgeDB's web interface.
    """

    # If we already compiled our languages, return them right away.
    global supported_langs
    if supported_langs and not rebuild:
        return supported_langs
    logging.debug("Building supported languages for language switcher.")

    langDict = {}
    for l in translations.getSupportedLangs():

        # We don't support 'en_GB', and 'en' and 'en_US' are the same.  'zh_HK'
        # is very similar to 'zh_TW' and we also lack translators for it, so we
        # drop the locale: <https://bugs.torproject.org/26543#comment:17>
        if l in ("en_GB", "en_US", "zh_HK"):
            continue

        try:
            langDict[l] = "%s" % (babel.core.Locale.parse(l).display_name.capitalize())
        except Exception as err:
            logging.warning("Failed to create language switcher option for %s: %s" % (l, err))

    # Sort languages alphabetically.
    supported_langs = sorted(langDict.items(), key=operator.itemgetter(1))

    return supported_langs


class MaliciousRequest(Exception):
    """Raised when we received a possibly malicious request."""


class CSPResource(resource.Resource):
    """A resource which adds a ``'Content-Security-Policy:'`` header.

    :vartype reportViolations: bool
    :var reportViolations: Use the Content Security Policy in `report-only`_
        mode, causing CSP violations to be reported back to the server (at
        :attr:`reportURI`, where the details of the violation will be logged).
        (default: ``False``)
    :vartype reportURI: str
    :var reportURI: If :attr:`reportViolations` is ``True``, Content Security
        Policy violations will be sent as JSON-encoded POST request to this
        URI.  (default: ``'csp-violation'``)

    .. _report-only:
        https://w3c.github.io/webappsec/specs/content-security-policy/#content-security-policy-report-only-header-field
    """
    reportViolations = False
    reportURI = 'csp-violation'

    def __init__(self, includeSelf=False, enabled=True, reportViolations=False,
                 useForwardedHeader=False):
        """Create a new :api:`twisted.web.resource.Resource` which adds a
        ``'Content-Security-Policy:'`` header.

        If enabled, the default Content Security Policy is::

            default-src 'none' ;
            base-uri FQDN ;
            script-src FQDN ;
            style-src FQDN ;
            img-src FQDN data: ;
            font-src FQDN ;

        where ``FQDN`` the value returned from the :func:`getFQDN` function
        (which uses the ``SERVER_PUBLIC_FQDN`` config file option).

        If the **includeSelf** parameter is enabled, then ``"'self'"``
        (literally, a string containing the word ``self``, surrounded by
        single-quotes) will be appended to the ``FQDN``.

        :param str fqdn: The public, fully-qualified domain name
            of the HTTP server that will serve this resource.
        :param bool includeSelf: Append ``'self'`` after the **fqdn** in the
            Content Security Policy.
        :param bool enabled: If ``False``, all Content Security Policy
            headers, including those used in report-only mode, will not be
            sent.  If ``True``, Content Security Policy headers (regardless of
            whether report-only mode is dis-/en-abled) will be sent.
            (default: ``True``)
        :param bool reportViolations: Use the Content Security Policy in
            report-only mode, causing CSP violations to be reported back to
            the server (at :attr:`reportURI`, where the details of the
            violation will be logged).  (default: ``False``)
        :param bool useForwardedHeader: If ``True``, then we will attempt to
            obtain the client's IP address from the ``X-Forwarded-For`` HTTP
            header.  This *only* has an effect if **reportViolations** is also
            set to ``True`` ??? the client's IP address is logged along with any
            CSP violation reports which the client sent via HTTP POST requests
            to our :attr:`reportURI`.  (default: ``False``)
        """
        resource.Resource.__init__(self)

        self.fqdn = getFQDN()
        self.enabled = enabled
        self.useForwardedHeader = useForwardedHeader
        self.csp = ("default-src 'none'; "
                    "base-uri {0}; "
                    "script-src {0}; "
                    "style-src {0}; "
                    "img-src {0} data:; "
                    "font-src {0}; ")

        if includeSelf:
            self.fqdn = " ".join([self.fqdn, "'self'"])

        if reportViolations:
            self.reportViolations = reportViolations

    def setCSPHeader(self, request):
        """Set the CSP header for a **request**.

        If this :class:`CSPResource` is :attr:`enabled`, then use
        :api:`twisted.web.http.Request.setHeader` to send an HTTP
        ``'Content-Security-Policy:'`` header for any response made to the
        **request** (or a ``'Content-Security-Policy-Report-Only:'`` header,
        if :attr:`reportViolations` is enabled).

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for :attr:`reportViolationURI`.
        """
        self.fqdn = self.fqdn or getFQDN()  # Update the FQDN if it changed.

        if self.enabled and self.fqdn:
            if not self.reportViolations:
                request.setHeader("Content-Security-Policy",
                                  self.csp.format(self.fqdn))
            else:
                logging.debug("Sending report-only CSP header...")
                request.setHeader("Content-Security-Policy-Report-Only",
                                  self.csp.format(self.fqdn) +
                                  "report-uri /%s" % self.reportURI)

    def render_POST(self, request):
        """If we're in debug mode, log a Content Security Policy violation.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for :attr:`reportViolationURI`.
        """
        try:
            client = getClientIP(request, self.useForwardedHeader)
            report = request.content.read(2048)

            logging.warning("Content-Security-Policy violation report from %s: %r"
                            % (client or "UNKNOWN CLIENT", report))
        except Exception as err:
            logging.error("Error while attempting to log CSP report: %s" % err)

        # Redirect back to the original resource after the report was logged:
        return redirectTo(request.uri, request)


class ErrorResource(CSPResource):
    """A resource which explains that BridgeDB is undergoing maintenance, or
    that some other (unexpected) error has occured.
    """
    isLeaf = True

    def __init__(self, template=None, code=200):
        """Create a :api:`twisted.web.resource.Resource` for an error page."""
        CSPResource.__init__(self)
        self.template = template
        self.code = code

    def render_GET(self, request):
        self.setCSPHeader(request)
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        request.setResponseCode(self.code)
        request.args = stringifyRequestArgs(request.args)

        try:
            template = lookup.get_template(self.template)
            rendered = template.render()
        except Exception as err:
            rendered = replaceErrorPage(request, err, html=False)

        return rendered

    render_POST = render_GET

resource400 = ErrorResource('error-400.html', code=400)
resource404 = ErrorResource('error-404.html', code=404)
resource500 = ErrorResource('error-500.html', code=500)
maintenance = ErrorResource('error-503.html', code=503)


class CustomErrorHandlingResource(resource.Resource):
    """A :api:`twisted.web.resource.Resource` which wraps the
    :api:`twisted.web.resource.Resource.getChild` method in order to use
    custom error handling pages.
    """
    def getChild(self, path, request):
        logging.debug("[404] %s" % request.uri)
        return resource404


class TranslatedTemplateResource(CustomErrorHandlingResource, CSPResource):
    """A generalised resource which uses gettext translations and Mako
    templates.
    """
    isLeaf = True

    def __init__(self, template=None, showFaq=True):
        """Create a new :api:`Resource <twisted.web.resource.Resource>` for a
        Mako-templated webpage.
        """
        gettext.install("bridgedb")
        CSPResource.__init__(self)
        self.template = template
        self.showFaq = showFaq

    def render_GET(self, request):
        self.setCSPHeader(request)
        request.args = stringifyRequestArgs(request.args)
        rtl = False
        try:
            langs = translations.getLocaleFromHTTPRequest(request)
            rtl = translations.usingRTLLang(langs)
            template = lookup.get_template(self.template)
            rendered = template.render(strings,
                                       getSortedLangList(),
                                       rtl=rtl,
                                       lang=langs[0],
                                       langOverride=translations.isLangOverridden(request),
                                       showFaq=self.showFaq)
        except Exception as err:  # pragma: no cover
            rendered = replaceErrorPage(request, err)
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        return rendered

    render_POST = render_GET


class IndexResource(TranslatedTemplateResource):
    """The parent resource of all other documents hosted by the webserver."""

    def __init__(self):
        """Create a :api:`twisted.web.resource.Resource` for the index page."""
        TranslatedTemplateResource.__init__(self, 'index.html')


class OptionsResource(TranslatedTemplateResource):
    """A resource with additional options which a client may use to specify the
    which bridge types should be returned by :class:`BridgesResource`.
    """
    def __init__(self):
        """Create a :api:`twisted.web.resource.Resource` for the options page."""
        TranslatedTemplateResource.__init__(self, 'options.html')


class InfoResource(TranslatedTemplateResource):
    def __init__(self):
        TranslatedTemplateResource.__init__(self, 'info.html', showFaq=False)


class HowtoResource(TranslatedTemplateResource):
    """A resource which explains how to use bridges."""

    def __init__(self):
        """Create a :api:`twisted.web.resource.Resource` for the HowTo page."""
        TranslatedTemplateResource.__init__(self, 'howto.html')


class CaptchaProtectedResource(CustomErrorHandlingResource, CSPResource):
    """A general resource protected by some form of CAPTCHA."""

    isLeaf = True

    def __init__(self, publicKey=None, secretKey=None,
                 useForwardedHeader=False, protectedResource=None):
        CSPResource.__init__(self)
        self.publicKey = publicKey
        self.secretKey = secretKey
        self.useForwardedHeader = useForwardedHeader
        self.resource = protectedResource

    def getClientIP(self, request):
        """Get the client's IP address from the ``'X-Forwarded-For:'``
        header, or from the :api:`request <twisted.web.server.Request>`.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` for a
            :api:`twisted.web.resource.Resource`.
        :rtype: ``None`` or :any:`str`
        :returns: The client's IP address, if it was obtainable.
        """
        return getClientIP(request, self.useForwardedHeader)

    def getCaptchaImage(self, request=None):
        """Get a CAPTCHA image.

        :rtype: tuple
        :returns: A 2-tuple of ``(image, challenge)``, where ``image`` is a
            JPEG-encoded image of type bytes, and ``challenge`` is a unique
            string. If unable to retrieve a CAPTCHA, returns a tuple
            containing (b'', '').
        """
        return (b'', '')

    def extractClientSolution(self, request):
        """Extract the client's CAPTCHA solution from a POST request.

        This is used after receiving a POST request from a client (which
        should contain their solution to the CAPTCHA), to extract the solution
        and challenge strings.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for 'bridges.html'.
        :returns: A redirect for a request for a new CAPTCHA if there was a
            problem. Otherwise, returns a 2-tuple of bytes, the first is the
            client's CAPTCHA solution from the text input area, and the second
            is the challenge string.
        """
        try:
            challenge = request.args['captcha_challenge_field'][0]
            response = request.args['captcha_response_field'][0]
        except Exception as error:
            raise MaliciousRequest(
                ("Client CAPTCHA solution to HTTPS distributor server "
                 "didn't include correct HTTP arguments: %s" % error))
        return (challenge, response)

    def checkSolution(self, request):
        """Override this method to check a client's CAPTCHA solution.

        :rtype: bool
        :returns: ``True`` if the client correctly solved the CAPTCHA;
            ``False`` otherwise.
        """
        return False

    def render_GET(self, request):
        """Retrieve a CAPTCHA and serve it to the client.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for a page which should be
            protected by a CAPTCHA.
        :rtype: bytes
        :returns: A rendered HTML page containing a CAPTCHA challenge image
            for the client to solve.
        """
        self.setCSPHeader(request)
        request.args = stringifyRequestArgs(request.args)

        rtl = False
        image, challenge = self.getCaptchaImage(request)

        try:
            langs = translations.getLocaleFromHTTPRequest(request)
            rtl = translations.usingRTLLang(langs)
            # TODO: this does not work for versions of IE < 8.0
            imgstr = b'data:image/jpeg;base64,%s' % base64.b64encode(image)
            template = lookup.get_template('captcha.html')
            rendered = template.render(strings,
                                       getSortedLangList(),
                                       rtl=rtl,
                                       lang=langs[0],
                                       langOverride=translations.isLangOverridden(request),
                                       imgstr=imgstr.decode("utf-8"),
                                       challenge_field=challenge)
        except Exception as err:
            rendered = replaceErrorPage(request, err, 'captcha.html')

        request.setHeader("Content-Type", "text/html; charset=utf-8")
        return rendered

    def render_POST(self, request):
        """Process a client's CAPTCHA solution.

        If the client's CAPTCHA solution is valid (according to
        :meth:`checkSolution`), process and serve their original
        request. Otherwise, redirect them back to a new CAPTCHA page.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object, including POST arguments which
            should include two key/value pairs: one key being
            ``'captcha_challenge_field'``, and the other,
            ``'captcha_response_field'``. These POST arguments should be
            obtained from :meth:`render_GET`.
        :rtype: str
        :returns: A rendered HTML page containing a ReCaptcha challenge image
            for the client to solve.
        """
        self.setCSPHeader(request)
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        request.args = stringifyRequestArgs(request.args)

        try:
            if self.checkSolution(request) is True:
                httpsMetrix.recordValidHTTPSRequest(request)
                return self.resource.render(request)
        except ValueError as err:
            logging.debug(str(err))
        except MaliciousRequest as err:
            logging.debug(str(err))
            # Make them wait a bit, then redirect them to a "daring
            # work of art" as pennance for their sins.
            d = task.deferLater(reactor, 1, lambda: request)
            d.addCallback(redirectMaliciousRequest)
            httpsMetrix.recordInvalidHTTPSRequest(request)
            return NOT_DONE_YET
        except Exception as err:
            logging.debug(str(err))
            httpsMetrix.recordInvalidHTTPSRequest(request)
            return replaceErrorPage(request, err)

        httpsMetrix.recordInvalidHTTPSRequest(request)
        logging.debug("Client failed a CAPTCHA; returning redirect to %s"
                      % request.uri)
        return redirectTo(request.uri, request)


class GimpCaptchaProtectedResource(CaptchaProtectedResource):
    """A web resource which uses a local cache of CAPTCHAs, generated with
    gimp-captcha_, to protect another resource.

    .. _gimp-captcha: https://github.com/isislovecruft/gimp-captcha
    """

    def __init__(self, hmacKey=None, captchaDir='', **kwargs):
        """Protect a resource via this one, using a local CAPTCHA cache.

        :param str secretkey: A PKCS#1 OAEP-padded, private RSA key, used for
            verifying the client's solution to the CAPTCHA. See
            :func:`bridgedb.crypto.getRSAKey` and the
            ``GIMP_CAPTCHA_RSA_KEYFILE`` config setting.
        :param str publickey: A PKCS#1 OAEP-padded, public RSA key, used for
            creating the ``captcha_challenge_field`` string to give to a
            client.
        :param bytes hmacKey: The master HMAC key, used for validating CAPTCHA
            challenge strings in :meth:`captcha.GimpCaptcha.check`. The file
            where this key is stored can be set via the
            ``GIMP_CAPTCHA_HMAC_KEYFILE`` option in the config file.
        :param str captchaDir: The directory where the cached CAPTCHA images
            are stored. See the ``GIMP_CAPTCHA_DIR`` config setting.
        :param bool useForwardedHeader: If ``True``, obtain the client's IP
            address from the ``X-Forwarded-For`` HTTP header.
        :type protectedResource: :api:`twisted.web.resource.Resource`
        :param protectedResource: The resource to serve if the client
            successfully passes the CAPTCHA challenge.
        :param str serverPublicFQDN: The public, fully-qualified domain name
            of the HTTP server that will serve this resource.
        """
        CaptchaProtectedResource.__init__(self, **kwargs)
        self.hmacKey = hmacKey
        self.captchaDir = captchaDir

    def checkSolution(self, request):
        """Process a solved CAPTCHA via :meth:`bridgedb.captcha.GimpCaptcha.check`.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object, including POST arguments which
            should include two key/value pairs: one key being
            ``'captcha_challenge_field'``, and the other,
            ``'captcha_response_field'``. These POST arguments should be
            obtained from :meth:`render_GET`.
        :rtupe: bool
        :returns: True, if the CAPTCHA solution was valid; False otherwise.
        """
        valid = False
        challenge, solution = self.extractClientSolution(request)
        clientIP = self.getClientIP(request)
        clientHMACKey = crypto.getHMAC(self.hmacKey, clientIP)

        try:
            valid = captcha.GimpCaptcha.check(challenge, solution,
                                              self.secretKey, clientHMACKey)
        except captcha.CaptchaExpired as error:
            logging.warn(error)
            valid = False

        logging.debug("%sorrect captcha from %r: %r."
                      % ("C" if valid else "Inc", clientIP, solution))
        return valid

    def getCaptchaImage(self, request):
        """Get a random CAPTCHA image from our **captchaDir**.

        Creates a :class:`~bridgedb.captcha.GimpCaptcha`, and calls its
        :meth:`~bridgedb.captcha.GimpCaptcha.get` method to return a random
        CAPTCHA and challenge string.

        :type request: :api:`twisted.web.http.Request`
        :param request: A client's initial request for some other resource
            which is protected by this one (i.e. protected by a CAPTCHA).
        :returns: A 2-tuple of ``(image, challenge)``, where::
            - ``image`` is a string holding a binary, JPEG-encoded image.
            - ``challenge`` is a unique string associated with the request.
        """
        # Create a new HMAC key, specific to requests from this client:
        clientIP = self.getClientIP(request)
        clientHMACKey = crypto.getHMAC(self.hmacKey, clientIP)
        capt = captcha.GimpCaptcha(self.publicKey, self.secretKey,
                                   clientHMACKey, self.captchaDir)
        try:
            capt.get()
        except captcha.GimpCaptchaError as error:
            logging.error(error)
        except Exception as error:  # pragma: no cover
            logging.error("Unhandled error while retrieving Gimp captcha!")
            logging.exception(error)

        return (capt.image, capt.challenge)

    def render_GET(self, request):
        """Get a random CAPTCHA from our local cache directory and serve it to
        the client.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for a page which should be
            protected by a CAPTCHA.
        :rtype: str
        :returns: A rendered HTML page containing a ReCaptcha challenge image
           for the client to solve.
        """
        return CaptchaProtectedResource.render_GET(self, request)

    def render_POST(self, request):
        """Process a client's CAPTCHA solution.

        If the client's CAPTCHA solution is valid (according to
        :meth:`checkSolution`), process and serve their original
        request. Otherwise, redirect them back to a new CAPTCHA page.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object, including POST arguments which
            should include two key/value pairs: one key being
            ``'captcha_challenge_field'``, and the other,
            ``'captcha_response_field'``. These POST arguments should be
            obtained from :meth:`render_GET`.
        :rtype: str
        :returns: A rendered HTML page containing a ReCaptcha challenge image
            for the client to solve.
        """
        return CaptchaProtectedResource.render_POST(self, request)


class ReCaptchaProtectedResource(CaptchaProtectedResource):
    """A web resource which uses the reCaptcha_ service.

    .. _reCaptcha: http://www.google.com/recaptcha
    """

    def __init__(self, remoteIP=None, **kwargs):
        CaptchaProtectedResource.__init__(self, **kwargs)
        self.remoteIP = remoteIP

    def _renderDeferred(self, checkedRequest):
        """Render this resource asynchronously.

        :type checkedRequest: tuple
        :param checkedRequest: A tuple of ``(bool, request)``, as returned
            from :meth:`checkSolution`.
        """
        try:
            valid, request = checkedRequest
        except Exception as err:
            logging.error("Error in _renderDeferred(): %s" % err)
            return

        logging.debug("Attemping to render %svalid request %r"
                      % ('' if valid else 'in', request))
        if valid is True:
            try:
                rendered = self.resource.render(request)
            except Exception as err:  # pragma: no cover
                rendered = replaceErrorPage(request, err)
        else:
            logging.info("Client failed a CAPTCHA; redirecting to %s"
                         % request.uri)
            rendered = redirectTo(request.uri, request)

        try:
            request.write(rendered.encode('utf-8') if isinstance(rendered, str) else rendered)
            request.finish()
        except Exception as err:  # pragma: no cover
            logging.exception(err)

        return request

    def getCaptchaImage(self, request):
        """Get a CAPTCHA image from the remote reCaptcha server.

        :type request: :api:`twisted.web.http.Request`
        :param request: A client's initial request for some other resource
            which is protected by this one (i.e. protected by a CAPTCHA).
        :returns: A 2-tuple of ``(image, challenge)``, where::
            - ``image`` is a string holding a binary, JPEG-encoded image.
            - ``challenge`` is a unique string associated with the request.
        """
        capt = captcha.ReCaptcha(self.publicKey, self.secretKey)

        try:
            capt.get()
        except Exception as error:
            logging.fatal("Connection to Recaptcha server failed: %s" % error)

        if capt.image is None:
            logging.warn("No CAPTCHA image received from ReCaptcha server!")

        return (capt.image, capt.challenge)

    def getRemoteIP(self):
        """Mask the client's real IP address with a faked one.

        The fake client IP address is sent to the reCaptcha server, and it is
        either the public IP address of bridges.torproject.org (if the config
        option ``RECAPTCHA_REMOTE_IP`` is configured), or a random IP.

        :rtype: str
        :returns: A fake IP address to report to the reCaptcha API server.
        """
        if self.remoteIP:
            remoteIP = self.remoteIP
        else:
            # generate a random IP for the captcha submission
            remoteIP = IPv4Address(random.randint(0, 2**32-1)).compressed

        return remoteIP

    def checkSolution(self, request):
        """Process a solved CAPTCHA by sending it to the ReCaptcha server.

        The client's IP address is not sent to the ReCaptcha server; instead,
        a completely random IP is generated and sent instead.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object, including POST arguments which
            should include two key/value pairs: one key being
            ``'captcha_challenge_field'``, and the other,
            ``'captcha_response_field'``. These POST arguments should be
            obtained from :meth:`render_GET`.
        :rtupe: :api:`twisted.internet.defer.Deferred`
        :returns: A deferred which will callback with a tuple in the following
            form:
                (:type:`bool`, :api:`twisted.web.server.Request`)
            If the CAPTCHA solution was valid, a tuple will contain::
                (True, Request)
            Otherwise, it will contain::
                (False, Request)
        """
        challenge, response = self.extractClientSolution(request)
        clientIP = self.getClientIP(request)
        remoteIP = self.getRemoteIP()

        logging.debug("Captcha from %r. Parameters: %r"
                      % (clientIP, request.args))

        def checkResponse(solution, request):
            """Check the :class:`txrecaptcha.RecaptchaResponse`.

            :type solution: :class:`txrecaptcha.RecaptchaResponse`.
            :param solution: The client's CAPTCHA solution, after it has been
                submitted to the reCaptcha API server.
            """
            # This valid CAPTCHA result from this function cannot be reliably
            # unittested, because it's callbacked to from the deferred
            # returned by ``txrecaptcha.submit``, the latter of which would
            # require networking (as well as automated CAPTCHA
            # breaking). Hence, the 'no cover' pragma.
            if solution.is_valid:  # pragma: no cover
                logging.info("Valid CAPTCHA solution from %r." % clientIP)
                httpsMetrix.recordValidHTTPSRequest(request)
                return (True, request)
            else:
                logging.info("Invalid CAPTCHA solution from %r: %r"
                             % (clientIP, solution.error_code))
                httpsMetrix.recordInvalidHTTPSRequest(request)
                return (False, request)

        d = txrecaptcha.submit(challenge, response, self.secretKey,
                               remoteIP).addCallback(checkResponse, request)
        return d

    def render_GET(self, request):
        """Retrieve a ReCaptcha from the API server and serve it to the client.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for 'bridges.html'.
        :rtype: str
        :returns: A rendered HTML page containing a ReCaptcha challenge image
            for the client to solve.
        """
        return CaptchaProtectedResource.render_GET(self, request)

    def render_POST(self, request):
        """Process a client's CAPTCHA solution.

        If the client's CAPTCHA solution is valid (according to
        :meth:`checkSolution`), process and serve their original
        request. Otherwise, redirect them back to a new CAPTCHA page.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object, including POST arguments which
            should include two key/value pairs: one key being
            ``'captcha_challenge_field'``, and the other,
            ``'captcha_response_field'``. These POST arguments should be
            obtained from :meth:`render_GET`.
        :returns: :api:`twisted.web.server.NOT_DONE_YET`, in order to handle
            the ``Deferred`` returned from :meth:`checkSolution`. Eventually,
            when the ``Deferred`` request is done being processed,
            :meth:`_renderDeferred` will handle rendering and displaying the
            HTML to the client.
        """
        self.setCSPHeader(request)
        request.args = stringifyRequestArgs(request.args)
        d = self.checkSolution(request)
        d.addCallback(self._renderDeferred)
        return NOT_DONE_YET


class BridgesResource(CustomErrorHandlingResource, CSPResource):
    """This resource displays bridge lines in response to a request."""

    isLeaf = True

    def __init__(self, distributor, schedule, N=1, useForwardedHeader=False,
                 includeFingerprints=True):
        """Create a new resource for displaying bridges to a client.

        :type distributor: :class:`HTTPSDistributor`
        :param distributor: The mechanism to retrieve bridges for this
            distributor.
        :type schedule: :class:`~bridgedb.schedule.ScheduledInterval`
        :param schedule: The time period used to tweak the bridge selection
            procedure.
        :param int N: The number of bridges to hand out per query.
        :param bool useForwardedHeader: Whether or not we should use the the
            X-Forwarded-For header instead of the source IP address.
        :param bool includeFingerprints: Do we include the bridge's
            fingerprint in the response?
        """
        gettext.install("bridgedb")
        CSPResource.__init__(self)
        self.distributor = distributor
        self.schedule = schedule
        self.nBridgesToGive = N
        self.useForwardedHeader = useForwardedHeader
        self.includeFingerprints = includeFingerprints

    def render(self, request):
        """Render a response for a client HTTP request.

        Presently, this method merely wraps :meth:`getBridgeRequestAnswer` to
        catch any unhandled exceptions which occur (otherwise the server will
        display the traceback to the client). If an unhandled exception *does*
        occur, the client will be served the default "No bridges currently
        available" HTML response page.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object containing the HTTP method, full
            URI, and any URL/POST arguments and headers present.
        :rtype: bytes
        :returns: A plaintext or HTML response to serve.
        """
        self.setCSPHeader(request)
        request.args = stringifyRequestArgs(request.args)

        try:
            response = self.getBridgeRequestAnswer(request)
        except Exception as err:
            logging.exception(err)
            response = self.renderAnswer(request)

        return response.encode('utf-8') if isinstance(response, str) else response

    def getClientIP(self, request):
        """Get the client's IP address from the ``'X-Forwarded-For:'``
        header, or from the :api:`request <twisted.web.server.Request>`.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object for a
            :api:`twisted.web.resource.Resource`.
        :rtype: ``None`` or :any:`str`
        :returns: The client's IP address, if it was obtainable.
        """
        return getClientIP(request, self.useForwardedHeader)

    def getBridgeRequestAnswer(self, request):
        """Respond to a client HTTP request for bridges.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object containing the HTTP method, full
            URI, and any URL/POST arguments and headers present.
        :rtype: str
        :returns: A plaintext or HTML response to serve.
        """
        bridgeLines = None
        interval = self.schedule.intervalStart(time.time())
        ip = self.getClientIP(request)

        logging.info("Replying to web request from %s. Parameters were %r"
                     % (ip, request.args))

        # Convert all key/value pairs from bytes to str.
        str_args = {}
        for arg, values in request.args.items():
            arg = arg if isinstance(arg, str) else arg.decode("utf-8")
            values = [value.decode("utf-8") if isinstance(value, bytes) else value for value in values]
            str_args[arg] = values
        request.args = str_args

        if ip:
            bridgeRequest = HTTPSBridgeRequest()
            bridgeRequest.client = ip
            bridgeRequest.isValid(True)
            bridgeRequest.withIPversion(request.args)
            bridgeRequest.withPluggableTransportType(request.args)
            bridgeRequest.withoutBlockInCountry(request)
            bridgeRequest.generateFilters()

            bridges = self.distributor.getBridges(bridgeRequest, interval)
            bridgeLines = [replaceControlChars(bridge.getBridgeLine(
                bridgeRequest, self.includeFingerprints)) for bridge in bridges]

            internalMetrix.recordHandoutsPerBridge(bridgeRequest, bridges)

            if antibot.isRequestFromBot(request):
                transports = bridgeRequest.transports
                # Return either a decoy bridge or no bridge.
                if len(transports) > 2:
                    logging.warning("More than one transport requested")
                    return self.renderAnswer(request)
                ttype = "vanilla" if len(transports) == 0 else transports[0]
                return self.renderAnswer(request, antibot.getDecoyBridge(ttype, bridgeRequest.ipVersion))

        return self.renderAnswer(request, bridgeLines)

    def getResponseFormat(self, request):
        """Determine the requested format for the response.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object containing the HTTP method, full
            URI, and any URL/POST arguments and headers present.
        :rtype: ``None`` or str
        :returns: The argument of the first occurence of the ``format=`` HTTP
            GET parameter, if any were present. (The only one which currently
            has any effect is ``format=plain``, see note in
            :meth:`renderAnswer`.)  Otherwise, returns ``None``.
        """
        format = request.args.get("format", None)
        if format and len(format):
            format = format[0]  # Choose the first arg
        return format

    def renderAnswer(self, request, bridgeLines=None):
        """Generate a response for a client which includes **bridgesLines**.

        .. note: The generated response can be plain or HTML. A plain response
            looks like::

                voltron 1.2.3.4:1234 ABCDEF01234567890ABCDEF01234567890ABCDEF
                voltron 5.5.5.5:5555 0123456789ABCDEF0123456789ABCDEF01234567

            That is, there is no HTML, what you see is what you get, and what
            you get is suitable for pasting directly into Tor Launcher (or
            into a torrc, if you prepend ``"Bridge "`` to each line). The
            plain format can be requested from BridgeDB's web service by
            adding an ``&format=plain`` HTTP GET parameter to the URL. Also
            note that you won't get a QRCode, usage instructions, error
            messages, or any other fanciness if you use the plain format.

        :type request: :api:`twisted.web.http.Request`
        :param request: A ``Request`` object containing the HTTP method, full
            URI, and any URL/POST arguments and headers present.
        :type bridgeLines: list or None
        :param bridgeLines: A list of strings used to configure a Tor client
            to use a bridge. If ``None``, then the returned page will instead
            explain that there were no bridges of the type they requested,
            with instructions on how to proceed.
        :rtype: bytes
        :returns: A plaintext or HTML response to serve.
        """
        rtl = False
        format = self.getResponseFormat(request)

        if not bridgeLines:
            internalMetrix.recordEmptyHTTPSResponse()

        if format == 'plain':
            request.setHeader("Content-Type", "text/plain")
            try:
                rendered = '\n'.join(bridgeLines).encode('utf-8')
            except Exception as err:
                rendered = replaceErrorPage(request, err, html=False)
        else:
            request.setHeader("Content-Type", "text/html; charset=utf-8")
            qrcode = None
            qrjpeg = generateQR(bridgeLines)

            if qrjpeg:
                qrcode = b'data:image/jpeg;base64,%s' % base64.b64encode(qrjpeg)
                qrcode = qrcode.decode("utf-8")

            try:
                langs = translations.getLocaleFromHTTPRequest(request)
                rtl = translations.usingRTLLang(langs)
                template = lookup.get_template('bridges.html')
                rendered = template.render(strings,
                                           getSortedLangList(),
                                           rtl=rtl,
                                           lang=langs[0],
                                           langOverride=translations.isLangOverridden(request),
                                           answer=bridgeLines,
                                           qrcode=qrcode)
            except Exception as err:
                rendered = replaceErrorPage(request, err)

        return rendered.encode("utf-8") if isinstance(rendered, str) else rendered


def addWebServer(config, distributor):
    """Set up a web server for HTTP(S)-based bridge distribution.

    :type config: :class:`bridgedb.persistent.Conf`
    :param config: A configuration object from
         :mod:`bridgedb.main`. Currently, we use these options::
             HTTP_UNENCRYPTED_PORT
             HTTP_UNENCRYPTED_BIND_IP
             HTTP_USE_IP_FROM_FORWARDED_HEADER
             HTTPS_N_BRIDGES_PER_ANSWER
             HTTPS_INCLUDE_FINGERPRINTS
             HTTPS_KEY_FILE
             HTTPS_CERT_FILE
             HTTPS_PORT
             HTTPS_BIND_IP
             HTTPS_USE_IP_FROM_FORWARDED_HEADER
             HTTPS_ROTATION_PERIOD
             RECAPTCHA_ENABLED
             RECAPTCHA_PUB_KEY
             RECAPTCHA_SEC_KEY
             RECAPTCHA_REMOTEIP
             GIMP_CAPTCHA_ENABLED
             GIMP_CAPTCHA_DIR
             GIMP_CAPTCHA_HMAC_KEYFILE
             GIMP_CAPTCHA_RSA_KEYFILE
             SERVER_PUBLIC_FQDN
             CSP_ENABLED
             CSP_REPORT_ONLY
             CSP_INCLUDE_SELF
    :type distributor: :class:`bridgedb.distributors.https.distributor.HTTPSDistributor`
    :param distributor: A bridge distributor.
    :raises SystemExit: if the servers cannot be started.
    :rtype: :api:`twisted.web.server.Site`
    :returns: A webserver.
    """
    captcha = None
    fwdHeaders = config.HTTP_USE_IP_FROM_FORWARDED_HEADER
    numBridges = config.HTTPS_N_BRIDGES_PER_ANSWER
    fprInclude = config.HTTPS_INCLUDE_FINGERPRINTS

    logging.info("Starting web servers...")

    setFQDN(config.SERVER_PUBLIC_FQDN)

    index   = IndexResource()
    options = OptionsResource()
    howto   = HowtoResource()
    info    = InfoResource()
    robots  = static.File(os.path.join(TEMPLATE_DIR, 'robots.txt'))
    assets  = static.File(os.path.join(TEMPLATE_DIR, 'assets/'))
    csp     = CSPResource(enabled=config.CSP_ENABLED,
                          includeSelf=config.CSP_INCLUDE_SELF,
                          reportViolations=config.CSP_REPORT_ONLY,
                          useForwardedHeader=fwdHeaders)

    root = CustomErrorHandlingResource()
    root.putChild(b'', index)
    root.putChild(b'robots.txt', robots)
    root.putChild(b'assets', assets)
    root.putChild(b'options', options)
    root.putChild(b'howto', howto)
    root.putChild(b'info', info)
    root.putChild(b'maintenance', maintenance)
    root.putChild(b'error', resource500)
    root.putChild(b'malicious', resource400)
    root.putChild(CSPResource.reportURI, csp)

    if config.RECAPTCHA_ENABLED:
        publicKey = config.RECAPTCHA_PUB_KEY
        secretKey = config.RECAPTCHA_SEC_KEY
        captcha = partial(ReCaptchaProtectedResource,
                          remoteIP=config.RECAPTCHA_REMOTEIP)
    elif config.GIMP_CAPTCHA_ENABLED:
        # Get the master HMAC secret key for CAPTCHA challenges, and then
        # create a new HMAC key from it for use on the server.
        captchaKey = crypto.getKey(config.GIMP_CAPTCHA_HMAC_KEYFILE)
        hmacKey = crypto.getHMAC(captchaKey, "Captcha-Key")
        # Load or create our encryption keys:
        secretKey, publicKey = crypto.getRSAKey(config.GIMP_CAPTCHA_RSA_KEYFILE)
        captcha = partial(GimpCaptchaProtectedResource,
                          hmacKey=hmacKey,
                          captchaDir=config.GIMP_CAPTCHA_DIR)

    if config.HTTPS_ROTATION_PERIOD:
        count, period = config.HTTPS_ROTATION_PERIOD.split()
        sched = ScheduledInterval(int(count), period)
    else:
        sched = Unscheduled()

    bridges = BridgesResource(distributor, sched, numBridges, fwdHeaders,
                              includeFingerprints=fprInclude)
    if captcha:
        # Protect the 'bridges' page with a CAPTCHA, if configured to do so:
        protected = captcha(publicKey=publicKey,
                            secretKey=secretKey,
                            useForwardedHeader=fwdHeaders,
                            protectedResource=bridges)
        root.putChild(b'bridges', protected)
        logging.info("Protecting resources with %s." % captcha.func.__name__)
    else:
        root.putChild(b'bridges', bridges)

    site = Site(root)
    site.displayTracebacks = False

    if config.HTTP_UNENCRYPTED_PORT:  # pragma: no cover
        ip = config.HTTP_UNENCRYPTED_BIND_IP or ""
        port = config.HTTP_UNENCRYPTED_PORT or 80
        try:
            reactor.listenTCP(port, site, interface=ip)
        except CannotListenError as error:
            raise SystemExit(error)
        logging.info("Started HTTP server on %s:%d" % (str(ip), int(port)))

    if config.HTTPS_PORT:  # pragma: no cover
        ip = config.HTTPS_BIND_IP or ""
        port = config.HTTPS_PORT or 443
        try:
            from twisted.internet.ssl import DefaultOpenSSLContextFactory
            factory = DefaultOpenSSLContextFactory(config.HTTPS_KEY_FILE,
                                                   config.HTTPS_CERT_FILE)
            reactor.listenSSL(port, site, factory, interface=ip)
        except CannotListenError as error:
            raise SystemExit(error)
        logging.info("Started HTTPS server on %s:%d" % (str(ip), int(port)))

    return site
