import asyncio
import os

import nats


async def main() -> None:
    nc = await nats.connect(
        "wss://rc.tingyou.cc/nats",
        token=os.environ["ZGWD_NATS_TOKEN"],
    )
    js = nc.jetstream()
    for name in ("ZGWD_EVENTS_default",):
        await js.purge_stream(name)
        info = await js.stream_info(name)
        print(name, "purged -> msgs=", info.state.messages, "last_seq=", info.state.last_seq)
    await nc.close()


asyncio.run(main())
