import asyncio
import json
from titan.kernel.app_context import AppContext
from titan.kernel.startup import perform_kernel_startup

print("🚀 TITAN SMOKE TEST STARTING...\n")

app = AppContext()

print("🔧 Performing kernel startup...\n")
perform_kernel_startup(app)

print("✅ Startup completed. Retrieving orchestrator...\n")

orch = app.get("orchestrator")
if orch is None:
    raise RuntimeError("❌ Orchestrator missing after startup")

print("🔍 Orchestrator loaded:", orch)

# ------------------------------------------------------
# ASYNC SMOKE TEST: EXECUTE A FAKE PLAN USING WORKERPOOL
# ------------------------------------------------------
async def run_test():
    print("\n⚙️  Running orchestrator smoke execution...")

    fake_plan = {
        "nodes": [
            {
                "id": "n1",
                "type": "plugin",
                "plugin": "filesystem",
                "action": "write_file",
                "args": {"path": "hello.txt", "text": "Hello Titan!"}
            },
            {
                "id": "n2",
                "type": "plugin",
                "plugin": "filesystem",
                "action": "read_file",
                "args": {"path": "hello.txt"}
            }
        ]
    }

    try:
        # FIX: Remove "actor"
        result = await orch.execute_plan(fake_plan)
        print("\n🎉 RESULT:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print("❌ Execution failed:", e)

asyncio.run(run_test())

print("\n🏁 SMOKE TEST FINISHED.\n")
