# -*- coding: utf-8 ; test-case-name: bridgedb.test.test_Main -*-
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: please see the AUTHORS file for attributions
# :copyright: (c) 2013-2017, Isis Lovecruft
#             (c) 2013-2017, Matthew Finkel
#             (c) 2007-2017, Nick Mathewson
#             (c) 2007-2017, The Tor Project, Inc.
# :license: see LICENSE for licensing information

"""This module sets up BridgeDB and starts the servers running."""

import logging
import os
import signal
import sys
import time

from twisted.internet import reactor
from twisted.internet import task

from bridgedb import crypto
from bridgedb import persistent
from bridgedb import proxy
from bridgedb import runner
from bridgedb import util
from bridgedb import metrics
from bridgedb import antibot
from bridgedb import rdsys
from bridgedb.bridges import MalformedBridgeInfo
from bridgedb.bridges import MissingServerDescriptorDigest
from bridgedb.bridges import ServerDescriptorDigestMismatch
from bridgedb.bridges import ServerDescriptorWithoutNetworkstatus
from bridgedb.bridges import Bridge
from bridgedb.configure import loadConfig
from bridgedb.distributors.email.distributor import EmailDistributor
from bridgedb.distributors.https.distributor import HTTPSDistributor
from bridgedb.distributors.moat.distributor import MoatDistributor
from bridgedb.parse import descriptors
from bridgedb.parse.blacklist import parseBridgeBlacklistFile
from bridgedb.parse.versions import parseVersionsList

import bridgedb.Storage

from bridgedb import bridgerings
from bridgedb.Stability import updateBridgeHistory


def expandBridgeAuthDir(authdir, filename):
    """Expands a descriptor ``filename`` relative to which of the
    BRIDGE_AUTHORITY_DIRECTORIES, ``authdir`` it resides within.
    """
    path = filename

    if not authdir in filename or not os.path.isabs(filename):
        path = os.path.abspath(os.path.expanduser(os.sep.join([authdir, filename])))

    return path

def writeMetrics(filename, measurementInterval):
    """Dump usage metrics to disk.

    :param str filename: The filename to write the metrics to.
    :param int measurementInterval: The number of seconds after which we rotate
        and dump our metrics.
    """

    logging.debug("Dumping metrics to file: '%s'" % filename)

    try:
        with open(filename, 'w') as fh:
            metrics.export(fh, measurementInterval)
    except IOError as err:
        logging.error("Failed to write metrics to '%s': %s" % (filename, err))

def load(cfg, proxyList, key):
    """Load the configured distributors and their connections to rdsys

    :type cfg: :class:`Conf`
    :ivar cfg: The current configuration, including any in-memory
        settings (i.e. settings whose values were not obtained from the
        config file, but were set via a function somewhere)
    :type proxyList: :class:`~bridgedb.proxy.ProxySet`
    :param proxyList: The container for the IP addresses of any currently
        known open proxies.
    :param bytes key: Hashring master key
    """
    # Create ring parameters.
    ringParams = bridgerings.BridgeRingParameters(needPorts=cfg.FORCE_PORTS,
                                                  needFlags=cfg.FORCE_FLAGS)

    emailDistributor = ipDistributor = moatDistributor = None

    # As appropriate, create a Moat distributor.
    if cfg.MOAT_DIST and cfg.MOAT_SHARE:
        logging.debug("Setting up Moat Distributor...")
        moatDistributor = MoatDistributor(
            cfg.MOAT_N_IP_CLUSTERS,
            crypto.getHMAC(key, "Moat-Dist-Key"),
            proxyList,
            answerParameters=ringParams)
        moatDistributor.prepopulateRings()
        if cfg.MOAT_DUMMY_BRIDGES_FILE:
            moatDistributor.loadDummyBridges(cfg.MOAT_DUMMY_BRIDGES_FILE)
        rdsys.start_stream("moat", cfg.RDSYS_TOKEN, cfg.RDSYS_ADDRESS, moatDistributor.hashring)

    # As appropriate, create an IP-based distributor.
    if cfg.HTTPS_DIST and cfg.HTTPS_SHARE:
        logging.debug("Setting up HTTPS Distributor...")
        ipDistributor = HTTPSDistributor(
            cfg.N_IP_CLUSTERS,
            crypto.getHMAC(key, "HTTPS-IP-Dist-Key"),
            proxyList,
            answerParameters=ringParams)
        ipDistributor.prepopulateRings()
        rdsys.start_stream("https", cfg.RDSYS_TOKEN, cfg.RDSYS_ADDRESS, ipDistributor.hashring)

    # As appropriate, create an email-based distributor.
    if cfg.EMAIL_DIST and cfg.EMAIL_SHARE:
        logging.debug("Setting up Email Distributor...")
        emailDistributor = EmailDistributor(
            crypto.getHMAC(key, "Email-Dist-Key"),
            cfg.EMAIL_DOMAIN_MAP.copy(),
            cfg.EMAIL_DOMAIN_RULES.copy(),
            answerParameters=ringParams,
            whitelist=cfg.EMAIL_WHITELIST.copy())
        emailDistributor.prepopulateRings()
        rdsys.start_stream("email", cfg.RDSYS_TOKEN, cfg.RDSYS_ADDRESS, emailDistributor.hashring)

    return emailDistributor, ipDistributor, moatDistributor

