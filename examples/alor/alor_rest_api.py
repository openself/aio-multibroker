import logging
import os
from datetime import datetime, timedelta

from multibroker.clients.alor.enums import Exchange, DataFormat
from multibroker.clients.alor.exceptions import AlorException, AlorGetTokenException
from multibroker.clients.alor.functions import msk_datetime_to_utc_timestamp
from multibroker.mb import MultiBroker
from multibroker.helpers import async_run

LOG = logging.getLogger('multibroker')
LOG.setLevel(logging.DEBUG)
# LOG.setLevel(logging.INFO)
LOG.addHandler(logging.StreamHandler())

# print(f"Available loggers: {[name for name in logging.root.manager.loggerDict]}")


async def get_candles_for_symbol(params, api_client):
    try:
        symbol = params['symbol']
        symbol_name = symbol['name']
        interval = params['interval']
        timeframe_seconds = interval['timeframe_seconds']
        seconds_from = params['start_time']
        seconds_to = params['end_time']
    except Exception as e:
        LOG.error(f"[error] failed to get bars data; error: {e}")
        return False, str(e)

    try:
        resp = await api_client.get_history(
            exchange=Exchange.MOEX, symbol=symbol_name, tf=timeframe_seconds,
            seconds_from=seconds_from, seconds_to=seconds_to,
            use_untraded=False, data_format=DataFormat.SIMPLE
    )
        if resp['status_code'] != 200:
            is_success = False
            info = f"[error] failed to get data for {symbol}; error: {resp['response']}"

            return is_success, info

        response = resp['response']
    except Exception as e:
        is_success = False
        info = f"[error] failed to get data for {symbol}; error: {e}"

        return is_success, info

    for bar in response['history']:
        print(bar)

    is_success = True
    info = f"[success]"

    return is_success, info


async def run():
    refresh_token = os.environ['refresh_token']
    is_demo = True

    try:
        api_client = MultiBroker.create_alor_client(refresh_token=refresh_token, is_demo=is_demo)
    except AlorGetTokenException as ex:
        LOG.error(ex)

        return

    # for account in api_client.accounts:  # Пробегаемся по всем счетам
    #     portfolio = account['portfolio']  # Портфель
    #
    #     LOG.info(f'Счет #{account["account_id"]}, Договор: {account["agreement"]}, '
    #              f'Портфель: {portfolio} (рынок {account["type"]})')
    #     LOG.info(f'Режимы торгов: {account["boards"]}')
    #
    #     for exchange in account['exchanges']:  # Пробегаемся по всем биржам
    #         LOG.info(f'- Биржа: {exchange}')
    #
    #         try:
    #             data = await api_client.get_portfolio_summary(portfolio=portfolio, exchange=exchange)
    #             LOG.info(f'  информации о портфеле: {data["response"]}')
    #         except AlorGetTokenException as ex:
    #             LOG.error(ex)
    #
    #             break

    now = datetime.now()
    start_time = msk_datetime_to_utc_timestamp(now - timedelta(minutes=5)) - 1
    end_time = msk_datetime_to_utc_timestamp(now)

    symbol = {
        'name':  'NG-3.25',
    }
    candle_rate = {
        'name': '1m',
        'timeframe_seconds': 60
    }

    params = {
        'start_time': start_time,
        'end_time': end_time,
        'symbol': symbol,
        'interval': candle_rate,
    }

    is_success, info = await get_candles_for_symbol(params, api_client)
    LOG.info(is_success)
    LOG.info(info)

    await api_client.close()


if __name__ == "__main__":
    async_run(run())
