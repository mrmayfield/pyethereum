import logging
import threading
import time
import json
from bottle import run as bottle_run
from bottle import Bottle
from bottle import abort
from dispatch import receiver
from marshmallow import Serializer, fields
from hyp.responder import Responder
from pyethereum.blocks import block_structure
from pyethereum.chainmanager import chain_manager
from pyethereum.signals import request_data_async, config_ready

logger = logging.getLogger(__name__)
base_url = '/api/v0alpha'


class ApiServer(threading.Thread):

    def __init__(self):
        super(ApiServer, self).__init__()
        self.daemon = True
        self.listen_host = '127.0.0.1'
        self.port = 30203

    def configure(self, config):
        self.listen_host = config.get('api', 'listen_host')
        self.port = config.getint('api', 'listen_port')

    def run(self):
        global app
        app = CorsMiddleware(app)
        bottle_run(app, server='waitress',
                   host=self.listen_host, port=self.port)


def response_async_data(name, make_response, req=None):
    state = dict(res=None, ready=False)

    def callback(data):
        state.update(res=make_response(data), ready=True)

    request_data_async(name, req, callback)

    for i in range(500):
        if state['ready']:
            return state['res']
        time.sleep(0.01)
    return Exception()


# ###### create server ######

api_server = ApiServer()


@receiver(config_ready)
def config_api_server(sender, **kwargs):
    api_server.configure(sender)

app = Bottle()  # FIXME line 32?
app.config['autojson'] = True


# #######cors##############
class CorsMiddleware:
    HEADERS = [
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
        ('Access-Control-Allow-Headers',
         'Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token')
    ]

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ["REQUEST_METHOD"] == "OPTIONS":
            start_response('200 OK',
                           CorsMiddleware.HEADERS + [('Content-Length', "0")])

            return ""
        else:
            def my_start_response(status, headers, exc_info=None):
                headers.extend(CorsMiddleware.HEADERS)

                return start_response(status, headers, exc_info)
            return self.app(environ, my_start_response)

# ####### serialize ########################

def serialize(objs, serializer_class):
    assert serializer_class.TYPE.endswith('s') # plural
    if not isinstance(objs, (list, tuple)):
        objs = [objs]
    res = []
    for o in objs:
        res.append(serializer_class(o).data)
    data = {serializer_class.TYPE:res}
    return json.dumps(data, indent=0)


# ############ Blocks ######################


class Binary(fields.Raw):
    def format(self, value):
        return value.encode('hex')

class BlockSerializer(Serializer):
    TYPE = 'blocks'
    blockhash = fields.Function(lambda o: o.hex_hash())
    prevhash = Binary()
    uncles_hash = Binary()
    nonce = Binary()
    tx_list_root = Binary()

    class Meta:
        fields = [name for name, typ, _ in block_structure] + ['blockhash']


@app.get(base_url + '/blocks/')
def blocks():
    logger.debug('blocks/')
    return serialize(chain_manager.get_chain(start='', count=20), BlockSerializer)


@app.get(base_url + '/blocks/<blockhash>')
def block(blockhash=None):
    logger.debug('blocks/%s', blockhash)
    blockhash = blockhash.decode('hex')
    if blockhash in chain_manager:
        return serialize(chain_manager.get(blockhash), BlockSerializer)
    else:
        return abort(404, 'No block with id %s' % blockhash)


# ######## Peers ###################


class PeerSerializer(Serializer):
    TYPE = 'peers'
    ip = fields.Function(lambda o: o['ip'])
    port = fields.Function(lambda o: str(o['port']))
    node_id = fields.Function(lambda o: o['node_id'].encode('hex'))

def make_peers_response(peers):
    peers = [dict(ip=ip, port=port, node_id=node_id) for
             (ip, port, node_id) in peers]
    return serialize(peers, PeerSerializer)


@app.get(base_url + '/connected_peers/')
def connected_peers():
    return response_async_data('connected_peer_addresses', make_peers_response)


@app.get(base_url + '/known_peers/')
def known_peers():
    return response_async_data('known_peer_addresses', make_peers_response)
