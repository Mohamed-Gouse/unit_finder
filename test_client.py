from telethon import TelegramClient

API_ID = 28380388
API_HASH = "c48a883a9f4be7a6447dab0685fb6485"

async def test_client():
    client = TelegramClient("test_session", API_ID, API_HASH)
    await client.start()
    print("Client started successfully!")
    await client.disconnect()

# Run the test script
import asyncio
asyncio.run(test_client())