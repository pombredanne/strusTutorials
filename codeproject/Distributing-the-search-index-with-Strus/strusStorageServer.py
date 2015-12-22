#!/usr/bin/python
import tornado.ioloop
import tornado.web
import tornado.gen
import tornado.iostream
import os
import sys
import struct
import strusIR
import time
import collections
import optparse
import trollius
import strusMessage
import binascii

# [0] Globals:
# Information retrieval engine:
backend = strusIR.Backend( "path=storage; cache=512M")
# Port of the global statistics server:
statserver = "localhost:7183"
# IO loop:
pubstats = False
# Strus client connection factory:
msgclient = strusMessage.RequestClient()

# [1] Publish statistics:
@tornado.gen.coroutine
def publishStatistics( itr):
    # Open connection to statistics server:
    print "+++ publishStatistics 1"
    try:
        ri = statserver.rindex(':')
        host,port = statserver[:ri],int( statserver[ri+1:])
        conn = yield msgclient.connect( host, port)
    except IOError as e:
        raise Exception( "connection so statistics server %s failed (%s) ... trying again (%u)" % (statserver, e))

    print "+++ publishStatistics 2"
    msg = itr.getNext()
    while (len(msg) > 0):
        try:
            print "+++ publishStatistics 3"
            reply = yield msgclient.issueRequest( conn, b"P" + bytearray(msg) )
            if (reply[0] == 'E'):
                raise Exception( "error in statistics server: %s" % reply[ 1:])
            elif (reply[0] != 'Y'):
                raise Exception( "protocol error publishing statistics")
        except tornado.iostream.StreamClosedError:
            raise Exception( "unexpected close of statistics server")
        msg = itr.getNext()
    print "+++ publishStatistics 4"

# [2] Request handlers
def packedMessage( msg):
    return struct.pack( ">H%ds" % len(msg), len(msg), msg)

@tornado.gen.coroutine
def processCommand( message):
    rt = bytearray("Y")
    try:
        messagesize = len(message)
        messageofs = 1
        if (message[0] == 'I'):
            # INSERT:
            print "+++ handle INSERT 1"
            # Insert documents:
            docblob = str( message[ 1:])
            print "+++ handle INSERT 2"
            nofDocuments = backend.insertDocuments( docblob.decode("utf-8"))
            # Publish statistic updates:
            print "+++ called INSERT %u" % nofDocuments
            itr = backend.getUpdateStatisticsIterator()
            print "+++ CALL publishStatistics"
            yield publishStatistics( itr)
            print "+++ DONE publishStatistics"
            rt += struct.pack( ">I", nofDocuments)
        elif (message[0] == 'Q'):
            # QUERY:
            Term = collections.namedtuple('Term', ['type', 'value', 'df'])
            rt = bytearray()
            nofranks = 20
            collectionsize = 0
            firstrank = 0
            terms = []
            messagesize = len(message)
            messageofs = 1
            while (messageofs < messagesize):
                if (message[ messageofs] == 'I'):
                    (firstrank,) = struct.unpack_from( ">H", message, messageofs+1)
                    messageofs += struct.calcsize( ">H") + 1
                elif (message[ messageofs] == 'N'):
                    (nofranks,) = struct.unpack_from( ">H", message, messageofs+1)
                    messageofs += struct.calcsize( ">H") + 1
                elif (message[ messageofs] == 'S'):
                    (collectionsize,) = struct.unpack_from( ">q", message, messageofs+1)
                    messageofs += struct.calcsize( ">q") + 1
                elif (message[ messageofs] == 'T'):
                    (df,typesize,valuesize) = struct.unpack_from( ">qHH", message, messageofs+1)
                    messageofs += struct.calcsize( ">qHH") + 1
                    (type,value) = struct.unpack_from( "%ds%ds" % (typesize,valuesize), message, messageofs)
                    messageofs += typesize + valuesize
                    terms.append( Term( type, value, df))
                else:
                    raise tornado.gen.Return( b"Eunknown parameter")
            # Evaluate query with BM25 (Okapi):
            results = backend.evaluateQueryText( terms, collectionsize, firstrank, nofranks)
            # Build the result:
            for result in results:
                rt.append( '_')
                rt.append( 'D')
                rt += struct.pack( ">I", result['docno'])
                rt.append( 'W')
                rt += struct.pack( ">f", result['weight'])
                rt.append( 'I')
                rt += self.packedstr( result['docid'])
                rt.append( 'T')
                rt += self.packedstr( result['title'])
                rt.append( 'A')
                rt += self.packedstr( result['abstract'])
        else:
            raise Exception( "unknown command")
    except Exception as e:
        raise tornado.gen.Return( bytearray( b"E" + str(e)) )
    raise tornado.gen.Return( rt)


def processShutdown():
    if (pubstats):
        publishStatistics( backend.getDoneStatisticsIterator())

# [4] Server main:
if __name__ == "__main__":
    try:
        parser = optparse.OptionParser()
        parser.add_option("-p", "--port", dest="port", default=7184,
                          help="Specify the port of this server as PORT (default %u)" % 7184,
                          metavar="PORT")
        parser.add_option("-s", "--statserver", dest="statserver", default=statserver,
                          help="Specify the address of the statistics server as ADDR (default %s" % statserver,
                          metavar="ADDR")
        parser.add_option("-P", "--publish-stats", action="store_true", dest="do_publish_stats", default=False,
                          help="Tell the node to publish the own storage statistics to the statistics server at startup")

        (options, args) = parser.parse_args()
        if len(args) > 0:
            parser.error("no arguments expected")
            parser.print_help()

        myport = options.port
        pubstats = options.do_publish_stats
        statserver = options.statserver
        if (statserver[0:].isdigit()):
            statserver = '{}:{}'.format( 'localhost', statserver)

        print "+++ statserver %s" % statserver
        if (pubstats):
            # Start publish local statistics:
            print( "Load local statistics to publish ...\n")
            publishStatistics( backend.getInitStatisticsIterator())

        # Start server:
        print( "Starting server ...")
        server = strusMessage.RequestServer( processCommand, processShutdown)
        server.start( myport)
        print( "Terminated\n")
    except Exception as e:
        print( e)


