from datetime import datetime
import logging
import os

from multibroker.clients.alor.AlorWebsocket import BarsSubscription
from multibroker.clients.alor.exceptions import AlorException, AlorGetTokenException
from multibroker.clients.alor.functions import msk_datetime_to_utc_timestamp
from multibroker.mb import MultiBroker
from multibroker.helpers import async_run

LOG = logging.getLogger("multibroker")
LOG.setLevel(logging.DEBUG)
LOG.addHandler(logging.StreamHandler())

# print(f"Available loggers: {[name for name in logging.root.manager.loggerDict]}\n")


async def bars_update(response: dict) -> None:
    print(f"callback bars_update: {response}")


async def run():
    refresh_token = os.environ['refresh_token']
    is_demo = False

    try:
        alor = MultiBroker.create_alor_client(refresh_token=refresh_token, is_demo=is_demo)
    except AlorGetTokenException as ex:
        LOG.error(ex)

        return

    # Bundle several subscriptions into a single websocket
    alor.compose_subscriptions([
        BarsSubscription(
            exchange='MOEX', symbol='NG-5.25', tf='60',
            instrument_group='RFUD',
            seconds_from=msk_datetime_to_utc_timestamp(datetime.now()),
            skip_history=False,
            frequency=1000,
            callbacks=[bars_update]
        ),
    ])

    # Execute all websockets asynchronously
    await alor.start_websockets()

    await alor.close()

if __name__ == "__main__":
    async_run(run())
