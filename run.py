"""Position averaging with a quantity-weighted take-profit on dYdX Chain."""
import time

from exchange import Exchange, TERMINAL
from runtime import State, load_config, log, number, single_instance


class Martingale:
    def __init__(self, conf, exchange, state=None):
        self.conf, self.exchange = conf, exchange
        if conf['main']['direction'] not in {'long', 'short'}:
            raise ValueError('direction must be long or short')
        if number(conf['stop']['price']) != 0:
            raise ValueError('stop.price is unsupported; use zero')
        if not conf['orders'] or number(conf['start']['follow']) < 0:
            raise ValueError('At least one entry and a nonnegative follow threshold are required')
        for order in conf['orders']:
            if number(order['size']) <= 0 or not 0 <= number(order['price']) < 1 or not 0 < number(order['profit']) < 1:
                raise ValueError('Entry sizes must be positive; deviations and profit targets must be valid ratios')
        self.state = state or State(initial={'strategy': conf, 'start_price': None, 'stage': 0, 'entries': [], 'dd': None, 'tp': None, 'tp_version': 0, 'done': False})
        if self.state.data['strategy'] != conf:
            raise ValueError('Strategy differs from saved state')

    def totals(self):
        entries = self.state.data['entries']
        size = sum((number(e['size']) for e in entries), number(0))
        value = sum((number(e['size']) * number(e['price']) for e in entries), number(0))
        fee = sum((number(e['fee']) for e in entries), number(0))
        return size, value / size if size else number(0), fee

    def tick(self):
        d = self.state.data
        if d['done']:
            return False
        ticker = self.conf['main']['market']
        direction = 1 if self.conf['main']['direction'] == 'long' else -1
        if d['tp'] is not None:
            tp_status = self.exchange.status(d['tp'])
            if number(tp_status['totalFilled']) and tp_status['status'] != 'FILLED':
                raise RuntimeError('Take-profit partially filled; stop and reconcile before changing exposure')
            if tp_status['status'] == 'FILLED':
                if d['dd'] is not None:
                    cancelled = self.exchange.cancel(d['dd'])
                    if number(cancelled['totalFilled']):
                        raise RuntimeError('Entry filled while take-profit completed; reconcile the remaining exposure')
                size, average, entry_fee = self.totals()
                closed, exit_price, exit_fee = self.exchange.execution(d['tp'])
                if closed != size:
                    raise RuntimeError('Take-profit size differs from tracked position')
                d['profit'] = str(direction * (exit_price - average) * size - entry_fee - exit_fee)
                d['done'] = True
                self.state.save()
                log('Net trade profit: ' + d['profit'], self.conf['main']['name'])
                return False
            if tp_status['status'] in TERMINAL:
                d['tp'] = None
                d['tp_version'] += 1
                self.state.save()
        if d['dd'] is not None:
            status = self.exchange.status(d['dd'])
            if status['status'] == 'FILLED':
                size, price, fee = self.exchange.execution(d['dd'])
                d['entries'].append({'size': str(size), 'price': str(price), 'fee': str(fee)})
                d['dd'] = None
                d['stage'] += 1
                self.state.save()
                if d['tp'] is not None:
                    cancelled = self.exchange.cancel(d['tp'])
                    if number(cancelled['totalFilled']):
                        raise RuntimeError('Take-profit filled during averaging; reconcile before replacing it')
                    d['tp'] = None
                    d['tp_version'] += 1
                    self.state.save()
            elif status['status'] in TERMINAL:
                raise RuntimeError('Entry cancelled or expired; reconcile before restarting this cycle')
            elif number(status['totalFilled']):
                # Never submit an exit based on an incomplete entry size.
                raise RuntimeError('Entry partially filled; stop and reconcile before continuing')
            elif d['stage'] == 0 and number(self.conf['start']['price']) == 0:
                movement = abs(self.exchange.price(ticker) - number(d['dd']['price'])) / number(d['dd']['price'])
                if movement > number(self.conf['start']['follow']):
                    cancelled = self.exchange.cancel(d['dd'])
                    if number(cancelled['totalFilled']):
                        return True
                    d['done'] = True
                    d['abandoned'] = True
                    self.state.save()
                    return False
        if d['stage']:
            size, average, _ = self.totals()
            if d['tp'] is not None and number(d['tp']['size']) != size:
                cancelled = self.exchange.cancel(d['tp'])
                if number(cancelled['totalFilled']):
                    raise RuntimeError('Take-profit filled during recovery; reconcile before replacing it')
                d['tp'] = None
                d['tp_version'] += 1
                self.state.save()
            if d['tp'] is None:
                target = average * (1 + direction * number(self.conf['orders'][d['stage'] - 1]['profit']))
                d['tp'] = self.exchange.place(f'tp:{d["stage"]}:{d["tp_version"]}', ticker, 'SELL' if direction == 1 else 'BUY', size, target, reduce_only=True)
                self.state.save()
        if d['dd'] is None and d['stage'] < len(self.conf['orders']):
            if d['start_price'] is None:
                d['start_price'] = str(number(self.conf['start']['price']) or self.exchange.price(ticker))
                self.state.save()
            entry = self.conf['orders'][d['stage']]
            price = number(d['start_price']) * (1 - direction * number(entry['price']))
            d['dd'] = self.exchange.place(f'entry:{d["stage"]}', ticker, 'BUY' if direction == 1 else 'SELL', entry['size'], price, post_only=True)
            self.state.save()
        return True


def main():
    conf = load_config()
    with single_instance():
        exchange = Exchange(conf['exchange'])
        try:
            bot = Martingale(conf, exchange)
            while bot.tick():
                time.sleep(conf['exchange']['poll_seconds'])
        finally:
            exchange.close()


if __name__ == '__main__':
    main()
