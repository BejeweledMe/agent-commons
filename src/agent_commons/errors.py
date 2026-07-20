class CommonsError(Exception):
    """Base error for Agent Commons."""


class ConfigurationError(CommonsError):
    pass


class ValidationError(CommonsError):
    pass


class IntegrityError(CommonsError):
    pass


class ImmutableCollisionError(IntegrityError):
    pass


class IdempotencyConflictError(IntegrityError):
    pass


class LifecycleConflictError(IntegrityError):
    pass


class ClaimConflictError(CommonsError):
    pass


class SecurityPolicyError(CommonsError):
    pass
