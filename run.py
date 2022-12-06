import datetime
import json
import requests
import urllib
import websocket
import threading
from decimal import Decimal

from dydx3 import Client
from dydx3.constants import *
from dydx3.helpers.request_helpers import generate_now_iso

from config import config


# Global Vars
xchange = None
signature = None
signature_time = None
account = None

GOOD_TILL = 1672531200
J = 10000000000


def log(aMsg):
    def _log(msg):
        conf = config()
        msg = conf['main']['name'] + ':' + msg
        print(datetime.datetime.now().isoformat(), msg)

        if conf['telegram']['chatid'] == '' or conf['telegram']['bottoken'] == '':
            return

        params = {
            'chat_id': conf['telegram']['chatid'],
            'text': msg
        }
        payload_str = urllib.parse.urlencode(params, safe='@')
        requests.get(
            'https://api.telegram.org/bot' +
            conf['telegram']['bottoken'] + '/sendMessage',
            params=payload_str
        )
    threading.Thread(target=_log, args=[aMsg]).start()


def createOrder(aSide, aSize, aPrice):
    global xchange
    global account
    conf = config()

    order = xchange.private.create_order(
        position_id=account['positionId'],
        market=conf['main']['market'],
        side=aSide,
        order_type=ORDER_TYPE_LIMIT,
        post_only=True,
        size=str(aSize),
        price=str(aPrice),
        limit_fee='0.1',
        expiration_epoch_seconds=GOOD_TILL,
    ).data['order']

    log(f'{aSide} order size {aSize} opened @ {aPrice}')
    return order


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
    global orderDCA
    global orderTP

    global startPrice
    global DCANo

    global totalSize
    global totalCash

    global account
    global tickSize
    global averagePrice

    conf = config()
    
    # Check only for order messages
    message = json.loads(message)
    if message['type'] != 'channel_data':
        return

    if len(message['contents']['orders']) == 0:
        return

    # Only let us know if TP or DCA order is filled
    foundFlag = False
    for order in message['contents']['orders']:
        if order['status'] == 'FILLED':
            if orderTP is not None:
                if order['id'] == orderTP['id']:
                    foundFlag = True
                    break

            if order['id'] == orderDCA['id']:
                foundFlag = True
                break
            
    if not foundFlag:
        return

    # TP Check, if take profit is filled hurray! 🦘
    if orderTP is not None:
        if order['id'] == orderTP['id']:
            log('Take profit order filled! 💰')
            try:
                xchange.private.cancel_order(orderDCA['id'])
            except:
                log(f'Order #{DCANo} cancel error. cancelled manually?')
            ws.close()
            return

    # Must be DCA order that is filled

    log('DCA order filled')
    # 1. Remove old TP and put new one
    if orderTP is not None:
        try:
            xchange.private.cancel_order(orderTP['id'])
        except:
            log('TP cancel failed, cancelled manually?')

    if conf['main']['direction'] == 'short':
        orderSide = ORDER_SIDE_BUY
        orderPrice = averagePrice * (1 - conf['orders'][DCANo]['profit'])

    if conf['main']['direction'] == 'long':
        orderSide = ORDER_SIDE_SELL
        orderPrice = averagePrice * (1 + conf['orders'][DCANo]['profit'])

    XorderPrice = round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent))

    log('Place new take profit')
    orderTP = createOrder(orderSide, totalSize, XorderPrice)

    # 2. Place new DCA order
    DCANo += 1

    orderSize = conf['orders'][DCANo]['size']

    if conf['main']['direction'] == 'long':
        orderSide = ORDER_SIDE_BUY
        orderPrice = startPrice * (1 - conf['orders'][DCANo]['price'])

    if conf['main']['direction'] == 'short':
        orderSide = ORDER_SIDE_SELL
        orderPrice = startPrice * (1 + conf['orders'][DCANo]['price'])

    XorderPrice = str(round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent)))

    log(f'Order #{DCANo}')
    orderDCA = createOrder(orderSide, orderSize, XorderPrice)

    totalSize += int(orderSize * J) / J
    totalCash += (int(orderSize * J) * orderPrice) / J
    averagePrice = totalCash / totalSize
    XaveragePrice = str(round(averagePrice, abs(Decimal(tickSize).as_tuple().exponent)))
    log(f'Position size:{totalSize} @ {XaveragePrice}')


