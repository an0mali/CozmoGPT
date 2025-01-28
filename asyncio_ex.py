import asyncio

async def async_function():
    print("Async function started")
    await asyncio.sleep(5)  # Simulate a long-running task
    print("Async function completed")

async def main():
    # Start the async function as a background task
    task = asyncio.create_task(async_function())

    # Continue executing other code
    for i in range(5):
        print(f"Main function is running iteration {i}")
        await asyncio.sleep(1)  # Simulate some work being done

    # Wait for the async function to complete
    await task

# Run the event loop
asyncio.run(main())
