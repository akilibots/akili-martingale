import datetime
import json
import requests
import urllib
import websocket
import threading
import time
import os

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

# Constants
GOOD_TILL = 31536000
TO_INT = 10000000000 # Based on the number of decimals in the market


def log(msg):
    def _log(_msg):
        conf = config()
        _msg = conf['main']['name'] + ':' + _msg
        print(datetime.datetime.now().isoformat(), _msg)

        if conf['telegram']['chatid'] == '' or conf['telegram']['bottoken'] == '':
            return

        params = {
            'chat_id': conf['telegram']['chatid'],
            'text': _msg
        }
        payload_str = urllib.parse.urlencode(params, safe='@')
        requests.get(
            'https://api.telegram.org/bot' +
            conf['telegram']['bottoken'] + '/sendMessage',
            params=payload_str
        )
    threading.Thread(target=_log, args=[msg]).start()

def save_state():
    # Save state of bot so that it can resume in case it dies for some reason (which it does often!)
    global order_dca
    global order_tp

    global start_price
    global average_price
    global total_size

    save_data = {
        'order_dca':order_dca,
        'order_tp':order_tp,
        'average_price':average_price,
        'total_size': total_size,
        'start_price': start_price,
        'dca_no':dca_no,
    }
    log('Save state.')

    with open("data/state.json", "w") as f:
        json.dump(save_data, f)

def load_state():
    global order_dca
    global order_tp

    global start_price
    global average_price
    global total_size

    log('Check for saved state.')
    if not os.path.isfile('data/state.json'):
        log('No state saved. Start new.')
        return False

    with open("data/state.json", "r") as f:
        load_data = json.load(f)

    order_dca = load_data['order_dca']
    order_tp = load_data['order_tp']
    start_price = load_data['start_price']
    average_price = load_data['average_price']
    total_size = load_data['total_size']
    order_dca = load_data['order_dca']

    log('State loaded.')
    return True


def place_order(side, size, price):
    global xchange
    global account
    conf = config()

    order = xchange.private.create_order(
        position_id=account['positionId'],
        market=conf['main']['market'],
        side=side,
        order_type=ORDER_TYPE_LIMIT,
        post_only=True,
        size=str(size),
        price=str(price),
        limit_fee='0.1',
        expiration_epoch_seconds=int(time.time()) + GOOD_TILL,
    ).data['order']

    log(f'{side} order size {size} opened @ {price}')
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
    global order_dca
    global order_tp

    global start_price
    global dca_no

    global total_size

    global account
    global tick_size
    global average_price

    conf = config()
   
    # Check only for order messages
    message = json.loads(message)
    if message['type'] != 'channel_data':
        # Not an order book update
        return

    if len(message['contents']['orders']) == 0:
        # No orders to process
        return

    for order in message['contents']['orders']:
        if order['status'] == 'CANCELED':
            # Reinstate ALL cancelled orders (CANCELED is mis-spelt smh Americans!!)
            if order_tp is not None:
                if order_tp['id'] == order['id']:
                    # Take profit was cancelled - re-instate
                    log(f'😡 Recreating cancelled TP {order_tp["side"]} order at {order_tp["price"]}')
                    order_tp = place_order(order_tp['side'], order_tp['size'], order_tp['price'])

            if order_dca['id'] == order['id']:
                # Take profit was cancelled - re-instate
                log(f'😡 Recreating cancelled DCA {order_dca["side"]} order at {order_dca["price"]}')
                order_dca = place_order(order_dca['side'], order_dca['size'], order_dca['price'])

            # Save any re-instated orders
            save_state()

    # Only let us know if TP or DCA order is filled
    order_found = False
    for order in message['contents']['orders']:
        if order['status'] == 'FILLED':
            if order_tp is not None:
                if order['id'] == order_tp['id']:
                    order_found = True
                    break

            if order['id'] == order_dca['id']:
                order_found = True
                break
            
    if not order_found:
        return

    # TP Check, if take profit is filled hurray! 🦘
    if order_tp is not None:
        if order['id'] == order_tp['id']:
            log('Take profit order filled! 💰')
            try:
                xchange.private.cancel_order(order_dca['id'])
            except:
                log(f'Order #{dca_no} cancel error. cancelled manually?')
            ws.close()
            return

    # Must be DCA order that is filled
    log('DCA order filled')
    average_price = ((int(average_price * TO_INT) + int(float(order['price']) * TO_INT)) / 2) / TO_INT
    total_size += config['orders'][dca_no]
    # 1. Remove old TP and put new one
    if order_tp is not None:
        try:
            xchange.private.cancel_order(order_tp['id'])
        except:
            log('TP cancel fail, cancelled manually?')

    if conf['main']['direction'] == 'short':
        order_side = ORDER_SIDE_BUY
        order_price = average_price * (1 - conf['orders'][dca_no]['profit'])

    if conf['main']['direction'] == 'long':
        order_side = ORDER_SIDE_SELL
        order_price = average_price * (1 + conf['orders'][dca_no]['profit'])

    tick_order_price = round(order_price, abs(Decimal(tick_size).as_tuple().exponent))

    log('Place new take profit')
    order_tp = place_order(order_side, total_size, tick_order_price)

    # 2. Place new DCA order
    dca_no += 1
    if dca_no < len(conf['orders']):

        order_size = conf['orders'][dca_no]['size']

        if conf['main']['direction'] == 'long':
            order_side = ORDER_SIDE_BUY
            order_price = start_price * (1 - conf['orders'][dca_no]['price'])

        if conf['main']['direction'] == 'short':
            order_side = ORDER_SIDE_SELL
            order_price = start_price * (1 + conf['orders'][dca_no]['price'])

        tick_order_price = str(round(order_price, abs(Decimal(tick_size).as_tuple().exponent)))

        log(f'Order #{dca_no}')
        if dca_no == len(conf['orders']) - 1:
            log('Final DCA order 😮')

        order_dca = place_order(order_side, order_size, tick_order_price)

    save_state()