def ws_close(ws, p2, p3):
    global orderTP
    global orderDCA
    global DCANo

    log('Terminated by user, cancelling orders')
    if orderTP is not None:
        try:
            xchange.private.cancel_order(orderTP['id'])
        except:
            log('TP cancel failed, cancelled manually?')

    try:
        xchange.private.cancel_order(orderDCA['id'])
    except:
       log(f'Order {DCANo} cancel failed, cancelled manually?') 

def on_ping(ws, message):
    global account
    global orderDCA
    global user
    global DCANo

    conf = config()
    # To keep connection API active
    user = xchange.private.get_user().data['user']

    # # Kill the bot if it waits too long for the first order
    # if DCANo == 0 and conf['start']['price'] == 0:
    #     if orderDCA is not None:
    #         orderDCA = xchange.private.get_order_by_id(orderDCA['id']).data['order']
    #         if ['status'] != 'FILLED':
    #             # TODO: The starting order can be partially filled. We need to compare remainingSize and size
    #             xchange.private.cancel_order(orderDCA['id'])
    #             log('Order #0 not filled. Exiting.')
    #             ws.close()

def main():
    global xchange
    global signature
    global signature_time
    global account
    global user

    global startPrice
    global DCANo
    global orderDCA

    global totalSize
    global totalCash


    global orderTP
    global tickSize
    global averagePrice

    startTime = datetime.datetime.now()

    # Load configuration
    conf = config()

    log(f'Start {startTime.isoformat()}')

    log('DEX connect.')
    xchange = Client(
        network_id=NETWORK_ID_MAINNET,
        host=API_HOST_MAINNET,
        api_key_credentials={
            'key': conf['dydx']['APIkey'],
            'secret': conf['dydx']['APIsecret'],
            'passphrase': conf['dydx']['APIpassphrase'],
        },
        stark_private_key=conf['dydx']['stark_private_key'],
        default_ethereum_address=conf['dydx']['default_ethereum_address'],
    )

    signature_time = generate_now_iso()
    signature = xchange.private.sign(
        request_path='/ws/accounts',
        method='GET',
        iso_timestamp=signature_time,
        data={},
    )

    account = xchange.private.get_account().data['account']
    user = xchange.private.get_user().data['user']

    market = xchange.public.get_markets(
        conf['main']['market']).data['markets'][conf['main']['market']]
    tickSize = market['tickSize']

    orderDCA = None
    totalSize = 0
    totalCash = 0
    DCANo = 0
    orderTP = None
    averagePrice = 0

    # First order
    log(f'Order #{DCANo}')
    orderBook = xchange.public.get_orderbook(conf['main']['market']).data
    startPrice = conf['start']['price']

    if startPrice == 0:
        ask = float(orderBook['asks'][0]['price'])
        bid = float(orderBook['bids'][0]['price'])
        startPrice = (ask + bid) / 2

    if conf['main']['direction'] == 'long':
        orderSide = ORDER_SIDE_BUY
        orderPrice = startPrice * (1 - conf['orders'][DCANo]['price'])

    if conf['main']['direction'] == 'short':
        orderSide = ORDER_SIDE_SELL
        orderPrice = startPrice * (1 + conf['orders'][DCANo]['price'])

    XorderPrice = str(round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent)))
    orderSize = conf['orders'][DCANo]['size']
    orderDCA = createOrder(orderSide, str(orderSize), XorderPrice)

    totalSize += int(orderSize * J) / J
    totalCash += (int(orderSize * J) * orderPrice) / J
    averagePrice = orderPrice
    XaveragePrice = str(round(averagePrice, abs(Decimal(tickSize).as_tuple().exponent)))
    log(f'Position size:{totalSize} @ {XaveragePrice}')

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
