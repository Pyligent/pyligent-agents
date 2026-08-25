"""Scripted behaviour for the Level 3 example."""

from __future__ import annotations

from pyligent_agents.testing import ScriptedCall, ScriptedTurn, turn


def drafting_policy(call: ScriptedCall) -> ScriptedTurn:
    if "compacting an agent transcript" in call.system:
        return turn("Earlier: read ticket T-9001, loaded order A-1207, quoted £257.99.")
    return ScriptedTurn(
        text=("Thanks for letting us know, and I'm sorry your order arrived late. "
              "Order A-1207 was promised for the 6th and delivered on the 14th, so "
              "the delay is ours and we're refunding the shipping as well as the "
              "goods: £257.99 in total. That will go back to your original payment "
              "method within five working days. There's nothing further you need "
              "to do, and you're welcome to keep or return the items at our cost."),
        input_tokens=900, output_tokens=150)
