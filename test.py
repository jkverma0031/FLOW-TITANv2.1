from titan.kernel.kernel import Kernel

def main():
    print("Booting TITAN Kernel…")

    kernel = Kernel()
    print("\n[OK] Kernel object created")

    print("\nStarting services via startup sequence…")
    kernel.start()

    print("\n[OK] Startup complete")
    print("Registered Services:", list(kernel.app.list_services().keys()))

    print("\nRunning diagnostics…")
    diag = kernel.app.get("diagnostics")
    print(diag.system_health())

    print("\nShutting down TITAN Kernel…")
    kernel.shutdown()
    print("[OK] Shutdown complete")


if __name__ == "__main__":
    main()