def _reloadFn(*args):
    """Placeholder callback function for :func:`_handleSIGHUP`."""
    return True

def _handleSIGHUP(*args):
    """Called when we receive a SIGHUP; invokes _reloadFn."""
    reactor.callInThread(_reloadFn)

def run(options, reactor=reactor):
    """This is BridgeDB's main entry point and main runtime loop.

    Given the parsed commandline options, this function handles locating the
    configuration file, loading and parsing it, and then either (re)parsing
    plus (re)starting the servers, or dumping bridge assignments to files.

    :type options: :class:`bridgedb.parse.options.MainOptions`
    :param options: A pre-parsed options class containing any arguments and
        options given in the commandline we were called with.
    :type state: :class:`bridgedb.persistent.State`
    :ivar state: A persistent state object which holds config changes.
    :param reactor: An implementer of
        :api:`twisted.internet.interfaces.IReactorCore`. This parameter is
        mainly for testing; the default
        :api:`twisted.internet.epollreactor.EPollReactor` is fine for normal
        application runs.
    """
    # Change to the directory where we're supposed to run. This must be done
    # before parsing the config file, otherwise there will need to be two
    # copies of the config file, one in the directory BridgeDB is started in,
    # and another in the directory it changes into.
    os.chdir(options['rundir'])
    if options['verbosity'] <= 10: # Corresponds to logging.DEBUG
        print("Changed to runtime directory %r" % os.getcwd())

    config = loadConfig(options['config'])
    config.RUN_IN_DIR = options['rundir']

    # Set up logging as early as possible. We cannot import from the bridgedb
    # package any of our modules which import :mod:`logging` and start using
    # it, at least, not until :func:`safelog.configureLogging` is
    # called. Otherwise a default handler that logs to the console will be
    # created by the imported module, and all further calls to
    # :func:`logging.basicConfig` will be ignored.
    util.configureLogging(config)

    if options.subCommand is not None:
        runSubcommand(options, config)

    # Write the pidfile only after any options.subCommands are run (because
    # these exit when they are finished). Otherwise, if there is a subcommand,
    # the real PIDFILE would get overwritten with the PID of the temporary
    # bridgedb process running the subcommand.
    if config.PIDFILE:
        logging.debug("Writing server PID to file: '%s'" % config.PIDFILE)
        with open(config.PIDFILE, 'w') as pidfile:
            pidfile.write("%s\n" % os.getpid())
            pidfile.flush()

    # Let our pluggable transport class know what transports are resistant to
    # active probing.  We need to know because we shouldn't hand out a
    # probing-vulnerable transport on a bridge that supports a
    # probing-resistant transport.  See
    # <https://bugs.torproject.org/28655> for details.
    from bridgedb.bridges import PluggableTransport
    PluggableTransport.probing_resistant_transports = config.PROBING_RESISTANT_TRANSPORTS

    from bridgedb import persistent

    state = persistent.State(config=config)

    from bridgedb.distributors.email.server import addServer as addSMTPServer
    from bridgedb.distributors.https.server import addWebServer
    from bridgedb.distributors.moat.server  import addMoatServer

    # Load the master key, or create a new one.
    key = crypto.getKey(config.MASTER_KEY_FILE)
    proxies = proxy.ProxySet()
    emailDistributor = None
    ipDistributor = None
    moatDistributor = None

    # Save our state
    state.key = key
    state.save()

    def reload(inThread=True): # pragma: no cover
        """Reload settings, proxy lists, and bridges.

        State should be saved before calling this method, and will be saved
        again at the end of it.

        The internal variables ``cfg`` is taken from a
        :class:`~bridgedb.persistent.State` instance, which has been saved to a
        statefile with :meth:`bridgedb.persistent.State.save`.

        :type cfg: :class:`Conf`
        :ivar cfg: The current configuration, including any in-memory
            settings (i.e. settings whose values were not obtained from the
            config file, but were set via a function somewhere)
        """
        logging.debug("Caught SIGHUP")
        logging.info("Reloading...")

        logging.info("Loading saved state...")
        state = persistent.load()
        cfg = loadConfig(state.CONFIG_FILE, state.config)
        logging.info("Updating any changed settings...")
        state.useChangedSettings(cfg)

        level = getattr(state, 'LOGLEVEL', 'WARNING')
        logging.info("Updating log level to: '%s'" % level)
        level = getattr(logging, level)
        logging.getLogger().setLevel(level)

        logging.info("Reloading the list of open proxies...")
        for proxyfile in cfg.PROXY_LIST_FILES:
            logging.info("Loading proxies from: %s" % proxyfile)
            proxy.loadProxiesFromFile(proxyfile, proxies, removeStale=True)
        metrics.setProxies(proxies)

        state.BLACKLISTED_TOR_VERSIONS = parseVersionsList(state.BLACKLISTED_TOR_VERSIONS)

        logging.info("Reloading blacklisted request headers...")
        antibot.loadBlacklistedRequestHeaders(config.BLACKLISTED_REQUEST_HEADERS_FILE)
        logging.info("Reloading decoy bridges...")
        antibot.loadDecoyBridges(config.DECOY_BRIDGES_FILE)

        # Initialize our DB.
        bridgedb.Storage.initializeDBLock()
        bridgedb.Storage.setDBFilename(cfg.DB_FILE + ".sqlite")

        state.save()

        if inThread:
            # XXX shutdown the distributors if they were previously running
            # and should now be disabled
            pass
        else:
            # We're still starting up. Return these distributors so
            # they are configured in the outer-namespace
            return load(cfg, proxies, key)

    global _reloadFn
    _reloadFn = reload
    signal.signal(signal.SIGHUP, _handleSIGHUP)

    if reactor:  # pragma: no cover
        # And actually load it to start parsing. Get back our distributors.
        emailDistributor, ipDistributor, moatDistributor = reload(False)

        # Configure all servers:
        if config.MOAT_DIST and config.MOAT_SHARE:
            addMoatServer(config, moatDistributor)
        if config.HTTPS_DIST and config.HTTPS_SHARE:
            addWebServer(config, ipDistributor)
        if config.EMAIL_DIST and config.EMAIL_SHARE:
            addSMTPServer(config, emailDistributor)

        metrics.setSupportedTransports(config.SUPPORTED_TRANSPORTS)

        tasks = {}

        # Setup all our repeating tasks:
        if config.TASKS['GET_TOR_EXIT_LIST']:
            tasks['GET_TOR_EXIT_LIST'] = task.LoopingCall(
                proxy.downloadTorExits,
                proxies,
                config.SERVER_PUBLIC_EXTERNAL_IP)

        if config.TASKS.get('DELETE_UNPARSEABLE_DESCRIPTORS'):
            delUnparseableSecs = config.TASKS['DELETE_UNPARSEABLE_DESCRIPTORS']
        else:
            delUnparseableSecs = 24 * 60 * 60  # Default to 24 hours

        # We use the directory name of STATUS_FILE, since that directory
        # is where the *.unparseable descriptor files will be written to.
        tasks['DELETE_UNPARSEABLE_DESCRIPTORS'] = task.LoopingCall(
            runner.cleanupUnparseableDescriptors,
            os.path.dirname(config.STATUS_FILE), delUnparseableSecs)

        measurementInterval, _ = config.TASKS['EXPORT_METRICS']
        tasks['EXPORT_METRICS'] = task.LoopingCall(
            writeMetrics, state.METRICS_FILE, measurementInterval)

        # Schedule all configured repeating tasks:
        for name, value in config.TASKS.items():
            seconds, startNow = value
            if seconds:
                try:
                    # Set now to False to get the servers up and running when
                    # first started, rather than spend a bunch of time in
                    # scheduled tasks.
                    tasks[name].start(abs(seconds), now=startNow)
                except KeyError:
                    logging.info("Task %s is disabled and will not run." % name)
                else:
                    logging.info("Scheduled task %s to run every %s seconds."
                                 % (name, seconds))

    # Actually run the servers.
    try:
        if reactor and not reactor.running:
            logging.info("Starting reactors.")
            reactor.run()
    except KeyboardInterrupt: # pragma: no cover
        logging.fatal("Received keyboard interrupt. Shutting down...")
    finally:
        if config.PIDFILE:
            os.unlink(config.PIDFILE)
        logging.info("Exiting...")
        sys.exit()

def runSubcommand(options, config):
    """Run a subcommand from the 'Commands' section of the bridgedb help menu.

    :type options: :class:`bridgedb.opt.MainOptions`
    :param options: A pre-parsed options class containing any arguments and
        options given in the commandline we were called with.
    :type config: :class:`bridgedb.main.Conf`
    :param config: The current configuration.
    :raises: :exc:`SystemExit` when all subCommands and subOptions have
        finished running.
    """
    # Make sure that the runner module is only imported after logging is set
    # up, otherwise we run into the same logging configuration problem as
    # mentioned above with the email.server and https.server.
    from bridgedb import runner

    if options.subCommand is not None:
        logging.debug("Running BridgeDB command: '%s'" % options.subCommand)

        if 'descriptors' in options.subOptions:
            runner.generateDescriptors(int(options.subOptions['descriptors']), config.RUN_IN_DIR)
        sys.exit(0)
