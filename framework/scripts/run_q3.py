from __future__ import annotations

import argparse

from mathmodel2026b.client import HttpSimulatorClient
from mathmodel2026b.state import DogState
from mathmodel2026b.strategy import Q3BaselineStrategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("robot_id")
    parser.add_argument("--url", default="http://127.0.0.1:2026")
    args = parser.parse_args()

    client = HttpSimulatorClient(robot_id=args.robot_id, base_url=args.url)
    state = DogState()
    strategy = Q3BaselineStrategy()

    client.enter()
    try:
        strategy.run(client, state)
    finally:
        client.exit()


if __name__ == "__main__":
    main()
