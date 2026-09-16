import asyncio
import json
import os

import nats


async def main() -> None:
    nc = await nats.connect(
        "wss://rc.tingyou.cc/nats",
        token=os.environ["ZGWD_NATS_TOKEN"],
    )
    js = nc.jetstream()
    msg = await js.get_last_msg("ZGWD_EVENTS_default", subject="zgwd.default.events")
    if msg is None:
        print("NO MESSAGE")
        await nc.close()
        return
    data = json.loads(msg.data.decode("utf-8"))
    out = ["stream_seq = %s" % msg.seq]
    for key in sorted(data.keys()):
        out.append("%s = %s" % (key, str(data[key])[:90]))
    with open(r"C:\Users\gaope\AppData\Local\Temp\last_event.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    await nc.close()


asyncio.run(main())
