from .core import ActionExecutor, CheckoutVerificationMiddleware

def get_executor(api):
    return ActionExecutor(
        api=api,
        middleware=[
            CheckoutVerificationMiddleware()
        ]
    )

