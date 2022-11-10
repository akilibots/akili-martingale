import ccxt

import config

xchange = ccxt.ftx({
        'apiKey': '-P',
        'secret': 'sL-',
    })

def getPrice():
    return xchange.fetch_ticker(config.MARKET)['last']

def buy(price, amount):
    return xchange.createLimitBuyOrder(config.MARKET, amount, price)

def sell(price,amount):
    return xchange.createLimitSellOrder(config.MARKET, amount, price)


# Create starting trade and wait for execution
price = getPrice()
if config.DIRECTION == config.dLong:
    start = buy(price * (1 - config.BASE_DISTANCE), config.BASE)

if config.DIRECTION == config.dShort:
    start = sell(price * (1 + config.BASE_DISTANCE), config.BASE)

basePrice = price

while xchange.fetch_order(start['id'])['status'] != 'closed':
    pass

takeProfitOrder = None
takeProfitPrice = None

safetyOrder = None
safetyOrderPrice = None

safetyDone = 0
safetySize = None

positionSize = config.BASE

# Create first safety order and take profit order
price = getPrice()
safetySize = positionSize * config.SAFETY_PRICE_PERCENT

if config.DIRECTION == config.dLong:
    takeProfitPrice = basePrice *  (1 + config.PROFIT_PERCENT)
    takeProfitOrder = sell(takeProfitPrice , positionSize)

    safetyOrderPrice = basePrice * (1 - config.SAFETY_PRICE_DISTANCE)
    safetyOrder = buy(safetyOrderPrice, safetySize)

if config.DIRECTION == config.dShort:
    takeProfitPrice = basePrice *  (1 - config.PROFIT_PERCENT)
    takeProfitOrder = buy(takeProfitPrice , positionSize)

    safetyOrderPrice = basePrice * (1 + config.SAFETY_PRICE_DISTANCE)
    safetyOrder = sell(safetyOrderPrice, safetySize)



# while True:
#     price =getPrice()
#     orders = xchange.fetch_orders()
    
#     # Check and update takeProfit order
#     if takeProfitOrder is None:
#         if config.DIRECTION == config.dLong:
#             takeProfitPrice = basePrice *  (1 + config.PROFIT_PERCENT)
#             takeProfitOrder = sell(takeProfitPrice , positionSize)

#         if config.DIRECTION == config.dShort:
#             takeProfitPrice = basePrice *  (1 - config.PROFIT_PERCENT)
#             takeProfitOrder = buy(takeProfitPrice , positionSize)

#     # Check and update Safety Order
#     if safetyOrder is None:
#         if config.DIRECTION == config.dLong:
#             safetyOrderPrice = basePrice * (1 - config.SAFETY_PRICE_PERCENT)