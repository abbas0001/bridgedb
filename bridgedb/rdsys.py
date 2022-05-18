import json
import logging
from io import BytesIO
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol
from twisted.web.client import Agent, FileBodyProducer
from twisted.web.http_headers import Headers

from bridgedb import metrics
from bridgedb.bridges import Bridge, MalformedBridgeInfo


inter_message_delimiter = b"\r"
max_retry_delay = 60*60  # 1 hour


class RdsysProtocol(Protocol):
    def __init__(self, finished, hashring, distributor):
        """
        :type hashring: :class:`bridgedb.bridgerings.FilteredBridgeSplitter`
        """
        self.finished = finished
        self.hashring = hashring
        self.distributor = distributor
        self.first_update = True
        self.buff = b""
        self.metrix = metrics.InternalMetrics()

    def dataReceived(self, data):
        """
        dataReceived is being called by twisted web client for each chunk it
        does receives. One chunk might not be full message from rdsys but a
        part of it, or the end of one and the beginning of the next, or
        multiple messages. We don't expect to be multiple messages in one
        chunk with rdsys, but anyway is implemented to support that usecase.

        self.buff is the accumulator, where we aggregate the chunks and when
        we find a inter_message_delimiter we update resources and reset
        self.buff setting it to the first part of the next message if there
        is one if not the data.split will anyway produce an empty bytes.
        """
        parts = data.split(inter_message_delimiter)
        self.buff += parts[0]
        for part in parts[1:]:
            self._updateResources()
            self._updateMetrics()
            self.buff = part

    def _updateResources(self):
        if self.first_update:
            self.hashring.bridges = {}
            self.first_update = False

        jb = json.loads(self.buff)
        for action, fn in [
            ("gone", self.hashring.remove),
            ("changed", self.hashring.insert),
            ("new", self.hashring.insert),
        ]:
            if jb[action] is None:
                continue

            for rtype in jb[action]:
                if jb[action][rtype] is None:
                    continue

                for resource in jb[action][rtype]:
                    bridge = Bridge()
                    try:
                        bridge.updateFromResource(resource)
                    except MalformedBridgeInfo as e:
                        logging.warning("Got a malformed bridge: %s" % e)
                    fn(bridge)

    def _updateMetrics(self):
        filterRings = self.hashring.filterRings
        for (ringName, (_, subring)) in filterRings.items():
            subRingName = "-".join(self.hashring.extractFilterNames(ringName))
            self.metrix.recordBridgesInHashring(self.distributor,
                                                subRingName,
                                                len(subring))

    def connectionLost(self, reason):
        logging.info("Connection lost with rdsys backend: %s" % reason)
        self.finished.callback(None)


def start_stream(distributor, token, rdsys_address, hashring):
    headers = Headers(
        {
            "Content-Type": ["application/json"],
            "Authorization": ["Bearer %s" % (token,)],
        }
    )
    body = {
        "request_origin": distributor,
        "resource_types": ["obfs4", "vanilla"],
    }
    agent = Agent(reactor)

    delay = 1

    def delayError(err):
        nonlocal delay
        d = Deferred()
        reactor.callLater(delay, d.errback, err)
        delay *= 2
        if delay > max_retry_delay:
            delay = max_retry_delay
        return d

    def cbResponse(r):
        nonlocal delay
        delay = 1
        finished = Deferred()
        r.deliverBody(RdsysProtocol(finished, hashring, distributor))
        return finished

    def connect(_=None):
        """
        Connect to the rdsys backend

        The param _ is there so it can be used as callback on a deferred and
        ignore the result of the previous callback.
        """
        buff = BytesIO(bytes(json.dumps(body), "utf-8"))
        body_producer = FileBodyProducer(buff)
        d = agent.request(
            b"GET",
            b"http://%s/resource-stream" % (rdsys_address.encode(),),
            headers=headers,
            bodyProducer=body_producer,
        )
        d.addErrback(delayError)
        d.addCallback(cbResponse)
        d.addErrback(lambda err: logging.warning("Error on the connection with rdsys: " + str(err)))
        d.addCallback(connect)

    connect()
