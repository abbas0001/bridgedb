# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_metrics ; -*-
# _____________________________________________________________________________
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: please see included AUTHORS file
# :copyright: (c) 2019, The Tor Project, Inc.
#             (c) 2019, Philipp Winter
# :license: see LICENSE for licensing information
# _____________________________________________________________________________

"""API for keeping track of BridgeDB metrics, e.g., the demand for bridges
over time.
"""

import logging
import ipaddr
import operator
import json
import datetime
import statistics
import numpy

from bridgedb import geo
from bridgedb.distributors.common.http import getClientIP
from bridgedb.distributors.email import request

from twisted.mail.smtp import Address

# Our data structure to keep track of exit relays.  The variable is of type
# bridgedb.proxy.ProxySet.  We reserve a special country code (determined by
# PROXY_CC below) for exit relays and other proxies.
PROXIES = None

# Our custom country code for IP addresses that we couldn't map to a country.
# This can happen for private IP addresses or if our geo-location provider has
# no mapping.
UNKNOWN_CC = "??"

# Our custom country code for IP addresses that are proxies, e.g., Tor exit
# relays.  The code "zz" is free for assignment for user needs as specified
# here: <https://en.wikipedia.org/w/index.php?title=ISO_3166-1_alpha-2&oldid=906611218#Decoding_table>
PROXY_CC = "ZZ"

# We use BIN_SIZE to reduce the granularity of our counters.  We round up
# numbers to the next multiple of BIN_SIZE, e.g., 28 is rounded up to:
# 10 * 3 = 30.
BIN_SIZE = 10

# The prefix length that we use to keep track of the number of unique subnets
# we have seen HTTPS requests from.
SUBNET_CTR_PREFIX_LEN = 20

# All of the pluggable transports BridgeDB currently supports.
SUPPORTED_TRANSPORTS = None

# Version number for our metrics format.  We increment the version if our
# format changes.
METRICS_VERSION = 2


def setProxies(proxies):
    """Set the given proxies.

    :type proxies: :class:`~bridgedb.proxy.ProxySet`
    :param proxies: The container for the IP addresses of any currently
        known open proxies.
    """
    logging.debug("Setting %d proxies." % len(proxies))
    global PROXIES
    PROXIES = proxies


def setSupportedTransports(supportedTransports):
    """Set the given supported transports.

    :param dict supportedTransports: The transport types that BridgeDB
        currently supports.
    """

    logging.debug("Setting %d supported transports." %
                  len(supportedTransports))
    global SUPPORTED_TRANSPORTS
    SUPPORTED_TRANSPORTS = supportedTransports


def isBridgeTypeSupported(bridgeType):
    """Return `True' or `False' depending on if the given bridge type is
    supported.

    :param str bridgeType: The bridge type, e.g., "vanilla" or "obfs4".
    """

    if SUPPORTED_TRANSPORTS is None:
        logging.error("Bug: Variable SUPPORTED_TRANSPORTS is None.")
        return False

    # Note that "vanilla" isn't a transport protocol (in fact, it's the absence
    # of a transport), which is why it isn't in SUPPORTED_TRANSPORTS.
    return (bridgeType in SUPPORTED_TRANSPORTS) or (bridgeType == "vanilla")


def export(fh, measurementInterval):
    """Export metrics by writing them to the given file handle.

    :param file fh: The file handle to which we're writing our metrics.
    :param int measurementInterval: The number of seconds after which we rotate
        and dump our metrics.
    """

    metrics = [HTTPSMetrics(),
               EmailMetrics(),
               MoatMetrics(),
               InternalMetrics()]

    # Rotate our metrics.
    for m in metrics:
        m.rotate()

    numProxies = len(PROXIES) if PROXIES is not None else 0
    if numProxies == 0:
        logging.error("Metrics module doesn't have any proxies.")
    else:
        logging.debug("Metrics module knows about %d proxies." % numProxies)

    now = datetime.datetime.utcnow()
    fh.write("bridgedb-metrics-end %s (%d s)\n" % (
             now.strftime("%Y-%m-%d %H:%M:%S"),
             measurementInterval))
    fh.write("bridgedb-metrics-version %d\n" % METRICS_VERSION)

    for m in metrics:
        distLines = m.getMetrics()
        for line in distLines:
            fh.write("bridgedb-metric-count %s\n" % line)
            logging.debug("Writing metrics line to file: %s" % line)

    for m in metrics:
        m.reset()


