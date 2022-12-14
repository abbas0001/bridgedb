# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_Bridges -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: see AUTHORS file
# :copyright: (c) 2007-2017, The Tor Project, Inc.
# :license: 3-Clause BSD, see LICENSE for licensing information

"""This module has low-level functionality for parsing bridges and arranging
them into hashrings for distributors.
"""

import binascii
import bisect
import logging
import re
import hashlib
import socket
import time
import ipaddr
import random

import bridgedb.Storage

from bridgedb.bridges import Bridge
from bridgedb.crypto import getHMACFunc
from bridgedb.parse import addr
from bridgedb.parse.fingerprint import isValidFingerprint
from bridgedb.parse.fingerprint import toHex
from bridgedb.safelog import logSafely

ID_LEN = 20  # XXX Only used in commented out line in Storage.py
DIGEST_LEN = 20
PORTSPEC_LEN = 16


class BridgeRingParameters(object):
    """Store validated settings on minimum number of Bridges with certain
    attributes which should be included in any generated subring of a
    hashring.

    :ivar list needPorts: List of two-tuples of desired port numbers and their
        respective minimums.
    :ivar list needFlags: List of two-tuples of desired flags_ assigned to a
        Bridge by the Bridge DirAuth.

    .. _flags: https://gitweb.torproject.org/torspec.git/tree/dir-spec.txt?id=6b557594ef#n1695
    """

    def __init__(self, needPorts=[], needFlags=[]):
        """Control the creation of subrings by including a minimum number of
        bridges which possess certain attributes.

        :type needPorts: iterable
        :param needPorts: An iterable of two-tuples. Each two tuple should
            contain ``(port, minimum)``, where ``port`` is an integer
            specifying a port number, and ``minimum`` is another integer
            specifying the minimum number of Bridges running on that ``port``
            to include in any new subring.
        :type needFlags: iterable
        :param needFlags: An iterable of two-tuples. Each two tuple should
            contain ``(flag, minimum)``, where ``flag`` is a string specifying
            an OR flag_, and ``minimum`` is an integer for the minimum number
            of Bridges which have acquired that ``flag`` to include in any new
            subring.
        :raises: An :exc:`TypeError` if an invalid port number, a minimum less
            than one, or an "unsupported" flag is given. "Stable" appears to
            be the only currently "supported" flag.
        """
        for port, count in needPorts:
            if not (1 <= port <= 65535):
                raise TypeError("Port %s out of range." % port)
            if count <= 0:
                raise TypeError("Count %s out of range." % count)
        for flag, count in needFlags:
            flag = flag.lower()
            if flag not in ["stable", "running",]:
                raise TypeError("Unsupported flag %s" % flag)
            if count <= 0:
                raise TypeError("Count %s out of range." % count)

        self.needPorts = needPorts[:]
        self.needFlags = [(flag.lower(), count) for flag, count in needFlags[:]]


