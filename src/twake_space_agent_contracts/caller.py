from typing import Annotated

from fastapi import Depends, Header

from twake_space_agent_contracts.problems import Problem


def _caller(
    x_twake_user: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> str:
    """The user the agent acts for, as APISIX names them in a header only it may set.

    Left out of the OpenAPI document: an agent must never get to choose this user.
    """
    if not x_twake_user:
        raise Problem(
            status=401,
            code="missing_user",
            title="Missing user",
            detail="The X-Twake-User header must name the user the agent acts for.",
        )
    return x_twake_user


Caller = Annotated[str, Depends(_caller)]
