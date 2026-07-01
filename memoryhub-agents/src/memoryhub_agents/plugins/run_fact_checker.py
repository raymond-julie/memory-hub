"""Entry point for the Fact Checker agent."""

import asyncio
import logging

from memoryhub_agents.config import AgentConfig
from memoryhub_agents.lifecycle import AgentRunner
from memoryhub_agents.plugins.fact_checker import FactCheckerPlugin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def main():
    config = AgentConfig()
    plugin = FactCheckerPlugin()
    runner = AgentRunner(config, plugin)
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