class BridgeRing(object):
    """Arranges bridges into a hashring based on an hmac function."""

    def __init__(self, key, answerParameters=None):
        """Create a new BridgeRing, using key as its hmac key.

        :type key: bytes
        :param key: The HMAC key, generated with
             :func:`~bridgedb.crypto.getKey`.
        :type answerParameters: :class:`BridgeRingParameters`
        :param answerParameters: DOCDOC
        :ivar dict bridges: A dictionary which maps HMAC keys to
            :class:`~bridgedb.bridges.Bridge`s.
        :ivar dict bridgesByID: A dictionary which maps raw hash digests of
            bridge ID keys to :class:`~bridgedb.bridges.Bridge`s.
        :type hmac: callable
        :ivar hmac: An HMAC function, which uses the **key** parameter to
             generate new HMACs for storing, inserting, and retrieving
             :class:`~bridgedb.bridges.Bridge`s within mappings.
        :ivar bool isSorted: ``True`` if ``sortedKeys`` is currently sorted.
        :ivar list sortedKeys: A sorted list of all of the HMACs.
        :ivar str name: A string which identifies this hashring, used mostly
            for differentiating this hashring in log messages, but it is also
            used for naming subrings. If this hashring is a subring, the
            ``name`` will include whatever distinguishing parameters
            differentiate that particular subring (i.e. ``'(port-443
            subring)'`` or ``'(Stable subring)'``)
        :type subrings: list
        :ivar subrings: A list of other ``BridgeRing``s, each of which
            contains bridges of a particular type. For example, a subring
            might contain only ``Bridge``s which have been given the "Stable"
            flag, or it might contain only IPv6 bridges. Each item in this
            list should be a 4-tuple::

                (type, value, count, ring)

            where:

              - ``type`` is a string which describes what kind of parameter is
                used to determine if a ``Bridge`` belongs in that subring,
                i.e. ``'port'`` or ``'flag'``.

              - ``value`` is a specific value pertaining to the ``type``,
                e.g. ``type='port'; value=443``.

              - ``count`` is an integer for the current total number of
                bridges in the subring.

              - ``ring`` is a :class:`BridgeRing`; it is the subhashring which
                contains ``count`` number of
                :class:`~bridgedb.bridges.Bridge`s of a certain ``type``.
        """
        self.bridges = {}
        self.bridgesByID = {}
        self.hmac = getHMACFunc(key, hex=False)
        self.isSorted = False
        self.sortedKeys = []
        if answerParameters is None:
            answerParameters = BridgeRingParameters()
        self.answerParameters = answerParameters

        self.subrings = []
        for port,count in self.answerParameters.needPorts:
            #note that we really need to use the same key here, so that
            # the mapping is in the same order for all subrings.
            self.subrings.append( ('port',port,count,BridgeRing(key,None)) )
        for flag,count in self.answerParameters.needFlags:
            self.subrings.append( ('flag',flag,count,BridgeRing(key,None)) )

        self.setName("Ring")

    def setName(self, name):
        """Tag a unique name to this hashring for identification.

        :param string name: The name for this hashring.
        """
        self.name = name
        for tp, val, _, subring in self.subrings:
            if tp == 'port':
                subring.setName("%s (port-%s subring)" % (name, val))
            else:
                subring.setName("%s (%s subring)" % (name, val))

    def __len__(self):
        """Get the number of unique bridges this hashring contains."""
        return len(self.bridges)

    def clear(self):
        """Remove all bridges and mappings from this hashring and subrings."""
        self.bridges = {}
        self.bridgesByID = {}
        self.isSorted = False
        self.sortedKeys = []

        for tp, val, count, subring in self.subrings:
            subring.clear()

    def remove(self, bridge):
        """Remove a **bridge** from this hashring."""
        for tp, val, _, subring in self.subrings:
            subring.remove(bridge)
        pos = self.hmac(bridge.identity)
        if pos in self.bridges:
            del self.bridges[pos]

    def insert(self, bridge):
        """Add a **bridge** to this hashring.

        The bridge's position in the hashring is dependent upon the HMAC of
        the raw hash digest of the bridge's ID key. The function used to
        generate the HMAC, :ivar:`BridgeRing.hmac`, is unique to each
        individual hashring.

        If the (presumably same) bridge is already at that determined position
        in this hashring, replace the old one.

        :type bridge: :class:`~bridgedb.bridges.Bridge`
        :param bridge: The bridge to insert into this hashring.
        """
        for tp, val, _, subring in self.subrings:
            if tp == 'port':
                if val == bridge.orPort:
                    subring.insert(bridge)
            else:
                assert tp == 'flag' and val == 'stable'
                if val == 'stable' and bridge.flags.stable:
                    subring.insert(bridge)

        pos = self.hmac(bridge.identity)
        if not pos in self.bridges:
            self.sortedKeys.append(pos)
            self.isSorted = False
        self.bridges[pos] = bridge
        self.bridgesByID[bridge.identity] = bridge
        logging.debug("Adding %s to %s" % (bridge.address, self.name))

    def _sort(self):
        """Helper: put the keys in sorted order."""
        if not self.isSorted:
            self.sortedKeys.sort()
            self.isSorted = True

    def _getBridgeKeysAt(self, pos, N=1):
        """Bisect a list of bridges at a specified position, **pos**, and
        retrieve bridges from that point onwards, wrapping around the hashring
        if necessary.

        If the number of bridges requested, **N**, is larger that the size of
        this hashring, return the entire ring. Otherwise:

          1. Sort this bridges in this hashring, if it is currently unsorted.

          2. Bisect the sorted bridges. If the bridge at the desired position,
             **pos**, already exists within this hashring, the the bisection
             result is the bridge at position **pos**. Otherwise, the bisection
             result is the first position after **pos** which has a bridge
             assigned to it.

          3. Try to obtain **N** bridges, starting at (and including) the
             bridge in the requested position, **pos**.

               a. If there aren't **N** bridges after **pos**, wrap back
                  around to the beginning of the hashring and obtain bridges
                  until we have **N** bridges.

          4. Check that the number of bridges obtained is indeed **N**, then
             return them.

        :param bytes pos: The position to jump to. Any bridges returned will
            start at this position in the hashring, if there is a bridge
            assigned to that position. Otherwise, indexing will start at the
            first position after this one which has a bridge assigned to it.
        :param int N: The number of bridges to return.
        :rtype: list
        :returns: A list of :class:`~bridgedb.bridges.Bridge`s.
        """
        assert len(pos) == DIGEST_LEN
        if N >= len(self.sortedKeys):
            return self.sortedKeys
        if not self.isSorted:
            self._sort()
        idx = bisect.bisect_left(self.sortedKeys, pos)
        r = self.sortedKeys[idx:idx+N]
        if len(r) < N:
            # wrap around as needed.
            r.extend(self.sortedKeys[:N - len(r)])
        assert len(r) == N
        return r

    def filterDistinctSubnets(self, fingerprints):
        """Given a chosen set of ``fingerprints`` of bridges to distribute,
        filter the bridges such that they are in distinct subnets.
        """
        logging.debug("Got %d possible bridges to filter" % len(fingerprints))

        bridges = []
        subnets = []

        for fingerprint in fingerprints:
            bridge = self.bridges[fingerprint]
            jump = False

            # HOTFIX for https://bugs.torproject.org/26150
            if not bridge.address:
                logging.error("Got strange bridge with no address field set: %s"
                              % toHex(fingerprint))
                continue

            for subnet in subnets:
                if bridge.address in subnet:
                    jump = True
                    logging.debug(
                        ("Skipping distribution of bridge %s in a subnet which "
                         "contains another bridge we're already distributing")
                        % bridge)
                    break
            if jump:
                continue

            bridges.append(bridge)
            if bridge.address.version == 4:
                cidr = str(bridge.address) + "/16"
            else:
                cidr = str(bridge.address) + "/32"
            subnets.append(ipaddr.IPNetwork(cidr))

        return bridges

    def getBridges(self, pos, N=1, filterBySubnet=False):
        """Return **N** bridges appearing in this hashring after a position.

        :param bytes pos: The position to jump to. Any bridges returned will
            start at this position in the hashring, if there is a bridge
            assigned to that position. Otherwise, indexing will start at the
            first position after this one which has a bridge assigned to it.
        :param int N: The number of bridges to return.
        :rtype: list
        :returns: A list of :class:`~bridgedb.bridges.Bridge`s.
        """
        forced = []
        for _, _, count, subring in self.subrings:
            if len(subring) < count:
                count = len(subring)
            forced.extend(subring._getBridgeKeysAt(pos, count))

        keys = []

        # Oversample double the number we need, in case we need to
        # filter them and some are within the same subnet.
        for k in forced + self._getBridgeKeysAt(pos, N + N):
            if k not in keys:
                keys.append(k)
            else:
                logging.debug(
                    "Got duplicate bridge %r in main hashring for position %r."
                    % (logSafely(binascii.hexlify(k).decode('utf-8')), binascii.hexlify(pos).decode('utf-8')))
        keys.sort()

        if filterBySubnet:
            bridges = self.filterDistinctSubnets(keys)
        else:
            bridges = [self.bridges[k] for k in keys]

        bridges = bridges[:N]
        logging.debug("Caller asked for N=%d, filterBySubnet=%s bridges.  "
                      "Returning %d bridges." % (N, filterBySubnet, len(bridges)))

        return bridges

    def getBridgeByID(self, fp):
        """Return the bridge whose identity digest is fp, or None if no such
           bridge exists."""
        for _,_,_,subring in self.subrings:
            b = subring.getBridgeByID(fp)
            if b is not None:
                return b

        return self.bridgesByID.get(fp)

    def dumpAssignments(self, f, description=""):
        logging.info("Dumping bridge assignments for %s..." % self.name)
        for b in self.bridges.values():
            desc = [ description ]
            for tp,val,_,subring in self.subrings:
                if subring.getBridgeByID(b.identity):
                    desc.append("%s=%s"%(tp,val))
            f.write("%s %s\n" % (b.fingerprint, " ".join(desc).strip()))


