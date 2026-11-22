import pytest
from run import Martingale
from runtime import State, number


def settings(conf):
    conf['main']['direction'] = 'long'
    conf['start'] = {'price': 100, 'follow': 0.1}
    conf['orders'] = [{'size': 1, 'price': 0, 'profit': 0.1}, {'size': 3, 'price': 0.1, 'profit': 0.1}]
    return conf


def test_weighted_average_reprices_full_position(conf, fake, state_dir):
    bot = Martingale(settings(conf), fake)
    bot.tick()
    fake.fill('entry:0', price=100, fee='0.1')
    bot.tick()
    fake.fill('entry:1', price=90, fee='0.27')
    bot.tick()
    assert bot.totals() == (number(4), number('92.5'), number('0.37'))
    assert number(bot.state.data['tp']['price']) == number('101.75')
    assert fake.calls[-1][2]['reduce_only'] is True
    restored = Martingale(settings(conf), fake, State())
    before = len(fake.calls)
    restored.tick()
    assert len(fake.calls) == before
    assert restored.state.data['stage'] == 2


def test_take_profit_stops_and_includes_actual_fees(conf, fake, state_dir):
    conf = settings(conf)
    conf['orders'] = conf['orders'][:1]
    bot = Martingale(conf, fake)
    bot.tick()
    fake.fill('entry:0', fee='0.1')
    bot.tick()
    fake.fill(bot.state.data['tp']['id'], price=110, fee='0.11')
    assert bot.tick() is False
    assert number(bot.state.data['profit']) == number('9.79')
    assert Martingale(conf, fake, State()).tick() is False


def test_partial_entry_stops_without_extra_orders(conf, fake, state_dir):
    bot = Martingale(settings(conf), fake)
    bot.tick()
    fake.fill('entry:0', size='0.5', status='OPEN')
    with pytest.raises(RuntimeError, match='partially filled'):
        bot.tick()
    assert len(fake.calls) == 1


def test_final_entry_has_no_extra_averaging_order(conf, fake, state_dir):
    conf = settings(conf)
    conf['orders'] = conf['orders'][:1]
    bot = Martingale(conf, fake)
    bot.tick()
    fake.fill('entry:0')
    bot.tick()
    assert bot.state.data['dd'] is None
    assert len(fake.calls) == 2


def test_follow_cancels_unfilled_initial_order(conf, fake, state_dir):
    conf = settings(conf)
    conf['start']['price'] = 0
    bot = Martingale(conf, fake)
    bot.tick()
    fake.mid = number(150)
    assert bot.tick() is False
    assert bot.state.data['abandoned'] is True
