"""Interactive and automated live testing script for the Argentura AI Bot."""

import asyncio
import sys
from app.agent_service import AgentService
from app.argentura.client import ArgenturaClient
from app.config import get_settings
from app.sessions import MemorySessionStore


async def run_test(custom_message: str | None = None):
    settings = get_settings()
    print("=" * 70)
    print("ARGENTURA AI CHATBOT LIVE TEST")
    print("=" * 70)

    # 1. Connect and login to live Argentura platform
    print("\n1. Connecting to live platform (https://www.management-suite.com)...")
    client = ArgenturaClient(
        base_url="https://www.management-suite.com",
        username="Test",
        pin="258036",
    )
    ok = await client.login()
    if not ok:
        print("❌ Login failed. Check credentials in app/config.py.")
        return

    print(f"✅ Logged in successfully! Agency ID: {client.agency_id}")

    # 2. Fetch active lady profile
    print("\n2. Fetching lady profile from live database...")
    ladies = await client.get_ladies()
    if not ladies:
        print("❌ No ladies found in agency.")
        return

    julia = next((l for l in ladies if "Julia" in l.name), ladies[0])
    print(f"✅ Loaded live profile: {julia.name} (ID: {julia.id})")
    print(f"   Studio Address: {julia.studio_address}")
    if julia.structuredPrices:
        print(f"   Prices: 1h = €{julia.structuredPrices.p60}, 2h = €{julia.structuredPrices.p120}")

    # 3. Initialize Agent with LangChain tools & stealth persona
    store = MemorySessionStore()
    agent = AgentService(
        settings=settings,
        store=store,
        lady=julia,
        client=client,
    )

    # 4. Process customer message
    test_msg = custom_message or (
        "Hi Julia! Are you available tomorrow (2026-10-15) at 19:00 for 1 hour at your place? "
        "My name is Peter and my number is +491701234567. Please book it for me!"
    )
    session_id = "local-test-session-peter"

    print("\n3. Simulating incoming customer message:")
    print(f"   [Customer Peter]: \"{test_msg}\"")

    print("\n4. Running AI Agent (Stealth Persona + Live Calendar Tools)...")
    reply = await agent.reply(session_id=session_id, text=test_msg)

    print("\n" + "-" * 70)
    print(f"   [Julia's Reply]:\n   \"{reply}\"")
    print("-" * 70)

    # 5. Query live calendar to verify appointment was created
    print("\n5. Verifying appointments in live Argentura database for 2026-10-15:")
    appts = await client.get_appointments(lady_id=julia.id, date="2026-10-15")
    print(f"   Found {len(appts)} appointment(s) in calendar:")
    for a in appts:
        print(f"   • {a.startTime}–{a.endTime} | {a.customerName} ({a.customerPhone}) | Status: {a.status} | Price: €{a.price}")

    await client.close()
    print("\n" + "=" * 70)
    print("TEST COMPLETE! Check the calendar at https://www.management-suite.com/argentura/")
    print("=" * 70)


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_test(msg))
