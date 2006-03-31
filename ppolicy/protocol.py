#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Twisted protocol factory and request
#
# Copyright (c) 2005 JAS
#
# Author: Petr Vokac <vokac@kmlinux.fjfi.cvut.cz>
#
# $Id$
#
import string
import logging
from twisted.internet import protocol, interfaces, task
from twisted.enterprise import adbapi



class PPolicyServerFactory:

    """This is a factory which produces protocols."""

    __implements__ = (interfaces.IProtocolFactory,)


    def __init__(self, protocol = None, config = {}):
        self.protocol = protocol
        self.numPorts = 0
        self.numProtocols = 0
        self.dbConnPool = None
        self.config = config
        self.modules = {}
        self.__addChecks(self.getConfig('modules'))


    def getDbConnection(self):
        if self.config.get('databaseAPI') == None:
           raise Exception("undefined databaseAPI")

        if self.dbConnPool == None:
            self.dbConnPool = adbapi.ConnectionPool(self.config.get('databaseAPI'),
                                                    **self.config.get('database'))
            #self.dbConnPool.min = 3
            #self.dbConnPool.max = 5
            self.dbConnPool.noisy = 1
            self.dbConnPool.start()

        return self.dbConnPool.connect()


    def getConfig(self, key, default = None):
        return self.config.get(key, default)


    def __addChecks(self, modules):
        for modName,v in modules.items():
            modType = v[0]
            modParams = v[1]
            if modType == None or modType == '':
                logging.getLogger().error("Type was not defined for module %s" % modName)
                raise Exception("Type was not defined for module %s" % modName)
            if not self.modules.has_key(modName):
                logging.getLogger().info("Adding module %s[%s(%s)]" % (modType, modName, modParams))
            else:
                logging.getLogger().warn("Redeclaration of module %s[%s(%s)]" % (modType, modName, modParams))
            globals()[modType] = eval("__import__('%s', globals(),  locals(), [])" % modType)
            obj = eval("%s.%s('%s', self, **%s)" % (modType, modType, modName, modParams))
            self.modules[modName] = obj


    def __startChecks(self):
        """Start factory modules."""
        for modName, modObj in self.modules.items():
	    logging.getLogger().info("Start module %s" % modObj.getId())
            modObj.start()


    def __stopChecks(self):
        """Stop factory modules."""
        for modName, modObj in self.modules.items():
	    logging.getLogger().info("Stop module %s" % modObj.getId())
            modObj.stop()


    def check(self, name, *args, **keywords):
        """Called from config file. We should cache results here."""
        # FIXME: cache results
        if not self.modules.has_key(name):
            raise Exception("Module named \"%s\" was not defined" % name)
        try:
            obj = self.modules.get(name)
            return obj.check(*args, **keywords)
        except Exception, e:
            logging.getLogger().error("failed to call method of \"%s\" module: %s" % (name, e))
            # raise e
            return 0, "%s failed with exception" % name


    def doStart(self):
	"""Make sure startFactory is called."""
	if not self.numPorts:
            logging.getLogger().info("Starting factory %s" % self)
	    self.startFactory()
	self.numPorts = self.numPorts + 1
        self.__startChecks()


    def doStop(self):
        """Make sure stopFactory is called."""
	assert self.numPorts > 0
	self.numPorts = self.numPorts - 1
	if not self.numPorts:
            logging.getLogger().info("Stopping factory %s" % self)
	    self.stopFactory()
        self.__stopChecks()


    def startFactory(self):
        """Called once."""
        pass


    def stopFactory(self):
        """Called once."""
        if self.dbConnPool != None and self.dbConnPool.running == 1:
            self.dbConnPool.close()


    def buildProtocol(self, addr):
	return self.protocol(self)



