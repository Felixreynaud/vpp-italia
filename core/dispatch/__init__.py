"""core.dispatch package — day-ahead price arbitrage dispatch engine.

Re-exports DispatchExecutor for backward compatibility with existing imports.
"""

from core.dispatch.executor import BatteryCommand, CommandResult, DispatchExecutor

__all__ = ["DispatchExecutor", "BatteryCommand", "CommandResult"]
