import datetime
import requests
import urllib
import websocket
import threading
import time
import os

from decimal import Decimal

import pyjson5 as json

from dydx3 import Client
from dydx3.constants import *
from dydx3.helpers.request_helpers import generate_now_iso

from config import config
from config import tokens


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
        keys = tokens()
        _msg = conf['main']['name'] + ':' + _msg
        print(datetime.datetime.now().isoformat(), _msg)

        if keys['telegram']['chatid'] == '' or keys['telegram']['bottoken'] == '':
            return

        params = {
            'chat_id': keys['telegram']['chatid'],
            'text': _msg
        }
        payload_str = urllib.parse.urlencode(params, safe='@')
        requests.get(
            'https://api.telegram.org/bot' +
            keys['telegram']['bottoken'] + '/sendMessage',
            params=payload_str
        )
    threading.Thread(target=_log, args=[msg]).start()

def save_state():
    # Save state of bot so that it can resume in case it dies for some reason (which it does often!)
    global order_dd
    global order_tp
    global dd_num

    global start_price
    global average_price
    global total_size

    save_data = {
        'order_dd':order_dd,
        'order_tp':order_tp,
        'average_price':average_price,
        'total_size': total_size,
        'start_price': start_price,
        'dd_num':dd_num,
    }

    with open("data/state.json", "w") as f:
        json.dump(save_data, f, default=str)

def load_state():
    global order_dd
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

    order_dd = load_data['order_dd']
    order_tp = load_data['order_tp']
    start_price = load_data['start_price']
    average_price = load_data['average_price']
    total_size = load_data['total_size']
    order_dd = load_data['order_dd']

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
    global order_dd
    global order_tp

    global start_price
    global dd_num

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

    # Check and recreate any manually cancelled orders
    for order in message['contents']['orders']:
        if order['status'] == 'CANCELED':
            # Reinstate ALL cancelled orders (CANCELED is mis-spelt smh Americans!!)
            if order_tp is not None:
                if order['id'] == order_tp['id']:
                    # Take profit was cancelled - recreate
                    log(f'Recreate cancelled TP 😡 {order_tp["side"]} order at {order_tp["price"]}')
                    order_tp = place_order(order_tp['side'], order_tp['size'], order_tp['price'])
                    # Save replacement order info
                    save_state()

            if order['id'] == order_dd['id']:
                # Take profit was cancelled - re-instate
                log(f'Recreate cancelled DD 😡 {order_dd["side"]} order at {order_dd["price"]}')
                order_dd = place_order(order_dd['side'], order_dd['size'], order_dd['price'])
                # Save replacement order info
                save_state()


    # Only let us know if TP or DD order is filled
    order_found = False
    for order in message['contents']['orders']:
        if order['status'] == 'FILLED':
            if order_tp is not None:
                if order['id'] == order_tp['id']:
                    order_found = True
                    break

            if order['id'] == order_dd['id']:
                order_found = True
                break
           
    if not order_found:
        return

    # TP Check, if take profit is filled hurray! 🦘
    if order_tp is not None:
        if order['id'] == order_tp['id']:
            try:
                xchange.private.cancel_order(order_dd['id'])
            except:
                log(f'Order #{dd_num} cancel error. cancelled manually?')
            xchange_fee = Decimal(user['makerFeeRate']) * (Decimal(str(dd_num)) + Decimal('2'))
            profit = abs(Decimal(str(average_price)) - Decimal(order['id']['price'])) * Decimal(str(total_size))
            net_profit = profit - xchange_fee
            log(f'Take profit order filled! 💰 USDC {net_profit}')

            ws.close()
            return

    # Must be DD order that is filled
    log(f'DD #{dd_num} filled')
 
    average_price = ((int(average_price * TO_INT) + int(float(order['price']) * TO_INT)) / 2) / TO_INT
    total_size += conf['orders'][dd_num]['size']
    log(f'Break even @ {average_price} size {total_size}')

    # 1. Remove old TP and put new one
    if order_tp is not None:
        try:
            xchange.private.cancel_order(order_tp['id'])
        except:
            log('TP cancel fail, cancelled manually?')
    if conf['main']['direction'] == 'short':
        order_side = ORDER_SIDE_BUY
        order_price = average_price * (1 - conf['orders'][dd_num]['profit'])
    if conf['main']['direction'] == 'long':
        order_side = ORDER_SIDE_SELL
        order_price = average_price * (1 + conf['orders'][dd_num]['profit'])
    tick_order_price = round(order_price, abs(Decimal(tick_size).as_tuple().exponent))
    order_tp = place_order(order_side, total_size, tick_order_price)

    # 2. Place new DD order
    dd_num += 1
    if dd_num < len(conf['orders']):
        order_size = conf['orders'][dd_num]['size']
        if conf['main']['direction'] == 'long':
            order_side = ORDER_SIDE_BUY
            order_price = start_price * (1 - conf['orders'][dd_num]['price'])
        if conf['main']['direction'] == 'short':
            order_side = ORDER_SIDE_SELL
            order_price = start_price * (1 + conf['orders'][dd_num]['price'])
        tick_order_price = str(round(order_price, abs(Decimal(tick_size).as_tuple().exponent)))
        if dd_num == len(conf['orders']) - 1:
            log('Final DD order 😮')

        order_dd = place_order(order_side, order_size, tick_order_price)

    save_state()


