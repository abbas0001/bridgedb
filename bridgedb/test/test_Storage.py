#!/usr/bin/env python3
"""Unittests for the :mod:`bridgedb.Storage` module."""

import os
import threading
import time
import datetime

from twisted.python import log
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.threads import deferToThread

import bridgedb.Storage as Storage
import bridgedb.main as main
from bridgedb.bridges import Bridge

from bridgedb.test.util import generateFakeBridges

class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.fakeBridges = generateFakeBridges()
        self.validRings = ['https', 'unallocated', 'email', 'moat']
        self.dbfname = 'test-bridgedb.sqlite'
        Storage.setDBFilename(self.dbfname)

    def tearDown(self):
        if os.path.isfile(self.dbfname):
            os.unlink(self.dbfname)
        Storage.clearGlobalDB()

    def _runAndDie(self, timeout, func):
        with func():
            time.sleep(timeout)

    def _cb_assertTrue(self, result):
        self.assertTrue(result)

    def _cb_assertFalse(self, result):
        self.assertFalse(result)

    def _eb_Failure(self, failure):
        self.fail(failure)

    def test_getDB_FalseWhenLocked(self):
        Storage._LOCK = threading.Lock()
        Storage._LOCK.acquire()
        self.assertFalse(Storage._LOCK.acquire(False))

    def test_getDB_AcquireLock(self):
        Storage.initializeDBLock()
        with Storage.getDB() as db:
            self.assertIsInstance(db, Storage.Database)
            self.assertTrue(Storage.dbIsLocked())
            self.assertEqual(db, Storage._OPENED_DB)

    def test_getDB_ConcurrencyLock(self):
        timeout = 1
        d1 = deferToThread(self._runAndDie, timeout, Storage.getDB)
        d1.addCallback(self._cb_assertFalse)
        d1.addErrback(self._eb_Failure)
        d2 = deferToThread(Storage.getDB, False)
        d2.addCallback(self._cb_assertFalse)
        d2.addErrback(self._eb_Failure)
        d2.addCallback(self._cb_assertTrue, Storage.getDB(False))

    def test_insertBridgeAndGetRing_new_bridge(self):
        bridge = self.fakeBridges[0]
        Storage.initializeDBLock()
        with Storage.getDB() as db:
            ringname = db.insertBridgeAndGetRing(bridge, 'moat',
                                                 time.time(),
                                                 self.validRings)
            self.assertIn(ringname, self.validRings)

    def test_insertBridgeAndGetRing_already_seen_bridge(self):
        bridge = self.fakeBridges[0]
        Storage.initializeDBLock()
        with Storage.getDB() as db:
            ringname = db.insertBridgeAndGetRing(bridge, 'moat',
                                                 time.time(),
                                                 self.validRings)
            self.assertIn(ringname, self.validRings)
            ringname = db.insertBridgeAndGetRing(bridge, 'https',
                                                 time.time(),
                                                 self.validRings)
            self.assertIn(ringname, self.validRings)
            self.assertEqual(ringname, 'https')

    def test_getBridgeDistributor_recognised(self):
        bridge = self.fakeBridges[0]
        Storage.initializeDBLock()
        with Storage.getDB() as db:
            ringname = db.insertBridgeAndGetRing(bridge, 'moat',
                                                 time.time(),
                                                 self.validRings)
            self.assertIn(ringname, self.validRings)
            self.assertEqual(ringname, 'moat')
            db.commit()

        with Storage.getDB() as db:
            ringname = db.getBridgeDistributor(bridge, self.validRings)
            self.assertEqual(ringname, 'moat')

    def test_getBridgeDistributor_unrecognised(self):
        bridge = self.fakeBridges[0]
        Storage.initializeDBLock()
        with Storage.getDB() as db:
            ringname = db.insertBridgeAndGetRing(bridge, 'godzilla',
                                                 time.time(),
                                                 self.validRings)
            self.assertIn(ringname, self.validRings)
            self.assertEqual(ringname, "unallocated")
            db.commit()

        with Storage.getDB() as db:
            ringname = db.getBridgeDistributor(bridge, self.validRings)
            self.assertEqual(ringname, "unallocated")

    def test_BridgeMeasurementComparison(self):
        m1 = Storage.BridgeMeasurement(0, "", "", "", "", "", "", "",
                                       "2020-06-17", 0)
        m2 = Storage.BridgeMeasurement(0, "", "", "", "", "", "", "",
                                       "2020-06-18", 0)
        self.assertTrue(m2.newerThan(m1))
        self.assertFalse(m1.newerThan(m2))
        self.assertFalse(m1.newerThan(m1))

    def test_BridgeMeasurementCompact(self):
        m = Storage.BridgeMeasurement(0, "FINGERPRINT", "obfs4", "1.2.3.4",
                                      "1234", "ru", "1234", "ooni",
                                      "2020-06-17", 0)
        self.assertEquals(m.compact(), ("ru", "1.2.3.4", "1234"))

    def test_fetchBridgeMeasurements(self):

        query = "INSERT INTO BridgeMeasurements (hex_key, bridge_type, " \
                "address, port, blocking_country, blocking_asn, " \
                "measured_by, last_measured, verdict) VALUES ('key', " \
                "'obfs4', '1.2.3.4', '1234', 'RU', '1234', 'OONI', '%s', 1)"
        oldMsmt = query % "2017-01-01"
        newMsmt = query % datetime.datetime.utcnow().strftime("%Y-%m-%d")

        Storage.initializeDBLock()
        with Storage.getDB() as db:
            db._cur.execute(oldMsmt)
            # We're calling _Database__fetchBridgeMeasurements instead of
            # __fetchBridgeMeasurements to account for Python's name meddling.
            rows = db._Database__fetchBridgeMeasurements()
            # Outdated measurements should not be returned.
            self.assertEquals(len(rows), 0)

            db._cur.execute(newMsmt)
            rows = db._Database__fetchBridgeMeasurements()
            # Measurements that are "young enough" should be returned.
            self.assertEquals(len(rows), 1)

    def test_getBlockedBridgesFromSql(self):

        elems = [(0, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-17",
                  Storage.BRIDGE_BLOCKED),
                 (1, "1111111111111111111111111111111111111111", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_BLOCKED),
                 (2, "1111111111111111111111111111111111111111", "obfs4",
                  "1.2.3.4", "4321", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_BLOCKED),
                 (3, "1111111111111111111111111111111111111111", "obfs4",
                  "1.2.3.4", "4321", "ru", "4321", "ooni", "2020-05-01",
                  Storage.BRIDGE_REACHABLE)]
        b = Storage.getBlockedBridgesFromSql(elems)
        self.assertEqual(b, {"0000000000000000000000000000000000000000":
                             [("ru", "1.2.3.4", "1234")],
                             "1111111111111111111111111111111111111111":
                             [("ru", "1.2.3.4", "1234"),
                              ("ru", "1.2.3.4", "4321")]})

        # If multiple measurements disagree, we believe the newest one.
        elems = [(0, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-17",
                  Storage.BRIDGE_BLOCKED),
                 (1, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_REACHABLE)]
        b = Storage.getBlockedBridgesFromSql(elems)
        self.assertEqual(b, {"0000000000000000000000000000000000000000":
                             [("ru", "1.2.3.4", "1234")]})

        elems = [(0, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_BLOCKED),
                 (1, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-17",
                  Storage.BRIDGE_REACHABLE)]
        b = Storage.getBlockedBridgesFromSql(elems)
        self.assertTrue(len(b) == 0)

        # Element ordering must not affect the outcome.
        elems = [(1, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-17",
                  Storage.BRIDGE_REACHABLE),
                 (0, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_BLOCKED)]
        b = Storage.getBlockedBridgesFromSql(elems)
        self.assertTrue(len(b) == 0)

        # Redundant measurements should be discarded.
        elems = [(1, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-17",
                  Storage.BRIDGE_BLOCKED),
                 (0, "0000000000000000000000000000000000000000", "obfs4",
                  "1.2.3.4", "1234", "ru", "4321", "ooni", "2020-06-01",
                  Storage.BRIDGE_BLOCKED)]
        b = Storage.getBlockedBridgesFromSql(elems)
        self.assertTrue(len(b) == 1)