def resolveCountryCode(ipAddr):
    """Return the country code of the given IP address.

    :param str ipAddr: The IP address to resolve.

    :rtype: str
    :returns: A two-letter country code.
    """

    if ipAddr is None:
        logging.warning("Given IP address was None.  Using %s as country "
                        "code." % UNKNOWN_CC)
        return UNKNOWN_CC

    if PROXIES is None:
        logging.warning("Proxies are not yet set.")
    elif ipAddr in PROXIES:
        return PROXY_CC

    countryCode = geo.getCountryCode(ipaddr.IPAddress(ipAddr))

    # countryCode may be None if GeoIP is unable to map an IP address to a
    # country.
    return UNKNOWN_CC if countryCode is None else countryCode


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args,
                                                                 **kwargs)
        return cls._instances[cls]

    def clear(cls):
        """Drop the instance (necessary for unit tests)."""
        try:
            del cls._instances[cls]
        except KeyError:
            pass


class Metrics(metaclass=Singleton):
    """Base class representing metrics.

    This class provides functionality that our three distribution mechanisms
    share.
    """

    def __init__(self, binSize=BIN_SIZE):
        logging.debug("Instantiating metrics class.")
        self.binSize = binSize

        # Metrics cover a 24 hour period.  To that end, we're maintaining two
        # data structures: our "hot" metrics are currently being populated
        # while our "cold" metrics are finished, and valid for 24 hours.  After
        # that, our hot metrics turn into cold metrics, and we start over.
        self.hotMetrics = dict()
        self.coldMetrics = dict()
        self.unsanitisedSet = set()

    def rotate(self):
        """Rotate our metrics."""

        self.coldMetrics = self.hotMetrics
        self.hotMetrics = dict()

    def findAnomaly(self, request):
        anomaly = "none"

        # TODO: Inspect email for traces of bots, Sherlock Homes-style!
        # See <https://bugs.torproject.org/9316#comment:19> for the rationale.
        # All classes that inherit from Metrics() should implement this method.

        return anomaly

    def doNotSanitise(self, key):
        """
        :param str key: A key that will not be sanitised when exporting our
            metrics.
        """
        self.unsanitisedSet.add(key)

    def getMetrics(self, sanitized=True):
        """Get our (sanitized) current metrics, one per line.

        Metrics are of the form:

            [
             "moat.obfs4.us.success.none 10",
             "https.vanilla.de.success.none 30",
             ...
            ]

        :param bool sanitized: ``True`` if the metrics must be sanitized.
        :rtype: list
        :returns: A list of metric lines.
        """
        lines = []
        for key, value in self.coldMetrics.items():

            # There's no need to sanitize internal metrics.
            if sanitized and not key in self.unsanitisedSet:
                # Round up our value to the nearest multiple of self.binSize to
                # reduce the accuracy of our real values.
                if (value % self.binSize) > 0:
                    value += self.binSize - (value % self.binSize)
            lines.append("%s %d" % (key, value))
        return lines

    def set(self, key, value):
        """Set the given key to the given value.

        :param str key: The time series key.
        :param int value: The time series value.
        """
        self.hotMetrics[key] = value

    def inc(self, key):
        """Increment the given key.

        :param str key: The time series key.
        """
        if key in self.hotMetrics:
            self.hotMetrics[key] += 1
        else:
            self.set(key, 1)

    def createKey(self, distMechanism, bridgeType, countryOrProvider,
                  success, anomaly):
        """Create and return a time series key.

        :param str distMechanism: A string representing our distribution
            mechanism, e.g., "https".
        :param str bridgeType: A string representing the requested bridge
            type, e.g., "vanilla" or "obfs4".
        :param str countryOrProvider: A string representing the client's
            two-letter country code or email provider, e.g., "it" or
            "yahoo.com".
        :param bool success: ``True`` if the request was successful and
            BridgeDB handed out a bridge; ``False`` otherwise.
        :param str anomaly: ``None`` if the request was not anomalous and hence
            believed to have come from a real user; otherwise a string
            representing the type of anomaly.
        :rtype: str
        :returns: A key that uniquely identifies the given metrics
            combinations.
        """

        if isinstance(countryOrProvider, bytes):
            countryOrProvider = countryOrProvider.decode('utf-8')

        countryOrProvider = countryOrProvider.lower()
        bridgeType = bridgeType.lower()
        success = "success" if success else "fail"

        key = "%s.%s.%s.%s.%s" % (distMechanism, bridgeType,
                                  countryOrProvider, success, anomaly)

        return key

    def reset(self):
        """Reset internal variables after a metrics interval."""
        pass


