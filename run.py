import datetime
import json
import requests
import urllib
import websocket

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


def log(msg):
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
        size=aSize,
        price=aPrice,
        limit_fee='0',
        expiration_epoch_seconds=GOOD_TILL,
    ).data['order']

    log(f'Placed {aSide} order size {aSize} @ {aPrice} : {order["status"]}')
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
    message = json.loads(message)

    if message['type'] != 'channel_data':
        return
    if len(message['contents']['orders']) == 0:
        return
    order = message['contents']['orders'][0]
    if order['status'] != 'FILLED':
        return

    if orderTP is not None:
        if order['id'] == orderTP['id']:
            log('Take profit order filled')
            try:
                xchange.private.cancel_order(orderDCA['id'])
            except:
                log(f'Error cancelling #{DCANo + 1}.Cancelled manually? Moving on...')
            ws.close()
            return

    if order['id'] == orderDCA['id']:
        log('DCA order filled')
        if orderTP is not None:
            try:
                xchange.private.cancel_order(orderTP['id'])
            except:
                log('Error cancelling take profit order. Cancelled manually? Moving on...')
            orderTP = None

        if conf['main']['takeprofit'] == 'buy':
            orderSide = ORDER_SIDE_BUY
            orderPrice = averagePrice * (1 - conf['orders'][DCANo]['profit'])

        if conf['main']['takeprofit'] == 'sell':
            orderSide = ORDER_SIDE_SELL
            orderPrice = averagePrice * (1 + conf['orders'][DCANo]['profit'])

        XorderPrice = str(round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent)))

        log('Placing take profit')
        orderTP = createOrder(orderSide, str(totalSize), XorderPrice)

        DCANo += 1

        orderSize = conf['orders'][DCANo]['size']

        if conf['main']['dca'] == 'buy':
            orderSide = ORDER_SIDE_BUY
            orderPrice = startPrice * (1 - conf['orders'][DCANo]['price'])

        if conf['main']['dca'] == 'sell':
            orderSide = ORDER_SIDE_SELL
            orderPrice = startPrice * (1 + conf['orders'][DCANo]['price'])

        XorderPrice = str(round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent)))

        log(f'Placing #{DCANo + 1}')
        orderDCA = createOrder(orderSide, orderSize, XorderPrice)

        totalSize += orderSize
        totalCash += orderSize * orderPrice
        averagePrice = totalCash / totalSize
        XaveragePrice = str(round(averagePrice, abs(Decimal(tickSize).as_tuple().exponent)))
        log(f'Position size:{totalSize} @ {XaveragePrice}')


def ws_close(ws, p2, p3):
    log('Terminated by user, cancelling orders')
    if orderTP is not None:
        xchange.private.cancel_order(orderTP['id'])
    xchange.private.cancel_order(orderDCA['id'])


def on_ping(ws, message):
    global account
    global orderDCA
    # To keep connection API active
    account = xchange.private.get_account().data['account']

    # Kill the bot if it waits too long for the first order
    if orderTP is None and DCANo == 0:
        xchange.private.cancel_order(orderDCA['id'])
        log('Order #1 not filled. Exiting.')
        ws.close()
        return

def main():
    global xchange
    global signature
    global signature_time
    global account

    global startPrice
    global DCANo

    global totalSize
    global totalCash

    global orderDCA
    global orderTP
    global tickSize
    global averagePrice

    startTime = datetime.datetime.now()

    # Load configuration
    conf = config()

    log(f'Start time {startTime.isoformat()} - strategy loaded.')

    log('Connecting to exchange.')
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
    log(f'Placing #1')
    orderBook = xchange.public.get_orderbook(conf['main']['market']).data
    ask = float(orderBook['asks'][0]['price'])
    bid = float(orderBook['bids'][0]['price'])
    startPrice = (ask + bid) / 2
    if conf['main']['dca'] == 'buy':
        orderSide = ORDER_SIDE_BUY
        orderPrice = startPrice * (1 - conf['orders'][DCANo]['price'])

    if conf['main']['dca'] == 'sell':
        orderSide = ORDER_SIDE_SELL
        orderPrice = startPrice * (1 + conf['orders'][DCANo]['price'])

    XorderPrice = str(round(orderPrice, abs(Decimal(tickSize).as_tuple().exponent)))
    orderSize = conf['orders'][DCANo]['size']
    orderDCA = createOrder(orderSide, str(orderSize), XorderPrice)

    totalSize += orderSize
    totalCash += orderSize * orderPrice
    averagePrice = totalCash / totalSize
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
