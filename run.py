import ccxt

import config

xchange = ccxt.ftx({
        'apiKey': '',
        'secret': '',
    })

def getPrice():
    return xchange.fetch_ticker(config.MARKET)['last']

def buy(price, amount):
    return xchange.createLimitBuyOrder(config.MARKET, amount, price)

def sell(price,amount):
    return xchange.createLimitSellOrder(config.MARKET, amount, price)


# Create starting trade and wait for execution
price = getPrice()
print(price)

