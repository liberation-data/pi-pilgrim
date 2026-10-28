class PilgrimError(Exception):
    """
    A component can't be assembled, or an assembly is used wrongly: for example, a circular dependency in
    initializers, a missing binding, or an instance that its lifecycle can't hold.
    """


class AsyncTeardownRequired(PilgrimError):
    """
    A sync close can't close an assembly, since a teardown in it is async: close it with `await aclose()`, or leave
    `async with`. Raised before anything is closed, except for a teardown found to be async only once it is called,
    which returned an awaitable.
    """
