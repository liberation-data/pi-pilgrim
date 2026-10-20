class PilgrimError(Exception):
    """
    A component can't be assembled, or an assembly is used wrongly: for example, a circular dependency in
    initializers, a missing binding, or an instance that its lifecycle can't hold.
    """
