import datetime
import json
import requests
import urllib
import websocket
from os import environ
from bisect import bisect

from dydx3 import Client
from dydx3.constants import *
from dydx3.helpers.request_helpers import generate_now_iso


# Constants
J = 10000000000

# Global Vars
config = None
xchange = None
signature = None
signature_time = None
grid = {}
account = None


def log(msg):
    msg = config['main']['name'] + ':' + msg
    print(datetime.datetime.now().isoformat(), msg)

    if config['telegram']['chatid'] == '' or config['telegram']['bottoken'] == '':
        return

    params = {
        'chat_id': config['telegram']['chatid'],
        'text': msg
    }
    payload_str = urllib.parse.urlencode(params, safe='@')
    requests.get(
        'https://api.telegram.org/bot' +
        config['telegram']['bottoken'] + '/sendMessage',
        params=payload_str
    )


def ws_open(ws):
    # Subscribe to order book updates
    log('Subscribing to order changes')
    ws.send(json.dumps({
        'type': 'subscribe',
        'channel': 'v3_accounts',
        'accountNumber': '0',
        'apiKey': xchange.api_key_credentials['key'],
        'passphrase': xchange.api_key_credentials['passphrase'],
        'timestamp': signature_time,
        'signature': signature,
    }))


def ws_message(ws, message):
    global config

    global orderDCA
    global orderTP

    # We are realoading configs so that you can update the grid when it is running
    config = json.loads(environ['strategy'])

    message = json.loads(message)
    if message['type'] != 'channel_data':
        return

    if len(message['contents']['orders']) == 0:
        return

    order = message['contents']['orders'][0]
    if order['status'] != 'FILLED':
        return

    if order['id'] == orderTP['id']:
        log('Take profit order filled')
        xchange.private.create_order(orderDCA['id'])
        orderDCA = None
        exit()

    if order['id'] == orderDCA['id']:
        log('DCA order filled')
        xchange.private.create_order(orderTP['id'])
        orderTP = None
        order()

 

def ws_close(ws, p2, p3):
    global grid

    log('Grid terminated by user.')
    for i in grid:
        if grid[i] is not None:
            orderType = grid[i]['side']
            orderPrice = grid[i]['price']

            log(f'Cancelling {orderType} order at {orderPrice}')
            xchange.private.cancel_order(grid[i]['id'])
            grid[i] = None
            
def on_ping(wsapp, message):
    global account        
    global config

    # We are realoading configs so that you can update the grid when it is running
    # To keep connection API active
    config = json.loads(environ['strategy'])
    account = xchange.private.get_account().data['account']
    # log("I'm alive!")

def order():
    global startPrice
    global DCANo
    global config
    global totalSize
    global totalCash
    global orderDCA
    global orderTP
    global account

    orderSize = config['orders'][DCANo]['size']

    if config['main']['dca'] == 'buy':
        orderSide = ORDER_SIDE_BUY
        orderPrice = startPrice * (1 - config['orders'][DCANo]['price'])

    if config['main']['dca'] == 'sell':
        orderSide = ORDER_SIDE_SELL
        orderPrice = startPrice * (1 + config['orders'][DCANo]['price'])

    log(f'Placing DCA {orderSide} order {DCANo + 1} at {orderPrice} size {orderSize}')
    orderDCA = xchange.private.create_order(
        position_id=account['positionId'],
        market=config['main']['market'],
        side=orderSide,
        order_type=ORDER_TYPE_LIMIT,
        post_only=True,
        size=str(orderSize),
        price=str(orderPrice),
        limit_fee='0',
        expiration_epoch_seconds=9000000000,
    ).data['order']

    totalSize += orderSize
    totalCash += orderSize * orderPrice
    averagePrice = totalCash / totalSize

    if config['main']['takeprofit'] == 'buy':
        orderSide = ORDER_SIDE_BUY
        orderPrice = averagePrice * (1 - config['orders'][DCANo]['price'])

    if config['main']['takeprofit'] == 'sell':
        orderSide = ORDER_SIDE_SELL
        orderPrice = averagePrice * (1 + config['orders'][DCANo]['price'])

    log(f'Placing take profit {totalSize} order at {averagePrice} size {totalSize}')
    orderTP = xchange.private.create_order(
        position_id=account['positionId'],
        market=config['main']['market'],
        side=orderSide,
        order_type=ORDER_TYPE_LIMIT,
        post_only=True,
        size=str(totalSize),
        price=str(orderPrice),
        limit_fee='0',
        expiration_epoch_seconds=9000000000,
    ).data['order']

    DCANo+=1

def main():
    global config
    global xchange
    global signature
    global signature_time
    global account

    global startPrice
    global DCANo
    global config
    global totalSize
    global totalCash
    global orderDCA
    global orderTP

    startTime = datetime.datetime.now()

    # Load configuration
    config = json.loads(environ['strategy'])

    log(f'Start time {startTime.isoformat()} - strategy loaded.')

    log('Connecting to exchange.')
    xchange = Client(
        network_id=NETWORK_ID_MAINNET,
        host=API_HOST_MAINNET,
        api_key_credentials={
            'key': config['dydx']['APIkey'],
            'secret': config['dydx']['APIsecret'],
            'passphrase': config['dydx']['APIpassphrase'],
        },
        stark_private_key=config['dydx']['stark_private_key'],
        default_ethereum_address=config['dydx']['default_ethereum_address'],
    )
    log('Signing URL')
    signature_time = generate_now_iso()
    signature = xchange.private.sign(
        request_path='/ws/accounts',
        method='GET',
        iso_timestamp=signature_time,
        data={},
    )

    log('Getting account data')
    account = xchange.private.get_account().data['account']

    orderBook = xchange.public.get_orderbook(config['main']['market']).data
    ask = float(orderBook['asks'][0]['price'])
    bid = float(orderBook['bids'][0]['price'])
   
    startPrice = (ask + bid) / 2
    orderDCA = None
    totalSize = 0
    totalCash = 0
    DCANo = 0

    order()

    log('Starting bot loop')
    # websocket.enableTrace(True)
    wsapp = websocket.WebSocketApp(
        WS_HOST_MAINNET,
        on_open=ws_open,
        on_message=ws_message,
        on_close=ws_close,
        on_ping=on_ping
    )

    wsapp.run_forever(ping_interval=60, ping_timeout=20)


if __name__ == "__main__":
    main()