class InternalMetrics(Metrics):

    def __init__(self):
        super(InternalMetrics, self).__init__()
        self.keyPrefix = "internal"
        # Maps bridges to the number of time they have been handed out.
        self.bridgeHandouts = {}

        # There's no reason for the following metrics to be sanitised.
        handoutsPrefix = "{}.handouts".format(self.keyPrefix)
        self.doNotSanitise("{}.unique-bridges".format(handoutsPrefix))
        self.doNotSanitise("{}.median".format(handoutsPrefix))
        self.doNotSanitise("{}.min".format(handoutsPrefix))
        self.doNotSanitise("{}.max".format(handoutsPrefix))
        self.doNotSanitise("{}.quartile1".format(handoutsPrefix))
        self.doNotSanitise("{}.quartile3".format(handoutsPrefix))
        self.doNotSanitise("{}.lower-whisker".format(handoutsPrefix))
        self.doNotSanitise("{}.upper-whisker".format(handoutsPrefix))

    def reset(self):
        """Reset bridge handouts after each interval."""

        # Log the bridge that has seen the most handouts.  This helps us
        # understand BridgeDB better.
        items = self.bridgeHandouts.items()
        if len(items):
            bridgeLine, num = sorted(items, key=lambda x: x[1],
                                     reverse=True)[0]
            logging.debug("Bridge line with most handouts (%d): %s" %
                          (num, bridgeLine))

        self.bridgeHandouts = {}

    def _recordEmptyResponse(self, distributor):
        """
        Record an empty bridge request response for the given distributor.

        :param str distributor: A bridge distributor, e.g., "https".
        """
        self.inc("{}.{}.empty-response".format(self.keyPrefix, distributor))

    def recordEmptyEmailResponse(self):
        self._recordEmptyResponse("email")

    def recordEmptyHTTPSResponse(self):
        self._recordEmptyResponse("https")

    def recordEmptyMoatResponse(self):
        self._recordEmptyResponse("moat")

    def recordHandoutsPerBridge(self, bridgeRequest, bridges):
        """
        Record how often a given bridge was handed out.

        Note that bridges that were not handed out will not be part of these
        metrics.

        :type bridgeRequest: :api:`bridgerequest.BridgeRequestBase`
        :param bridgeRequest: A bridge request for either one of our
            distributors.
        :param list bridges: A list of :class:`~bridgedb.Bridges.Bridge`s.
        """

        handoutsPrefix = "{}.handouts".format(self.keyPrefix)

        if bridgeRequest is None or bridges is None:
            logging.warning("Given bridgeRequest and bridges cannot be None.")
            return

        # Keep track of how many IPv4 and IPv6 requests we are seeing.
        ipVersion = bridgeRequest.ipVersion
        if ipVersion not in [4, 6]:
            logging.warning("Got bridge request for unsupported IP version "
                            "{}.".format(ipVersion))
            return
        else:
            self.inc("{}.ipv{}".format(handoutsPrefix, ipVersion))

        # Keep track of how many times we're handing out a given bridge.
        for bridge in bridges:
            # Use bridge lines as dictionary key.  We cannot use the bridge
            # objects because BridgeDB reloads its descriptors every 30
            # minutes, at which points the bridge objects change.
            key = bridge.getBridgeLine(bridgeRequest)
            num = self.bridgeHandouts.get(key, None)
            if num is None:
                self.bridgeHandouts[key] = 1
            else:
                self.bridgeHandouts[key] = num + 1

        # We need more than two handouts to calculate our statistics.
        values = self.bridgeHandouts.values()
        if len(values) <= 2:
            return

        # Update our statistics.
        self.set("{}.median".format(handoutsPrefix),
                 statistics.median(values))
        self.set("{}.min".format(handoutsPrefix), min(values))
        self.set("{}.max".format(handoutsPrefix), max(values))
        self.set("{}.unique-bridges".format(handoutsPrefix),
                 len(self.bridgeHandouts))
        # Python 3.8 comes with a statistics.quantiles function, which we
        # should use instead of numpy once 3.8 is available in Debian stable.
        q1, q3 = numpy.quantile(numpy.array(list(values)), [0.25, 0.75])
        self.set("{}.quartile1".format(handoutsPrefix), q1)
        self.set("{}.quartile3".format(handoutsPrefix), q3)
        # Determine our inter-quartile range (the difference between quartile 3
        # and quartile 1) and use it to calculate the upper and lower whiskers
        # as you would see them in a boxplot.
        iqr = q3 - q1
        lowerWhisker = min([x for x in values if x >= q1 - (1.5 * iqr)])
        upperWhisker = max([x for x in values if x <= q3 + (1.5 * iqr)])
        self.set("{}.lower-whisker".format(handoutsPrefix), lowerWhisker)
        self.set("{}.upper-whisker".format(handoutsPrefix), upperWhisker)

    def recordBridgesInHashring(self, ringName, subRingName, numBridges):
        """
        Record the number of bridges per hashring.

        :param str ringName: The name of the ring, e.g., "https".
        :param str subRingName: The name of the subring, e.g.,
            "byIPv6-bySubring1of4".
        :param int numBridges: The number of bridges in the given subring.
        """

        if not ringName or not subRingName:
            logging.warning("Ring name ({}) and subring name ({}) cannot be "
                            "empty.".format(ringName, subRingName))
            return

        logging.info("Recording metrics for bridge (sub)rings: %s/%s/%d." %
                     (ringName, subRingName, numBridges))
        # E.g, concatenate "https" with "byipv6-bysubring1of4".
        key = "{}.{}.{}".format(self.keyPrefix, ringName, subRingName.lower())
        self.set(key, numBridges)


