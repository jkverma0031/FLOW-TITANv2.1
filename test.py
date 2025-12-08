# tests/smoke_autonomy.py
import asyncio
import time
import logging

from titan.kernel.app_context import AppContext
from titan.kernel.startup import perform_kernel_startup

logging.basicConfig(level=logging.INFO)


async def main():
    print("🚀 TITAN SMOKE TEST STARTING...\n")

    # -------------------------------------------------------
    # 1. Create app context + initialize kernel subsystems
    # -------------------------------------------------------
    app = AppContext()

    print("🔧 Performing kernel startup...")
    perform_kernel_startup(app, cfg={
        "allow_autonomy": True,   # IMPORTANT for AutonomyEngine DO actions
    })

    # -------------------------------------------------------
    # 2. Retrieve core services
    # -------------------------------------------------------
    event_bus = app.get("event_bus")
    autonomy = app.get("autonomy_engine")
    perception = app.get("perception_manager")  # optional
    orchestrator = app.get("orchestrator")

    print("📌 Services discovered:")
    for name, info in app.dump().items():
        print(f"  - {name}: {info}")

    # -------------------------------------------------------
    # 3. Start Perception + Autonomy
    # -------------------------------------------------------
    print("\n🚦 Starting AutonomyEngine...")
    await autonomy.start()

    if perception:
        print("🔊 Starting PerceptionManager...")
        await perception.start()
    else:
        print("⚠️ PerceptionManager not available (this is OK for smoke test)")

    # -------------------------------------------------------
    # 4. Subscribe to autonomy + planning events for debugging
    # -------------------------------------------------------
    def event_logger(payload):
        print(f"\n📨 EVENT RECEIVED ({payload.get('type')}):\n{payload}\n")

    # subscribe to all autonomy events
    try:
        event_bus.subscribe("autonomy.*", event_logger)
    except Exception:
        # fallback: subscribe to some known autonomy events
        for et in ["autonomy.ask_user_confirmation", "autonomy.debug", "autonomy.event"]:
            try:
                event_bus.subscribe(et, event_logger)
            except Exception:
                pass

    # -------------------------------------------------------
    # 5. Send a fake speech transcript to simulate voice input
    # -------------------------------------------------------
    print("\n🎤 Sending fake transcript event into EventBus...\n")

    fake_event = {
        "sensor": "microphone",
        "type": "transcript",
        "text": "Titan, please summarize this webpage for me.",
        "user_id": "test_user",
        "trust_level": "high",
        "ts": time.time(),
    }

    event_bus.publish("perception.transcript", fake_event, block=False)

    # -------------------------------------------------------
    # 6. Allow time for the entire pipeline to run
    # -------------------------------------------------------
    print("⏳ Waiting for autonomy pipeline (intent → plan → execution)...\n")
    await asyncio.sleep(8)

    # -------------------------------------------------------
    # 7. Graceful shutdown
    # -------------------------------------------------------
    print("\n🛑 Stopping AutonomyEngine...")
    await autonomy.stop()

    if perception:
        print("🛑 Stopping PerceptionManager...")
        await perception.stop()

    print("\n✅ Smoke test completed successfully.\n")


if __name__ == "__main__":
    asyncio.run(main())