class UnallocatedHolder(object):
    """A pseudo-bridgeholder that ignores its bridges and leaves them
       unassigned.
    """
    def __init__(self):
        self.fingerprints = []

    def remove(self, bridge):
        logging.debug("Removing %s from unallocated" % bridge.fingerprint)
        i = -1
        try:
            i = self.fingerprints.index(bridge.fingerprint)
        except ValueError:
            return
        del self.fingerprints[i]

    def insert(self, bridge):
        logging.debug("Leaving %s unallocated" % bridge.fingerprint)
        if not bridge.fingerprint in self.fingerprints:
            self.fingerprints.append(bridge.fingerprint)

    def __len__(self):
        return len(self.fingerprints)

    def clear(self):
        self.fingerprints = []

    def dumpAssignments(self, f, description=""):
        with bridgedb.Storage.getDB() as db:
            allBridges = db.getAllBridges()
            for bridge in allBridges:
                if bridge.hex_key not in self.fingerprints:
                    continue
                dist = bridge.distributor
                desc = [ description ]
                if dist != "unallocated":
                    continue
                f.write("%s %s\n" % (bridge.hex_key, " ".join(desc).strip()))


class BridgeSplitter(object):
    """Splits incoming bridges up based on an HMAC, and assigns them to
    sub-bridgeholders with different probabilities.  Bridge ?????? BridgeSplitter
    associations are recorded in a store.
    """
    def __init__(self, key):
        self.hmac = getHMACFunc(key, hex=True)
        self.ringsByName = {}
        self.totalP = 0
        self.pValues = []
        self.rings = []
        self.statsHolders = []

    def __len__(self):
        n = 0
        for r in self.ringsByName.values():
            n += len(r)
        return n

    def addRing(self, ring, ringname, p=1):
        """Add a new subring.

        :param ring: The subring to add.
        :param str ringname: This is used to record which bridges have been
            assigned where in the store.
        :param int p: The relative proportion of bridges to assign to this
            bridgeholder.
        """
        self.ringsByName[ringname] = ring
        self.pValues.append(self.totalP)
        self.rings.append(ringname)
        self.totalP += p

    def addTracker(self, t):
        """Adds a statistics tracker that gets told about every bridge we see.
        """
        self.statsHolders.append(t)

    def clear(self):
        for r in self.ringsByName.values():
            r.clear()

    def insert(self, bridge):
        assert self.rings

        for s in self.statsHolders:
            s.insert(bridge)

        # The bridge must be running to insert it:
        if not bridge.flags.running:
            return

        validRings = self.rings
        distribution_method = orig_method = None

        with bridgedb.Storage.getDB() as db:
            orig_method = db.getBridgeDistributor(bridge, validRings)
            if orig_method is not None:
                distribution_method = orig_method
                logging.info("So far, bridge %s was in hashring %s" %
                             (bridge, orig_method))

        # Check if the bridge requested a distribution method and if so, try to
        # use it.
        if bridge.distribution_request:
            distribution_method = bridge.distribution_request
            logging.info("Bridge %s requested placement in hashring %s" %
                         (bridge, distribution_method))

        # Is the bridge requesting a distribution method that's different
        # from the one we have on record?  If so, we have to delete it from
        # its previous ring.
        if orig_method is not None and \
           orig_method != distribution_method and \
           distribution_method in (validRings + ["none"]):
            logging.info("Bridge %s is in %s but wants to be in %s." %
                            (bridge, orig_method, distribution_method))
            prevRing = self.ringsByName.get(orig_method)
            prevRing.remove(bridge)

        # If they requested not to be distributed, honor the request:
        if distribution_method == "none":
            logging.info("Bridge %s requested to not be distributed." % bridge)
            return

        # If we didn't know what they are talking about, or they requested
        # "any" distribution method, and we've never seen this bridge
        # before, then deterministically determine where to place it.
        if ((distribution_method not in validRings) or
            (distribution_method == "any")):

            pos = self.hmac(bridge.identity)
            n = int(pos[:8], 16) % self.totalP
            pos = bisect.bisect_right(self.pValues, n) - 1
            assert 0 <= pos < len(self.rings)
            distribution_method = self.rings[pos]
            logging.info(("%s placing bridge %s into hashring %s (via n=%s,"
                            " pos=%s).") % (self.__class__.__name__, bridge,
                                            distribution_method, n, pos))

        with bridgedb.Storage.getDB() as db:
            ringname = db.insertBridgeAndGetRing(bridge, distribution_method,
                                                 time.time(), validRings)
            db.commit()

        ring = self.ringsByName.get(ringname)
        ring.insert(bridge)

        if ring is None:
            logging.warn("Couldn't recognise ring named: '%s'" % ringname)
            logging.info("Current rings: %s" % " ".join(self.ringsByName))

    def dumpAssignments(self, f, description=""):
        for name,ring in self.ringsByName.items():
            ring.dumpAssignments(f, "%s %s" % (description, name))