class HTTPSMetrics(Metrics):

    def __init__(self):
        super(HTTPSMetrics, self).__init__()

        # Maps subnets (e.g., "1.2.0.0/16") to the number of times we've seen
        # requests from the given subnet.
        self.subnetCounter = dict()
        self.keyPrefix = "https"

    def getTopNSubnets(self, n=10):

        sortedByNum = sorted(self.subnetCounter.items(),
                             key=operator.itemgetter(1),
                             reverse=True)
        return sortedByNum[:n]

    def _recordHTTPSRequest(self, request, success):

        logging.debug("HTTPS request has user agent: %s" %
                      request.requestHeaders.getRawHeaders("User-Agent"))

        # Pull the client's IP address out of the request and convert it to a
        # two-letter country code.
        ipAddr = getClientIP(request,
                             useForwardedHeader=True,
                             skipInvalid=True)
        self.updateSubnetCounter(ipAddr)
        countryCode = resolveCountryCode(ipAddr)

        transports = request.args.get("transport", list())
        if len(transports) > 1:
            logging.warning("Expected a maximum of one transport but %d are "
                            "given." % len(transports))

        if len(transports) == 0:
            bridgeType = "vanilla"
        elif transports[0] == "" or transports[0] == "0":
            bridgeType = "vanilla"
        else:
            bridgeType = transports[0]

        # BridgeDB's HTTPS interface exposes transport types as a drop down
        # menu but users can still request anything by manipulating HTTP
        # parameters.
        if not isBridgeTypeSupported(bridgeType):
            logging.warning("User requested unsupported transport type %s "
                            "over HTTPS." % bridgeType)
            return

        logging.debug("Recording %svalid HTTPS request for %s from %s (%s)." %
                      ("" if success else "in",
                       bridgeType, ipAddr, countryCode))

        # Now update our metrics.
        key = self.createKey(self.keyPrefix, bridgeType, countryCode,
                             success, self.findAnomaly(request))
        self.inc(key)

    def recordValidHTTPSRequest(self, request):
        self._recordHTTPSRequest(request, True)

    def recordInvalidHTTPSRequest(self, request):
        self._recordHTTPSRequest(request, False)

    def updateSubnetCounter(self, ipAddr):

        if ipAddr is None:
            return

        nw = ipaddr.IPNetwork(ipAddr + "/" + str(SUBNET_CTR_PREFIX_LEN),
                              strict=False)
        subnet = nw.network.compressed
        logging.debug("Updating subnet counter with %s" % subnet)

        num = self.subnetCounter.get(subnet, 0)
        self.subnetCounter[subnet] = num + 1


