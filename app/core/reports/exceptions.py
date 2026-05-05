class ReportGenerationError(Exception):
    pass


class InvalidScopeError(ReportGenerationError):
    pass


class EmptyScopeError(ReportGenerationError):
    pass