class FilteredBridgeSplitter(object):
    """Places bridges into subrings based upon sets of filters.

    The set of subrings and conditions used to assign :class:`Bridge`s should
    be passed to :meth:`~FilteredBridgeSplitter.addRing`.
    """

    def __init__(self, key, max_cached_rings=3):
        """Create a hashring which filters bridges into sub hashrings.

        :type key: DOCDOC
        :param key: An HMAC key.
        :param int max_cached_rings: XXX max_cached_rings appears to not be
             used anywhere.

        :ivar filterRings: A dictionary of subrings which has the form
             ``{ringname: (filterFn, subring)}``, where:
                 - ``ringname`` is a unique string identifying the subring.
                 - ``filterFn`` is a callable which filters Bridges in some
                   manner, i.e. by whether they are IPv4 or IPv6, etc.
                 - ``subring`` is any of the horribly-implemented,
                   I-guess-it-passes-for-some-sort-of-hashring classes in this
                   module.
        :ivar hmac: DOCDOC
        :ivar bridges: A dictionary mapping a bridge's fingerprint to its
             :class:`~bridgedb.bridges.Bridge` object.
        :type distributorName: str
        :ivar distributorName: The name of this splitter's distributor. See
             :meth:`~bridgedb.distributors.https.distributor.HTTPSDistributor.setDistributorName`.
        """
        self.key = key
        self.filterRings = {}
        self.hmac = getHMACFunc(key, hex=True)
        self.bridges = {}
        self.distributorName = ''

        #XXX: unused
        self.max_cached_rings = max_cached_rings

    def __len__(self):
        return len(self.bridges)

    def clear(self):
        self.bridges = {}
        self.filterRings = {}

    def remove(self, bridge):
        """Remove a bridge from all appropriate sub-hashrings.

        :type bridge: :class:`~bridgedb.bridges.Bridge`
        :param bridge: The bridge to remove.
        """
        logging.debug("Removing %s from hashring..." % bridge)

        try:
            del self.bridges[bridge.fingerprint]
        except KeyError:
            logging.warn("Was asked to remove non-existant bridge %s "
                         "from ring." % bridge)
            return

        for ringname, (filterFn, subring) in self.filterRings.items():
            if filterFn(bridge):
                subring.remove(bridge)
                logging.debug("Removed bridge %s from %s subhashring." %
                              (bridge, ringname))

    def insert(self, bridge):
        """Insert a bridge into all appropriate sub-hashrings.

        For all sub-hashrings, the ``bridge`` will only be added iff it passes
        the filter functions for that sub-hashring.

        :type bridge: :class:`~bridgedb.bridges.Bridge`
        :param bridge: The bridge to add.
        """
        # The bridge must be running to insert it:
        if not bridge.flags.running:
            logging.warn(("Skipping hashring insertion for non-running "
                          "bridge: %s") % bridge)
            return

        logging.debug("Inserting %s into hashring..." % bridge)
        self.bridges[bridge.fingerprint] = bridge

        for ringname, (filterFn, subring) in self.filterRings.items():
            if filterFn(bridge):
                subring.insert(bridge)
                logging.debug("Inserted bridge %s into %s subhashring." %
                              (bridge, ringname))

    def extractFilterNames(self, ringname):
        """Get the names of the filters applied to a particular sub hashring.

        :param str ringname: A unique name identifying a sub hashring.
        :rtype: list
        :returns: A sorted list of strings, all the function names of the
            filters applied to the sub hashring named **ringname**.
        """
        filterNames = []

        for filterName in [x.__name__ for x in list(ringname)]:
            # Using `assignBridgesToSubring.__name__` gives us a messy
            # string which includes all parameters and memory addresses. Get
            # rid of this by partitioning at the first `(`:
            realFilterName = filterName.partition('(')[0]
            filterNames.append(realFilterName)

        filterNames.sort()
        return filterNames

    def addRing(self, subring, ringname, filterFn, populate_from=None):
        """Add a subring to this hashring.

        :param subring: The subring to add.
        :param str ringname: A unique name for identifying the new subring.
        :param filterFn: A function whose input is a :class:`Bridge`, and
            returns True/False based on some filtration criteria.
        :type populate_from: iterable or None
        :param populate_from: A group of :class:`Bridge`s. If given, the newly
            added subring will be populated with these bridges.
        :rtype: bool
        :returns: False if there was a problem adding the subring, True
            otherwise.
        """
        # XXX I think subring and ringname are switched in this function, or
        # at least that whatever is passed into this function as as the
        # `ringname` parameter from somewhere else is odd; for example, with
        # the original code, which was `log.debug("Inserted %d bridges into
        # hashring '%s'!" % (inserted, ringname))`, this log message appears:
        #
        # Jan 04 23:18:37 [INFO] Inserted 12 bridges into hashring
        # frozenset([<function byIPv4 at 0x2d67cf8>, <function
        # assignBridgesToSubring(<function hmac_fn at 0x3778398>, 4, 0) at
        # 0x37de578>])!
        #
        # I suppose since it contains memory addresses, it *is* technically
        # likely to be a unique string, but it is messy.

        if ringname in self.filterRings.keys():
            logging.fatal("%s hashring already has a subring named '%s'!"
                          % (self.distributorName, ringname))
            return False

        filterNames = self.extractFilterNames(ringname)
        subringName = [self.distributorName]
        subringNumber = None
        for filterName in filterNames:
            if filterName.startswith('assignBridgesToSubring'):
                subringNumber = filterName.lstrip('assignBridgesToSubring')
            else:
                subringName.append(filterName.lstrip('by'))
        if subring.name and 'Proxy' in subring.name:
            subringName.append('Proxy')
        elif subringNumber:
            subringName.append(subringNumber)
        subringName = '-'.join([x for x in subringName])
        subring.setName(subringName)

        logging.info("Adding %s subring %s to the %s Distributor's hashring..." %
                     (subring.name, subringNumber, self.distributorName))
        logging.info("  Subring filters: %s" % filterNames)

        #TODO: drop LRU ring if len(self.filterRings) > self.max_cached_rings
        self.filterRings[ringname] = (filterFn, subring)

        if populate_from:
            inserted = 0
            for bridge in populate_from.values():
                if isinstance(bridge, Bridge) and filterFn(bridge):
                    subring.insert(bridge)
                    inserted += 1
            logging.info("Bridges inserted into %s subring %s: %d"
                         % (subring.name, subringNumber, inserted))

        return True

    def dumpAssignments(self, f, description=""):
        # one ring per filter set
        # bridges may be present in multiple filter sets
        # only one line should be dumped per bridge

        for b in self.bridges.values():
            # gather all the filter descriptions
            desc = []
            for n,(g,r) in self.filterRings.items():
                if g(b):
                    # ghetto. get subring flags, ports
                    for tp,val,_,subring in r.subrings:
                        if subring.getBridgeByID(b.identity):
                            desc.append("%s=%s"%(tp,val))
                    try:
                        desc.extend(g.description.split())
                    except TypeError:
                        desc.append(g.description)

            # add transports
            logging.debug("%s supports %d transports" % (b, len(b.transports)))
            for transport in b.transports:
                desc.append("transport=%s"%(transport.methodname))

            # dedupe and group
            desc = set(desc)
            grouped = dict()
            for kw in desc:
                l,r = kw.split('=')
                try:
                    grouped[l] = "%s,%s"%(grouped[l],r)
                except KeyError:
                    grouped[l] = kw

            # add to assignments
            desc = "%s %s" % (description.strip(),
                    " ".join([v for k,v in grouped.items()]).strip())
            f.write("%s %s\n" % (b.fingerprint, desc))