class EmailMetrics(Metrics):

    def __init__(self):
        super(EmailMetrics, self).__init__()
        self.keyPrefix = "email"

    def _recordEmailRequest(self, smtpAutoresp, success):

        emailAddrs = smtpAutoresp.getMailTo()
        if len(emailAddrs) == 0:
            # This is just for unit tests.
            emailAddr = Address("foo@gmail.com")
        else:
            emailAddr = emailAddrs[0]

        # Get the requested transport protocol.
        br = request.determineBridgeRequestOptions( smtpAutoresp.incoming.lines)
        bridgeType = "vanilla" if not len(br.transports) else br.transports[0]

        # Over email, transports are requested by typing them.  Typos happen
        # and users can request anything, really.
        if not isBridgeTypeSupported(bridgeType):
            logging.warning("User requested unsupported transport type %s "
                            "over email." % bridgeType)
            return

        logging.debug("Recording %svalid email request for %s from %s." %
                      ("" if success else "in", bridgeType, emailAddr))
        sld = emailAddr.domain.split(b".")[0]

        # Now update our metrics.
        key = self.createKey(self.keyPrefix, bridgeType, sld, success,
                             self.findAnomaly(request))
        self.inc(key)

    def recordValidEmailRequest(self, smtpAutoresp):
        self._recordEmailRequest(smtpAutoresp, True)

    def recordInvalidEmailRequest(self, smtpAutoresp):
        self._recordEmailRequest(smtpAutoresp, False)


class MoatMetrics(Metrics):

    def __init__(self):
        super(MoatMetrics, self).__init__()
        self.keyPrefix = "moat"

    def _recordMoatRequest(self, request, success):

        logging.debug("Moat request has user agent: %s" %
                      request.requestHeaders.getRawHeaders("User-Agent"))

        ipAddr = getClientIP(request,
                             useForwardedHeader=True,
                             skipInvalid=True)
        countryCode = resolveCountryCode(ipAddr)

        try:
            encodedClientData = request.content.read()
            clientData = json.loads(encodedClientData)["data"][0]
            transport = clientData["transport"]
            bridgeType = "vanilla" if not len(transport) else transport
        except Exception as err:
            logging.warning("Could not decode request: %s" % err)
            return

        if not isBridgeTypeSupported(bridgeType):
            logging.warning("User requested unsupported transport type %s "
                            "over moat." % bridgeType)
            return

        logging.debug("Recording %svalid moat request for %s from %s (%s)." %
                      ("" if success else "in",
                       bridgeType, ipAddr, countryCode))

        # Now update our metrics.
        key = self.createKey(self.keyPrefix, bridgeType,
                             countryCode, success, self.findAnomaly(request))
        self.inc(key)

    def recordValidMoatRequest(self, request):
        self._recordMoatRequest(request, True)

    def recordInvalidMoatRequest(self, request):
        self._recordMoatRequest(request, False)
