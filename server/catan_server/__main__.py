"""Run the built application directly, without a Node/npm parent process."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "catan_server.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        workers=1,
    )


if __name__ == "__main__":
    main()