def ws_close(ws, p2, p3):

    log('Asked to stop some reason')
    save_state()


def on_ping(ws, message):
    global account
    global order_dca
    global user
    global dca_no

    conf = config()
    # To keep connection API active
    user = xchange.private.get_user().data['user']

    # # Kill the bot if it waits too long for the first order
    # if dca_no == 0 and conf['start']['price'] == 0:
    #     if order_dca is not None:
    #         order_dca = xchange.private.get_order_by_id(order_dca['id']).data['order']
    #         if ['status'] != 'FILLED':
    #             # TODO: The starting order can be partially filled. We need to compare remainingSize and size
    #             xchange.private.cancel_order(order_dca['id'])
    #             log('Order #0 not filled. Exiting.')
    #             ws.close()

def main():
    global xchange
    global signature
    global signature_time
    global account
    global user

    global start_price
    global dca_no
    global order_dca

    global total_size

    global order_tp
    global tick_size
    global average_price

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

    if not load_state():

        market = xchange.public.get_markets(
            conf['main']['market']).data['markets'][conf['main']['market']]

        tick_size = market['tickSize']

        order_dca = None
        order_tp = None

        total_size = 0
        average_price = 0

        dca_no = 0

        # First order
        log(f'Order #{dca_no}')
        order_book = xchange.public.get_orderbook(conf['main']['market']).data
        start_price = conf['start']['price'] 

        if start_price == 0:
            ask = float(order_book['asks'][0]['price'])
            bid = float(order_book['bids'][0]['price'])
            start_price = (ask + bid) / 2

        if conf['main']['direction'] == 'long':
            order_side = ORDER_SIDE_BUY
            order_price = start_price * (1 - conf['orders'][dca_no]['price'])

        if conf['main']['direction'] == 'short':
            order_side = ORDER_SIDE_SELL
            order_price = start_price * (1 + conf['orders'][dca_no]['price'])

        tick_order_price = str(round(order_price, abs(Decimal(tick_size).as_tuple().exponent)))
        order_size = conf['orders'][dca_no]['size']
        order_dca = place_order(order_side, str(order_size), tick_order_price)

        average_price = order_price
        # XaveragePrice = str(round(average_price, abs(Decimal(tick_size).as_tuple().exponent)))
        # log(f'Position size:{total_size} @ {XaveragePrice}')

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