class PPolicyServerRequest(protocol.Protocol):

    CONN_LIMIT = 100


    def __init__(self, factory):
        self.data = {}
        self.factory = factory
        self.check = factory.getConfig('check')
        self.returnOnFatalError = factory.getConfig('returnOnFatalError', ('dunno', None))
        self.returnOnConnLimit = factory.getConfig('returnOnConnLimit', ('dunno', None))
	self.finished = False


    def connectionMade(self):
        self.factory.numProtocols += 1
        logging.getLogger().debug("connection #%s made" % self.factory.numProtocols)
        if self.factory.numProtocols > self.CONN_LIMIT:
            logging.getLogger().error("connection limit (%s) reached, returning dunno" % self.CONN_LIMIT)
            self.dataResponse(self.returnOnConnLimit[0], self.returnOnConnLimit[1])
            #self.transport.write("Too many connections, try later") 
            self.transport.loseConnection()


    def connectionLost(self, reason):
        logging.getLogger().debug("connection %s lost: %s" % (self.factory.numProtocols, reason))
        self.factory.numProtocols = self.factory.numProtocols-1


    def dataReceived(self, data):
        """Receive Data, Parse it, go through the checks"""
        try:
            parsedData = self.__parseData(data)
            if parsedData != None:
                action, actionEx = self.check(self.factory, parsedData)
                self.dataResponse(action, actionEx)
            else:
                # default return action for garbage?
                self.dataResponse()
        except Exception, err:
            import traceback
            logging.getLogger().error("uncatched exception: %s" % str(err))
            logging.getLogger().error("%s" % traceback.format_exc())
            self.dataResponse(self.returnOnFatalError[0], self.returnOnFatalError[1])


    def dataResponse(self, action=None, actionEx=None):
        """Check response"""
        if action == None:
            logging.getLogger().debug("output: action=dunno")
	    self.transport.write("action=dunno\n\n")
        elif actionEx == None:
            logging.getLogger().debug("output: action=%s" % action)
	    self.transport.write("action=%s\n\n" % action)
	else:
            logging.getLogger().debug("output: action=%s %s" % (action, actionEx))
	    self.transport.write("action=%s %s\n\n" % (action, actionEx))


    def __parseData(self, data):
        """Parse incomming data."""
        retData = {}
        for line in string.split(data, '\n'):
            line = line.strip()
            if line == '':
                if retData.has_key("request"):
                    return retData
                else:
                    logging.getLogger().error("policy protocol error: request wasn't specified before empty line")
                    return None
            try:
                k, v = line.split('=')
                if k == 'sender' or k == 'recipient':
                    if len(v) != 0 and v[0] == '<': v = v[1:]
                    if len(v) != 0 and v[-1] == '<': v = v[:-1]
                #if k == 'instance' and self.cluster == True:
                #    self.clusterip = self.transport.getPeer().host
                #    v = '%s_%s' % (self.clusterip, v)
                retData[k] = v
                logging.getLogger().debug("input: %s=%s" % (k, v))
            except ValueError:
                logging.getLogger().warn("garbage in input: %s" % line)
        logging.getLogger().warn("input was not ended by empty line")
        if retData.has_key("request"):
            return retData
        else:
            return None



if __name__ == "__main__":
    print "Module tests:"
    import sys, time
    import twisted.python.log
    twisted.python.log.startLogging(sys.stdout)

    # default config
    config = {
        'configFile'   : '../ppolicy.conf',
        'logLevel'     : logging.DEBUG,
        'admin'        : 'postmaster',
        'domain'       : socket.gethostname(),
        'databaseAPI'  : 'MySQLdb',
        'database'     : { 'host'   : 'localhost',
                           'port'   : 3306,
                           'db'     : 'ppolicy',
                           'user'   : 'ppolicy',
                           'passwd' : 'secret',
                           },
        'listenPort'   : 10030,
        'returnOnConnLimit': ('450', 'reached connection limit to ppolicy, retry later'),
        'returnOnFatalError': ('450', 'fatal error when checking SMTP data, retry later'),
        'check'        : lambda x: ('dunno', ''),
        'modules'      : {},
        }

    print ">>> Create Factory"
    factory	= PPolicyServerFactory(PPolicyServerRequest, config)

    print ">>> Test Db Connection"
    factory.getDbConnection().cursor().execute("SHOW DATABASES")

    print ">>> Start Factory"
    factory.doStart()
    time.sleep(5)
    print ">>> Stop Factory"
    factory.doStop()