def ws_close(ws, p2, p3):
    log('Asked to stop some reason')
    save_state()


def on_ping(ws, message):
    global account
    global order_dd
    global user
    global dd_num

    # To keep connection API active
    user = xchange.private.get_user().data['user']

    # Kill the bot if it waits too long for the first order and past follow threshold
    conf = config()
    if dd_num == 0 and conf['start']['price'] == 0:
        if order_dd is not None:
            order_dd = xchange.private.get_order_by_id(order_dd['id']).data['order']
            if order_dd['status'] == 'PENDING':
                # Get current market price
                order_book = xchange.public.get_orderbook(conf['main']['market']).data
                ask = Decimal(order_book['asks'][0]['price'])
                bid = Decimal(order_book['bids'][0]['price'])
                market_price = (ask + bid) / 2

                move_ratio = abs(market_price - Decimal(order_dd['price']))/ Decimal(order_dd['price'])

                if move_ratio > Decimal(conf['start']['follow']):
                    # Price has moved past allowed follow threshold
                    # TODO: The starting order can be partially filled. We need to compare remainingSize and size
                   xchange.private.cancel_order(order_dd['id'])
                   log(f'Price {market_price} moved past follow threshold {move_ratio} for Order #0 @ {Decimal(order_dd["price"])}. Exiting.')
                   ws.close()

def main():
    global xchange
    global signature
    global signature_time
    global account
    global user

    global start_price
    global dd_num
    global order_dd

    global total_size

    global order_tp
    global tick_size
    global average_price

    startTime = datetime.datetime.now()

    # Load configuration
    conf = config()
    keys = tokens()

    log(f'Start {startTime.isoformat()}')

    log('DEX connect.')
    xchange = Client(
        network_id=NETWORK_ID_MAINNET,
        host=API_HOST_MAINNET,
        api_key_credentials={
            'key': keys['dydx']['APIkey'],
            'secret': keys['dydx']['APIsecret'],
            'passphrase': keys['dydx']['APIpassphrase'],
        },
        stark_private_key=keys['dydx']['stark_private_key'],
        default_ethereum_address=keys['dydx']['default_ethereum_address'],
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

        order_dd = None
        order_tp = None

        total_size = 0
        average_price = 0

        dd_num = 0

        # First order
        log(f'DD #{dd_num} (start order)')
        order_book = xchange.public.get_orderbook(conf['main']['market']).data
        start_price = conf['start']['price'] 

        if start_price == 0:
            ask = float(order_book['asks'][0]['price'])
            bid = float(order_book['bids'][0]['price'])
            start_price = (ask + bid) / 2

        if conf['main']['direction'] == 'long':
            order_side = ORDER_SIDE_BUY
            order_price = start_price * (1 - conf['orders'][dd_num]['price'])

        if conf['main']['direction'] == 'short':
            order_side = ORDER_SIDE_SELL
            order_price = start_price * (1 + conf['orders'][dd_num]['price'])

        tick_order_price = str(round(order_price, abs(Decimal(tick_size).as_tuple().exponent)))
        order_size = conf['orders'][dd_num]['size']
        order_dd = place_order(order_side, str(order_size), tick_order_price)

        average_price = order_price
        # XaveragePrice = str(round(average_price, abs(Decimal(tick_size).as_tuple().exponent)))
        # log(f'Position size:{total_size} @ {XaveragePrice}')
        save_state()

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
