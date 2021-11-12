#
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: see AUTHORS file
# :copyright: (c) 2007-2017, The Tor Project, Inc.
# :license: 3-Clause BSD, see LICENSE for licensing information

"""
Boilerplate setup for GeoIP. GeoIP allows us to look up the country code
associated with an IP address. This is a "pure" python version which interacts
with Tor GeoIP DB. It requires, in Debian, the tor-geoipdb package.
"""

import logging
from os.path import isfile

from ipaddr import IPv4Address, IPv6Address

# IPv4 database
GEOIP_DBFILE = '/usr/share/tor/geoip'
# IPv6 database
GEOIPv6_DBFILE = '/usr/share/tor/geoip6'


def loadFromGeoIPDB(filepath):
    """Load entries from IPV4 or IPV6 Tor Geo IP DB Files.

    :param str filepath: Path to Tor GeoIP DB file.
    :rtype: ``None`` or list

    :returns: Returns a table containing all entries from Tor Geo IP DB file.
    """
    parsedTable = []
    with open(filepath) as fd:
        rawData = fd.read()

    splitLines = rawData.split('\n')
    for line in splitLines[:-1]:
        if line.startswith('#'):
            continue
        singleRecord = line.split(',')
        singleRecord[0] = int(singleRecord[0])
        singleRecord[1] = int(singleRecord[1])
        parsedTable.append(singleRecord)

    return parsedTable


def loadFromGeoIPDB6(filepath):
    """Load entries from IPV4 or IPV6 Tor Geo IP DB Files.

    :param str filepath: Path to Tor GeoIP DB file.
    :rtype: ``None`` or list

    :returns: Returns a table containing all entries from Tor Geo IP DB file.
    """
    parsedTable = []
    with open(filepath) as fd:
        rawData = fd.read()

    splitLines = rawData.split('\n')
    for line in splitLines[:-1]:
        if line.startswith('#'):
            continue
        singleRecord = line.split(',')
        singleRecord[0] = int(IPv6Address(singleRecord[0]))
        singleRecord[1] = int(IPv6Address(singleRecord[1]))
        parsedTable.append(singleRecord)

    return parsedTable


try:
    # Make sure we have the database before trying to import the module:
    if not (isfile(GEOIP_DBFILE) and isfile(GEOIPv6_DBFILE)):
        # pragma: no cover
        raise EnvironmentError("Could not find %r. On Debian-based systems, "
                               "please install the tor-geoipdb package."
                               % GEOIP_DBFILE)

    geoip = loadFromGeoIPDB(GEOIP_DBFILE)
    geoipv6 = loadFromGeoIPDB6(GEOIPv6_DBFILE)
    logging.info("GeoIP databases loaded")
except Exception as err:  # pragma: no cover
    logging.warn("Error while loading data from GeoIP Database: %r" % err)
    geoip = None
    geoipv6 = None


def countryCodeByAddress(table, addr):
    """Lookup Country Code in Geo IP tables.

    :param list table: Contains list of IP Address ranges and mapped countries
    :type addr: :class:`ipaddr.IPAddress`
    :param addr: An IPv4 OR IPv6 address.
    :rtype: ``None`` or str

    :returns: If the GeoIP databases are loaded, and the **ip** lookup is
        successful, then this returns a two-letter country code.  Otherwise,
        this returns ``None``.
    """
    if addr.is_loopback:
        return None

    addrLong = int(addr)

    # First find the range_end which is greater than input.
    # Then if input lies in that range we return the country.
    for item in table:
        if item[1] >= addrLong:
            if item[0] <= addrLong:
                return item[2]
            else:
                return None

    return None


def getCountryCode(ip):
    """Return the two-letter country code of a given IP address.

    :type ip: :class:`ipaddr.IPAddress`
    :param ip: An IPv4 OR IPv6 address.
    :rtype: ``None`` or str

    :returns: If the GeoIP databases are loaded, and the **ip** lookup is
        successful, then this returns a two-letter country code.  Otherwise,
        this returns ``None``.
    """
    addr = None
    version = None
    try:
        addr = ip.compressed
        version = ip.version
    except AttributeError as err:
        logging.warn("Wrong type passed to getCountryCode: %s" % str(err))
        return None

    # GeoIP has two databases: one for IPv4 addresses, and one for IPv6
    # addresses. This will ensure we use the correct one.
    db = None
    # First, make sure we loaded GeoIP properly.
    if None in (geoip, geoipv6):
        logging.warn("GeoIP databases not loaded; can't look up country code")
        return None
    else:
        if version == 4:
            db = geoip
        else:
            db = geoipv6

    # Look up the country code of the address.
    countryCode = countryCodeByAddress(db, ip)
    if countryCode:
        logging.debug("Looked up country code: %s" % countryCode)
        return countryCode
    else:
        logging.debug("Country code was not detected. IP: %s" % addr)
        return None
